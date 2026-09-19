#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4", "markdownify"]
# ///
"""The deterministic half of a HubSpot section harvest: which pages, which
directories, what each page says, and the report.

One download ticket captures EVERY page of the section. The host no longer
fans pages out or filters them by scope, so this script does what the ticket
asks of the unit itself: it applies `harvest.scope`, `harvest.exclude_urls`,
`min_date` and `known[]` to the enumerated URLs, names one capture directory
per page, renders each rendered `page.html` to the `page.md` + `capture.json`
the generic extractor reads, and writes `report.json` last.

Everything it needs comes from `ticket.json` in the ticket's capture
directory (the spawner wrote it). With no `ticket.json` — `whereami` says
`spawn: none` and the foreman read the facts off `pipeline queue show` — pass
them to `plan` as flags; `page` and `report` then read them back off
`plan.json`.

  plan   <capture-dir> --urls <file> [--urls <file> ...] [--limit N]
         Reads the enumeration (a saved sitemap.xml, a JSON list of urls or
         of {url, lastmod} objects, or one url per line), normalizes each url
         (fragment and `hsLang` stripped, plus the site's own `strip_params`),
         filters, orders newest-first, and writes `<capture-dir>/plan.json`:
           {"leaves": [{"item", "dir", "lastmod"}], "skipped": [{"url", "why"}]}
         The ticket's own item keeps the ticket's own `capture_dir`; every
         other page gets `_raw/<slug>/<page-slug>--<hash8>`. The host has no
         verb for that name, so it is composed here in the host's own shape
         (`pipeline/jobs.py::capture_dir_for`): the url's path slugified, then
         the first 8 hex of sha1(item url).

  page   <capture-dir> <leaf-dir> [--published YYYY-MM-DD]
         [--external-url URL] [--media-file <path> | --no-media-leaf]
         Converts `<leaf-dir>/page.html` with this unit's `to_markdown.py` and
         the site's selectors (`references/sites.json`, keyed by host), puts a
         facts block and the video reference on top, and writes `page.md` and
         `capture.json` (with the `frontmatter` object) into the leaf.
         Where the video was downloaded — `<leaf-dir>/assets.json` marks the
         stable Mux master `downloaded`, or `--media-file` names the file — it
         also writes the page's MEDIA leaf: a sibling capture directory whose
         body is that file, which is the only thing the extractor turns into a
         queued transcription.

  report <capture-dir> [--missing <why> <url> ...] [--reason TEXT] [--failed]
         Reads `plan.json`, lists every leaf that really holds a capture in
         `captured[]`, and writes `<capture-dir>/report.json`. Run it LAST.
         First it settles the titles: the extractor files a page under its
         TITLE and overwrites what is there, so a later page whose title makes
         the filename an earlier one made is retitled `<title> (<the url path
         segment that tells them apart>)` in its own `capture.json`, its media
         leaf following it (see "page names" below).

Usage:
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py plan <capture_dir> --urls <capture_dir>/sitemap.xml
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py page <capture_dir> <leaf_dir>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py report <capture_dir>

(The leading `ops/` is the run verb's frozen argument grammar, resolved by
the front door to wherever this wiki's machinery tree lives. `run` starts the
script at the wiki root, so the ticket's wiki-relative `capture_dir` and a
`plan.json` leaf's `dir` are the paths to pass.)

Stdlib only in this file; the declared dependencies are `to_markdown.py`'s,
which `page` runs as a child of the same interpreter. Nothing is imported
from the plugin.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
SITES = HERE.parent / "references" / "sites.json"
CONVERTER = HERE / "to_markdown.py"

TICKET_NAME = "ticket.json"
PLAN_NAME = "plan.json"
CAPTURE_NAME = "capture.json"
REPORT_NAME = "report.json"
BODY_NAME = "page.md"
RAW = "_raw"

# Always stripped: HubSpot appends `?hsLang=<lang>` to internal nav links, so
# one page is otherwise two urls. A site adds its own under `strip_params`.
STRIP_PARAMS = ("hsLang",)

# The suffixes the extractor treats as media (`pipeline/text.py::MEDIA`): a
# body with one of these becomes an empty page flagged for transcription. A
# media leaf whose file has any other suffix would be read as "unsupported".
MEDIA_SUFFIXES = frozenset({".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".mp4", ".mov", ".mkv", ".webm", ".m4v"})

# Keys another verb owns on a page. Never offered in `frontmatter`.
HOST_KEYS = frozenset({"status", "document_id", "document_revision", "harvested", "extracted", "title", "resource"})

WHY = ("denied", "timeout", "auth", "error")

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_PLACEHOLDER = re.compile(r"<!-- media:(video|audio|embed):(\d+) -->")
_H1 = re.compile(r"\A# +(.+?)\s*\n+")


class Problem(Exception):
    """Something the caller got wrong; said on stderr, exit 2."""


# ------------------------------------------------------------ pure: urls


def normalize_url(url: str, strip_params=()) -> str:
    """One spelling per page: no fragment, no `hsLang` (or the site's own
    params), lowercase scheme and host, no trailing slash but the root's."""
    parts = urlsplit(url.strip())
    drop = {name.lower() for name in (*STRIP_PARAMS, *strip_params)}
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in drop])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _bare(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def in_scope(url: str, target: str, scope: str) -> bool:
    """`page`: the target alone. `section`: the target's host, at or under the
    target's path. `domain`: the target's host. `www.` is not a second host."""
    u, t = urlsplit(url), urlsplit(target)
    if _bare(u.netloc) != _bare(t.netloc):
        return False
    if scope == "domain":
        return True
    root = t.path.rstrip("/")
    path = u.path.rstrip("/")
    if scope == "section":
        return path == root or path.startswith(root + "/")
    return path == root and u.query == t.query


def slugify(parts, max_len: int = 60, *, fallback: str = "item") -> str:
    folded = _NON_SLUG.sub("-", "-".join(str(part) for part in parts).lower()).strip("-")
    return folded[:max_len] or fallback


def leaf_name(item: str) -> str:
    """`<page-slug>--<hash8>`, the shape the host's own namer makes."""
    parts = urlsplit(item)
    bits = [bit for bit in parts.path.split("/") if bit] or [parts.netloc.lower()]
    return f"{slugify(bits)}--{hashlib.sha1(item.encode('utf-8')).hexdigest()[:8]}"


def leaf_dir(slug: str, item: str) -> str:
    return f"{RAW}/{slug}/{leaf_name(item)}"


def media_leaf_dir(page_dir: str, media_item: str) -> str:
    """The media leaf beside a page's leaf: `<page-slug>-video--<hash8>`."""
    parent, _, name = page_dir.rpartition("/")
    stem = name.rsplit("--", 1)[0]
    return f"{parent}/{slugify([stem, 'video'])}--{hashlib.sha1(media_item.encode('utf-8')).hexdigest()[:8]}"


def _day(value) -> str | None:
    return value[:10] if isinstance(value, str) and _DATE.match(value) else None


def parse_urls(text: str) -> tuple[list[dict], list[str]]:
    """`([{url, lastmod}], [child sitemap urls])` from a sitemap, a JSON list
    or plain lines. A sitemap INDEX yields no pages, only the children to fetch."""
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<"):
        root = ET.fromstring(stripped)
        local = lambda tag: str(tag).rsplit("}", 1)[-1]  # noqa: E731
        pages, children = [], []
        for node in root:
            fields = {local(child.tag): (child.text or "").strip() for child in node}
            if not fields.get("loc"):
                continue
            if local(node.tag) == "sitemap":
                children.append(fields["loc"])
            elif local(node.tag) == "url":
                pages.append({"url": fields["loc"], "lastmod": _day(fields.get("lastmod"))})
        return pages, children
    if stripped.startswith("["):
        pages = []
        for entry in json.loads(stripped):
            if isinstance(entry, str):
                pages.append({"url": entry, "lastmod": None})
            elif isinstance(entry, dict) and isinstance(entry.get("url"), str):
                pages.append({"url": entry["url"], "lastmod": _day(entry.get("lastmod"))})
        return pages, []
    return [{"url": line.strip(), "lastmod": None} for line in stripped.splitlines() if line.strip()], []


def plan_leaves(pages: list[dict], *, slug: str, target: str, item: str | None, capture_dir: str | None,
                scope: str = "section", exclude_urls=(), min_date: str | None = None, known=(),
                strip_params=(), limit: int | None = None) -> dict:
    """The filter, as data in and data out. Order of the tests is the order a
    reader would ask them: is it a page of this job, was it excluded, is it too
    old, do we already hold it."""
    target_n = normalize_url(target, strip_params)
    item_n = normalize_url(item, strip_params) if item else target_n
    held = {normalize_url(row["resource"], strip_params) for row in known if isinstance(row, dict) and isinstance(row.get("resource"), str)}
    if scope == "page" and not any(normalize_url(p["url"], strip_params) == target_n for p in pages):
        pages = [*pages, {"url": target, "lastmod": None}]
    seen, kept, skipped = set(), [], []
    for page in pages:
        url = normalize_url(page["url"], strip_params)
        if url in seen:
            continue
        seen.add(url)
        if not url.startswith(("http://", "https://")) or not in_scope(url, target_n, scope):
            skipped.append({"url": url, "why": "scope"})
        elif any(fnmatch.fnmatchcase(url, pattern) for pattern in exclude_urls):
            skipped.append({"url": url, "why": "excluded"})
        elif min_date and page.get("lastmod") and page["lastmod"] < min_date:
            skipped.append({"url": url, "why": "older_than_min_date"})
        elif url in held:
            skipped.append({"url": url, "why": "known"})
        else:
            directory = capture_dir if (capture_dir and url == item_n) else leaf_dir(slug, url)
            kept.append({"item": url, "dir": directory, "lastmod": page.get("lastmod")})
    # Newest first, undated last: a slice is killed at thirty minutes, and what
    # it did not reach is what the next run picks up through `known[]`.
    kept.sort(key=lambda leaf: leaf["lastmod"] or "", reverse=True)
    if limit is not None and len(kept) > limit:
        skipped += [{"url": leaf["item"], "why": "over_limit"} for leaf in kept[limit:]]
        kept = kept[:limit]
    return {"leaves": kept, "skipped": skipped}


# ------------------------------------------------------------ pure: sites


def site_rules(sites: dict, host: str) -> dict:
    """The rules for one host: its own entry, else the `www.`-less one, else `*`."""
    table = sites.get("sites") if isinstance(sites.get("sites"), dict) else {}
    host = host.lower()
    for key in (host, _bare(host), "www." + _bare(host), "*"):
        if isinstance(table.get(key), dict):
            return table[key]
    return {}


def load_sites(path: Path | None) -> dict:
    path = path or SITES
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise Problem(f"{path}: not JSON — {exc}") from None
    return loaded if isinstance(loaded, dict) else {}


# ------------------------------------------------------------ pure: the page


def split_title(md: str) -> tuple[str | None, str]:
    """A leading `# Title` off the converter's output. The extractor writes the
    title into the page's frontmatter, so the body does not repeat it."""
    found = _H1.match(md)
    return (found.group(1).strip(), md[found.end():]) if found else (None, md)


def video_block(meta: dict) -> str:
    """How this unit references its video: the venue's live iframe — verbatim,
    params and all, because the bare player url refuses to play outside its
    page — and a plain link under it, which is what is left when the job says
    `process.embeds: false` and the extractor takes every iframe out."""
    lines = []
    if meta.get("embed_url"):
        lines.append(f'<iframe src="{meta["embed_url"]}" width="640" height="360" allowfullscreen></iframe>')
    link = meta.get("stream_url") or meta.get("player_url")
    if link:
        lines.append(f"[Video: {link}]({link})")
    return "\n\n".join(lines)


def place_video(body: str, meta: dict) -> str:
    """The video where the player stood; on top where the content root held none."""
    block = video_block(meta)
    if not block:
        return body
    placed = []

    def swap(match):
        if match.group(1) == "embed" and not placed:
            placed.append(True)
            return block
        return match.group(0)

    body = _PLACEHOLDER.sub(swap, body)
    return body if placed else f"{block}\n\n{body}"


def facts_of(item: str, meta: dict, *, published: str | None, external_url: str | None) -> dict:
    """The unit's exact facts: scalars only, none of the host's own keys."""
    facts = {
        "type": "video" if meta.get("stream_url") else "page",
        "venue": "hubspot-cms",
        "published": published,
        "mux_playback_id": meta.get("mux_playback_id"),
        "video_url": meta.get("stream_url"),
        "player_url": meta.get("player_url"),
        "external_url": external_url,
        "canonical_url": meta.get("final_url") if meta.get("final_url") and meta.get("final_url") != item else None,
    }
    return {key: value for key, value in facts.items() if value not in (None, "") and key not in HOST_KEYS}


def facts_block(item: str, facts: dict) -> str:
    rows = [f"- source: <{item}>", *(f"- {key}: {value}" for key, value in facts.items())]
    return "\n".join(rows)


def render_body(md: str, item: str, meta: dict, facts: dict) -> str:
    body = place_video(md.strip(), meta)
    # The facts block opens the body, so the file can never open with `---`:
    # the extractor prepends its own frontmatter and a second block corrupts it.
    return f"{facts_block(item, facts)}\n\n{body}".rstrip() + "\n"


def capture_record(*, slug: str, item: str, title: str | None, body: str, content_type: str, facts: dict) -> dict:
    return {
        "slug": slug,
        "item": item,
        "title": title,
        "body": body,
        "content_type": content_type,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frontmatter": facts,
    }


# ------------------------------------------------------------ pure: the report


def build_report(*, ticket: str | None, planned: list[dict], captured: list[dict], skipped: list[dict],
                 missing: list[dict], reason: str | None = None, failed: bool = False) -> dict:
    """`ok` when every planned page landed, `partial` when some did, `skipped`
    when there was nothing new to get, `failed` otherwise."""
    pages = [leaf for leaf in planned if not leaf.get("media_of")]
    landed = {row["dir"] for row in captured}
    got = [leaf for leaf in pages if leaf["dir"] in landed]
    reasons = [reason] if reason else []
    if failed:
        outcome = "failed"
        reasons = reasons or ["failed"]
    elif pages and len(got) == len(pages):
        outcome = "ok"
    elif got:
        outcome = "partial"
        reasons.append(f"{len(got)} of {len(pages)} pages captured; the rest are picked up next run")
    elif pages:
        outcome = "failed"
        reasons = reasons or [f"none of {len(pages)} pages captured"]
    elif skipped:
        outcome = "skipped"
        counts: dict = {}
        for row in skipped:
            counts[row["why"]] = counts.get(row["why"], 0) + 1
        reasons.append("nothing new in scope — " + ", ".join(f"{why}: {n}" for why, n in sorted(counts.items())))
    else:
        outcome = "failed"
        reasons = reasons or ["no pages enumerated"]
    if outcome == "ok" and missing:
        outcome = "partial"
        reasons.append(f"{len(missing)} url(s) could not be reached")
    return {
        "v": 1,
        "ticket": ticket,
        "outcome": outcome,
        "reason": "; ".join(reasons) or None,
        "captured": captured if outcome != "failed" else [],
        "written": [],
        "missing": missing,
        "discovered": [],
    }


# ------------------------------------------------------------ the filesystem


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")


def wiki_root(capture: Path, rel: str) -> Path:
    """The wiki root, from a capture directory and its wiki-relative name."""
    parts = Path(rel).parts
    if len(parts) != 3 or parts[0] != RAW or capture.parts[-3:] != parts:
        raise Problem(f"{capture} is not the capture directory `{rel}` names")
    return capture.parents[2]


def job_facts(capture: Path, args=None) -> dict:
    """The job, off `ticket.json`; off `plan.json` where no spawner wrote one;
    off `plan`'s own flags the first time."""
    ticket = _read_json(capture / TICKET_NAME)
    if isinstance(ticket, dict):
        harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
        facts = {
            "ticket": ticket.get("ticket"), "slug": ticket.get("slug"), "item": ticket.get("item"),
            "target": ticket.get("target") or ticket.get("item"), "capture_dir": ticket.get("capture_dir"),
            "scope": harvest.get("scope") or "section", "exclude_urls": harvest.get("exclude_urls") or [],
            "min_date": ticket.get("min_date"), "known": ticket.get("known") or [],
        }
    else:
        plan = _read_json(capture / PLAN_NAME)
        facts = dict(plan["job"]) if isinstance(plan, dict) and isinstance(plan.get("job"), dict) else {
            "ticket": None, "slug": None, "item": None, "target": None, "capture_dir": None,
            "scope": "section", "exclude_urls": [], "min_date": None, "known": [],
        }
    for key in ("ticket", "slug", "target", "scope", "min_date"):  # explicit flags win: a hand run
        if args is not None and getattr(args, key, None):
            facts[key] = getattr(args, key)
    if args is not None and getattr(args, "exclude_url", None):
        facts["exclude_urls"] = [*facts["exclude_urls"], *args.exclude_url]
    if not facts.get("slug") or not facts.get("target"):
        raise Problem(f"no {TICKET_NAME} in {capture}: pass --slug and --target (read them off `pipeline queue show`)")
    facts["item"] = facts.get("item") or facts["target"]
    facts["capture_dir"] = facts.get("capture_dir") or f"{RAW}/{facts['slug']}/{capture.name}"
    return facts


def cmd_plan(args) -> int:
    capture = Path(args.capture_dir).resolve()
    job = job_facts(capture, args)
    wiki_root(capture, job["capture_dir"])
    rules = site_rules(load_sites(args.sites), urlsplit(job["target"]).netloc)
    pages, children = [], []
    for name in args.urls or []:
        text = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8", errors="replace")
        found, more = parse_urls(text)
        pages += found
        children += more
    plan = plan_leaves(
        pages, slug=job["slug"], target=job["target"], item=job["item"], capture_dir=job["capture_dir"],
        scope=job["scope"], exclude_urls=[*job["exclude_urls"], *(rules.get("exclude_urls") or [])],
        min_date=job["min_date"], known=job["known"], strip_params=rules.get("strip_params") or (), limit=args.limit,
    )
    plan = {"v": 1, "job": {**job, "known": []}, "sitemaps": children, **plan}
    _write_json(capture / PLAN_NAME, plan)
    print(json.dumps({"leaves": plan["leaves"], "skipped": len(plan["skipped"]), "sitemaps": children}, indent=1))
    return 0


def _leaf_of(plan: dict, root: Path, leaf: Path) -> dict | None:
    return next((row for row in plan.get("leaves") or [] if (root / row["dir"]).resolve() == leaf), None)


def convert(html: Path, url: str, rules: dict) -> str:
    """`page.html` → markdown, through this unit's own converter and the site's selectors."""
    argv = [sys.executable, str(CONVERTER), str(html), "--out", "-", "--base-url", url]
    if rules.get("content_selector"):
        argv += ["--selector", rules["content_selector"]]
    for selector in rules.get("drop_selectors") or []:
        argv += ["--drop-selector", selector]
    if rules.get("title_selector"):
        argv += ["--title-selector", rules["title_selector"]]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    sys.stderr.write(done.stderr)
    if done.returncode != 0:
        raise Problem(f"to_markdown.py exited {done.returncode} on {html}")
    return done.stdout


def downloaded_media(leaf: Path, meta: dict) -> Path | None:
    """The page's video as the plugin's `assets.py download` left it: the asset
    `patch-assets` appended (its `src_url` is the stable Mux master), marked
    `downloaded`, its `local_path` relative to the leaf. None where the job
    downloads nothing, or the download failed."""
    assets = _read_json(leaf / "assets.json")
    if not isinstance(assets, list) or not meta.get("stream_url"):
        return None
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("src_url") != meta["stream_url"]:
            continue
        if asset.get("status") != "downloaded" or not isinstance(asset.get("local_path"), str):
            continue
        found = (leaf / asset["local_path"]).resolve()
        if found.is_file() and found.suffix.lower() in MEDIA_SUFFIXES:
            return found
    return None


def _place(source: Path, dest: Path) -> None:
    """A hard link where the filesystem allows one — a lesson video is hundreds
    of megabytes and the store already holds it — else a copy."""
    if dest.exists():
        dest.unlink()
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def cmd_page(args) -> int:
    capture, leaf = Path(args.capture_dir).resolve(), Path(args.leaf_dir).resolve()
    job = job_facts(capture)
    root = wiki_root(capture, job["capture_dir"])
    plan = _read_json(capture / PLAN_NAME) or {}
    row = _leaf_of(plan, root, leaf)
    item = args.url or (row or {}).get("item") or (job["item"] if leaf == capture else None)
    if not item:
        raise Problem(f"{leaf} is not a leaf {PLAN_NAME} names; run `plan` first, or pass --url")
    html = leaf / "page.html"
    if not html.is_file():
        raise Problem(f"{html} is not there — render the page first")
    meta = _read_json(leaf / "meta.json")
    meta = meta if isinstance(meta, dict) else {}
    if args.no_media_leaf:
        source = None
    elif args.media_file:
        source = Path(args.media_file).resolve()
    else:
        source = downloaded_media(leaf, meta)
    if source is not None and (not source.is_file() or source.suffix.lower() not in MEDIA_SUFFIXES):
        # Before anything is written: a leaf half-made is a leaf `report` would list.
        raise Problem(f"{source} is not a media file the extractor queues ({', '.join(sorted(MEDIA_SUFFIXES))})")
    rules = site_rules(load_sites(args.sites), urlsplit(item).netloc)
    found_title, md = split_title(convert(html, item, rules))
    # The site's own title rule outranks the render's guess; with no rule, the
    # render's (rendered h2, then <title>) outranks a bare <title>.
    title = (found_title if rules.get("title_selector") else None) or meta.get("title") or found_title
    facts = facts_of(item, meta, published=args.published, external_url=args.external_url)
    (leaf / BODY_NAME).write_text(render_body(md, item, meta, facts), encoding="utf-8")
    record = capture_record(slug=job["slug"], item=item, title=title, body=BODY_NAME, content_type="text/markdown", facts=facts)
    _write_json(leaf / CAPTURE_NAME, record)
    out = [{"item": item, "dir": str(leaf.relative_to(root)), "title": title}]

    if source is not None:
        media_item = meta.get("stream_url") or f"{item}#video"
        rel = media_leaf_dir(str(leaf.relative_to(root)), media_item)
        media_leaf = root / rel
        media_leaf.mkdir(parents=True, exist_ok=True)
        body = f"media{source.suffix.lower()}"
        _place(source, media_leaf / body)
        media_title = f"{title or leaf.name} (video)"
        _write_json(media_leaf / CAPTURE_NAME, capture_record(
            slug=job["slug"], item=media_item, title=media_title, body=body,
            content_type=mimetypes.guess_type(body)[0] or "application/octet-stream",
            facts={**facts, "page_url": item},
        ))
        leaves = [one for one in plan.get("leaves") or [] if one.get("dir") != rel]
        leaves.append({"item": media_item, "dir": rel, "lastmod": None, "media_of": item})
        if plan:
            plan["leaves"] = leaves
            _write_json(capture / PLAN_NAME, plan)
        out.append({"item": media_item, "dir": rel, "title": media_title})
    print(json.dumps(out, indent=1))
    return 0


def held(root: Path, leaf: dict) -> dict | None:
    """The `captured[]` row for a leaf that really holds a capture, else None."""
    directory = root / leaf["dir"]
    record = _read_json(directory / CAPTURE_NAME)
    if not isinstance(record, dict) or not isinstance(record.get("body"), str):
        return None
    if not (directory / record["body"]).is_file():
        return None
    title = record.get("title")
    return {"item": leaf["item"], "dir": leaf["dir"], "title": title if isinstance(title, str) else None}


# ------------------------------------------------------------ page names
#
# KEEP IN SYNC with the host. `pipeline extract` names a page FILE from the
# capture's title and writes it with no existence check (llm-wiki-ops
# `commands/pipeline/extract.py::_capture_to_page` -> `pipeline/pages.py::
# name_for` -> `page/note.py::filename_for`): the filename is
# `title.strip() + ".md"` — nothing folded, nothing dropped; a title carrying
# one of `ILLEGAL` or a control character is REFUSED, not altered — and a
# capture with no title is filed under its body's stem (`page`). So two leaves
# of one run whose titles differ only in outer whitespace are ONE page, the
# second overwriting the first; on a filesystem that folds case (macOS,
# Windows) so are two that differ only in that. `page_key` folds both: a
# needless qualifier costs nothing, an overwritten page is lost. NOT folded:
# Unicode form (NFC/NFD), which the same filesystems also fold — stdlib has it
# only in `unicodedata`, and one venue spelling one title two ways is rare.
# Duplicated per unit on purpose — units install one by one, nothing is shared.

TITLE_ILLEGAL = '/\\:*?"<>|'  # `page/note.py::ILLEGAL`
QUALIFIER_MAX = 60


def page_key(title: str) -> str:
    """What two titles share when they make one page file."""
    return title.strip().casefold()


def qualifier(text) -> str:
    """Venue text made safe inside a title: one line, capped, and none of the
    characters the host refuses a title for."""
    if not isinstance(text, str):
        return ""
    safe = "".join("-" if (char in TITLE_ILLEGAL or ord(char) < 32) else char for char in text)
    return " ".join(safe.split())[:QUALIFIER_MAX].strip(" -.")


def unique_title(title: str, qualifiers, taken: dict) -> str:
    """`title`, untouched, when no leaf before this one makes its filename;
    else `title (<qualifier>)` with the first qualifier that tells it apart.

    `taken` maps a `page_key` to the qualifiers of the leaf holding it, and the
    answer is claimed in it. A qualifier the holder shares distinguishes
    nothing and is passed over; callers end the list with the leaf's hash8,
    which no other leaf has, and a counter closes it, so the answer is always
    free. A title this already qualified is free on the next pass and comes
    back as it is — re-running never renames a leaf a second time.
    """
    given = list(dict.fromkeys(q for q in map(qualifier, qualifiers) if q))
    chosen = title
    holder = taken.get(page_key(title))
    if holder is not None:
        shared = {page_key(q) for q in holder}
        options = [q for q in given if page_key(q) not in shared]
        base = title.strip()
        chosen = next((f"{base} ({q})" for q in options if page_key(f"{base} ({q})") not in taken), None)
        stem, n = (f"{base} ({options[-1]})" if options else base), 2
        while chosen is None:
            if page_key(f"{stem} ({n})") not in taken:
                chosen = f"{stem} ({n})"
            n += 1
    taken[page_key(chosen)] = given
    return chosen


def leaf_qualifiers(leaf: dict) -> list:
    """What tells this page from a namesake: its url's path segments, deepest
    first — `unique_title` passes over the ones the namesake shares, so what is
    left is the segment that tells them apart (`module-2` of
    `/learn/module-2/intro`) — then the hash of its item. A media leaf is
    qualified by its PAGE's url: a stream url says nothing a person can read."""
    path = urlsplit(leaf.get("media_of") or leaf["item"]).path
    bits = [unquote(bit) for bit in path.split("/") if bit]
    return [*reversed(bits), hashlib.sha1(leaf["item"].encode("utf-8")).hexdigest()[:8]]


def settle_titles(root: Path, planned: list[dict]) -> None:
    """One page per leaf: in plan order the first leaf to make a filename keeps
    its title, and a later one is retitled in its own `capture.json`.

    In `report` and not in `page`, because `page` sees one leaf, pages are
    rendered in whatever order the worker reaches them, and `report` runs
    last, over all of them, before anything is extracted. Pages first, then
    media leaves: a retitled page's media leaf becomes `<new title> (video)`,
    so the stub and the lesson still read as a pair. A title already qualified
    is free on the next pass, so a second report renames nothing twice.
    """
    taken: dict = {}
    retitled: dict = {}
    ordered = [leaf for leaf in planned if not leaf.get("media_of")] + [leaf for leaf in planned if leaf.get("media_of")]
    for leaf in ordered:
        directory = root / leaf["dir"]
        record = _read_json(directory / CAPTURE_NAME)
        if held(root, leaf) is None:
            continue
        title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
        on_disk = title or Path(record["body"]).stem
        wanted = f"{retitled[leaf['media_of']]} (video)" if leaf.get("media_of") in retitled else on_disk
        final = unique_title(wanted, leaf_qualifiers(leaf), taken)
        if final != on_disk:
            record["title"] = final
            _write_json(directory / CAPTURE_NAME, record)
            retitled[leaf["item"]] = final


def cmd_report(args) -> int:
    capture = Path(args.capture_dir).resolve()
    job = job_facts(capture)
    root = wiki_root(capture, job["capture_dir"])
    plan = _read_json(capture / PLAN_NAME) or {}
    planned = plan.get("leaves") or []
    settle_titles(root, planned)
    captured = [row for row in (held(root, leaf) for leaf in planned) if row]
    missing = []
    for why, url in args.missing or []:
        if why not in WHY:
            raise Problem(f"--missing {why}: why is one of {', '.join(WHY)}")
        missing.append({"host": urlsplit(url).netloc.lower(), "url": url, "why": why})
    report = build_report(ticket=job["ticket"], planned=planned, captured=captured, skipped=plan.get("skipped") or [],
                          missing=missing, reason=args.reason, failed=args.failed)
    _write_json(capture / REPORT_NAME, report)
    print(json.dumps({"outcome": report["outcome"], "reason": report["reason"], "captured": len(report["captured"])}))
    return 0 if report["outcome"] != "failed" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="filter the enumerated urls and name one capture directory per page")
    p.add_argument("capture_dir", help="the ticket's capture directory (where ticket.json is)")
    p.add_argument("--urls", action="append", help="sitemap.xml, a JSON list, or one url per line; `-` is stdin (repeatable)")
    p.add_argument("--limit", type=int, default=None, help="plan at most N pages this run, newest first")
    p.add_argument("--sites", type=Path, default=None, help="default: this unit's references/sites.json")
    for flag in ("--ticket", "--slug", "--target", "--min-date"):
        p.add_argument(flag, default=None, help="hand run with no ticket.json: the ticket's own value")
    p.add_argument("--scope", choices=["page", "section", "domain"], default=None, help="hand run: harvest.scope")
    p.add_argument("--exclude-url", action="append", default=[], help="hand run: one more harvest.exclude_urls glob")
    p.set_defaults(fn=cmd_plan)

    g = sub.add_parser("page", help="page.html -> page.md + capture.json in one leaf")
    g.add_argument("capture_dir")
    g.add_argument("leaf_dir")
    g.add_argument("--url", default=None, help="the page's url, when plan.json does not name this leaf")
    g.add_argument("--published", default=None, help="YYYY-MM-DD, only as the plugin's published_date.py printed it")
    g.add_argument("--external-url", default=None, help="a pointer page's payload: the link it exists to give")
    g.add_argument("--media-file", default=None, help="the downloaded video/audio, when <leaf-dir>/assets.json does not name it")
    g.add_argument("--no-media-leaf", action="store_true", help="write the page alone, even where the video was downloaded")
    g.add_argument("--sites", type=Path, default=None, help="default: this unit's references/sites.json")
    g.set_defaults(fn=cmd_page)

    r = sub.add_parser("report", help="write report.json — last")
    r.add_argument("capture_dir")
    r.add_argument("--missing", nargs=2, action="append", metavar=("WHY", "URL"), help="a url that could not be reached")
    r.add_argument("--reason", default=None)
    r.add_argument("--failed", action="store_true", help="nothing usable landed; say why with --reason")
    r.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Problem as exc:
        print(f"leaves.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
