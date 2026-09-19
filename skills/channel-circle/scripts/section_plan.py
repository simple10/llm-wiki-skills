#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Plan, record and report one Circle harvest ticket — the deterministic half.

platform: circle
scope: platform-general. No browser, no network, stdlib only: everything here
is a pure function of files already in the job's `_raw/<slug>/` slice.

One harvest ticket walks a whole section. `capture_lesson.py` does the
rendering; this script decides WHICH lessons, WHERE each one lands, writes the
record the extractor reads, and writes the one report that leaves the slice.

  section_plan.py plan   <capture_dir> [--target URL] [--slug SLUG]
                         [--scope page|section|domain] [--ticket ID]
  section_plan.py record <capture_dir> <lesson-url> [--author NAME]
  section_plan.py report <capture_dir> [--auth-expired] [--reason TEXT]
                         [--missing <host> <url> <denied|timeout|auth|error>]...
                         [--ticket ID]

`<capture_dir>` is the TICKET's capture directory — the one holding
`ticket.json` and the root capture (`meta.json`, `page.html`). Under
`llm-wiki-ops run` the working directory is the wiki root, so the
wiki-relative `capture_dir` off `ticket.json` is the path to pass.

plan    reads `ticket.json` (target, slug, `harvest.scope`, `harvest.access`,
        `harvest.exclude_urls`, `known[]`) and the root capture's `meta.json`
        (`discovered_lesson_links`, in the order the course lists them), and
        writes `plan.json`: the ordered leaf work list, each with the
        directory it is captured into, plus every link dropped and why.
        The flags stand in for `ticket.json` on a hand run.
record  after `capture_lesson.py` and `to_markdown.py` have filled one leaf:
        puts a compact facts block under the heading of its `page.md`, appends
        the leaf's `transcript.md` (the plugin's `format_transcript.py` over
        the lesson's caption track) under `## Transcript` when there is one,
        and writes its `capture.json`, carrying the same facts as
        `frontmatter`. Media the asset manifest (`assets.json`, beside the
        leaf's page) says was DOWNLOADED is named by its wiki-relative path;
        a signed stream url is never written down — it is dead within hours.
        Safe to re-run: neither block is stacked twice.
report  LAST: writes `report.json` in the ticket's capture dir. `captured[]`
        is derived, never claimed — a leaf counts when its `capture.json`
        names a body file that is there.

Scope is the ticket's own: `page` keeps only the target; `section` keeps the
lessons under the target's path (so the target must be the space root,
`/c/<slug>`); `domain` keeps every lesson on the target's host. Nothing
downstream filters a second time — what this plans is what gets fetched.

Leaf directories follow the host's own namer, which has no verb, so the same
shape is composed here: `_raw/<slug>/<slugified url path>--<first 8 hex of
sha1(url)>`. The target itself, where it is a leaf, lands in the ticket's own
`capture_dir`, which is never composed.

Exit status: `plan` and `record` exit 0 on success, 1 when an input is missing
or unusable (said on stderr). `report` always writes the report; it exits 0
for `ok`, `partial` and `skipped`, 1 for `failed`.

History:
  2026-09-19  created — the rebuilt pipeline fans nothing out: one ticket
              captures every leaf, and the unit applies scope itself.
"""

import argparse
import hashlib
import json
import posixpath
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PLAN_V = 1
REPORT_V = 1
CAPTURE_V = 1
TICKET_NAME = "ticket.json"
META_NAME = "meta.json"
PLAN_NAME = "plan.json"
CAPTURE_NAME = "capture.json"
REPORT_NAME = "report.json"
BODY_NAME = "page.md"
TRANSCRIPT_NAME = "transcript.md"
ASSETS_NAME = "assets.json"

RAW_DIRNAME = "_raw"
SCOPES = ("page", "section", "domain")
DEFAULT_SCOPE = "section"  # the unit manifest's `watch.defaults.harvest.scope`
WHYS = ("denied", "timeout", "auth", "error")

# Keys another verb owns: never in `frontmatter`.
HOST_OWNED = ("status", "document_id", "document_revision", "harvested", "extracted", "title", "resource")

LESSON_RE = re.compile(r"/lessons/([^/?#]+)")
SECTION_RE = re.compile(r"/sections/([^/?#]+)")
SPACE_RE = re.compile(r"^/c/([^/?#]+)")
POSITION_RE = re.compile(r"\bTopic\s+(\d+)\s+of\s+(\d+)\b", re.I)
DURATION_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")
HEADING_RE = re.compile(r"\A#\s+(.+?)\s*(?:\n|\Z)")
FACT_LINE_RE = re.compile(r"^- \*\*[^*]+\*\*: ")
FACTS_OPEN = "- **Type**: lesson"
LINK_TEXT_CAP = 80  # `capture_lesson.py` slices link text to this many chars

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def read_json(path) -> dict:
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def write_json(path, doc) -> None:
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- urls ---------------------------------------------------------------------


def clean_url(url: str) -> str | None:
    """An http(s) url with its fragment dropped, or None for anything else."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def same_key(url: str) -> str:
    """What two spellings of one page share: host lowercased, no trailing
    slash, no fragment. Used to compare, never to rewrite."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), (parts.netloc or "").lower(), parts.path.rstrip("/"), parts.query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def in_scope(url: str, target: str, scope: str) -> bool:
    if scope == "page":
        return same_key(url) == same_key(target)
    if host_of(url) != host_of(target):
        return False
    if scope == "domain":
        return True
    prefix = urlsplit(target).path.rstrip("/")
    path = urlsplit(url).path
    return path == prefix or path.startswith(prefix + "/")


def excluded(url: str, exclude_urls) -> bool:
    """An `exclude_urls` entry drops the url it names and everything under it."""
    key = same_key(url)
    for entry in exclude_urls or []:
        if not isinstance(entry, str) or not entry.strip():
            continue
        base = same_key(entry.strip())
        if key == base or key.startswith(base + "/"):
            return True
    return False


def known_keys(known) -> set:
    return {same_key(e["resource"]) for e in known or [] if isinstance(e, dict) and isinstance(e.get("resource"), str)}


# --- naming -------------------------------------------------------------------


def slugify(parts, max_len: int = 60, *, fallback: str = "item") -> str:
    folded = "-".join(str(part) for part in parts).lower()
    folded = _NON_SLUG.sub("-", folded).strip("-")
    return folded[:max_len] or fallback


def leaf_dir(slug: str, url: str) -> str:
    """`_raw/<slug>/<slugified path>--<hash8>` — the shape the host's own namer
    gives an item with an address."""
    hash8 = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    parts = urlsplit(url)
    bits = [bit for bit in parts.path.split("/") if bit] or [parts.netloc.lower()]
    return f"{RAW_DIRNAME}/{slug}/{slugify(bits)}--{hash8}"


# --- plan ---------------------------------------------------------------------


def link_facts(text: str) -> tuple[str | None, str | None]:
    """`(title, duration)` off a sidebar link's text — "Lesson title\\n\\n04:07".
    A text that filled the capture's cap may be cut mid-title, so it names no
    title."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    duration = next((line for line in lines if DURATION_RE.match(line)), None)
    titles = [line for line in lines if not DURATION_RE.match(line)]
    title = titles[0] if titles and len(text or "") < LINK_TEXT_CAP else None
    return title, duration


def is_lesson(url: str) -> bool:
    return bool(LESSON_RE.search(urlsplit(url).path))


def build_plan(ticket: dict, meta: dict, *, capture_rel: str) -> dict:
    """The ordered leaf work list for one ticket. Pure: same inputs, same plan."""
    target = clean_url(ticket.get("target") or ticket.get("item") or "")
    slug = ticket.get("slug")
    if not target:
        raise ValueError("no target: none in ticket.json and no --target")
    if not isinstance(slug, str) or not slug:
        raise ValueError("no slug: none in ticket.json and no --slug")
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    scope = harvest.get("scope") or DEFAULT_SCOPE
    if scope not in SCOPES:
        raise ValueError(f"harvest.scope={scope!r} — one of {', '.join(SCOPES)}")
    access = harvest.get("access") or "licensed"
    held = known_keys(ticket.get("known"))

    candidates = []  # (url, text, is_root)
    if scope == "page" or is_lesson(target):
        candidates.append((target, "", True))
    if scope != "page":
        for link in meta.get("discovered_lesson_links") or []:
            if isinstance(link, dict):
                candidates.append((link.get("href"), link.get("text") or "", False))

    leaves, dropped, seen = [], [], set()
    for raw, text, is_root in candidates:
        url = clean_url(raw)
        if url is None:
            continue
        key = same_key(url)
        if key in seen:
            # The sidebar repeats a link (section header + lesson row); the
            # first text that names a title wins.
            again_title, again_duration = link_facts(text)
            for leaf in leaves:
                if same_key(leaf["url"]) == key:
                    leaf["title"] = leaf["title"] or again_title
                    leaf["duration"] = leaf["duration"] or again_duration
            continue
        seen.add(key)
        if not is_root and not is_lesson(url):
            why = "not_lesson"
        elif not in_scope(url, target, scope):
            why = "scope"
        elif excluded(url, harvest.get("exclude_urls")):
            why = "excluded"
        elif key in held:
            why = "known"
        elif access == "free":
            why = "access"
        else:
            why = None
        if why:
            dropped.append({"url": url, "why": why})
            continue
        title, duration = link_facts(text)
        leaves.append(
            {
                "order": len(leaves) + 1,
                "url": url,
                "dir": capture_rel if is_root else leaf_dir(slug, url),
                "title": title,
                "duration": duration,
                "root": is_root,
            }
        )

    space = SPACE_RE.match(urlsplit(target).path)
    return {
        "v": PLAN_V,
        "ticket": ticket.get("ticket"),
        "slug": slug,
        "target": target,
        "capture_dir": capture_rel,
        "scope": scope,
        "access": access,
        # Circle declares no per-lesson date anywhere this unit has seen, so a
        # `min_date` cannot be applied; it rides the plan so the report can say so.
        "min_date": ticket.get("min_date"),
        "space": space.group(1) if space else None,
        "course": (meta.get("title") or "").strip() or None,
        "leaves": leaves,
        "dropped": dropped,
    }


# --- record -------------------------------------------------------------------


def downloaded_media(directory: Path, leaf_rel: str) -> list:
    """The FILE NAMES of the non-image assets `assets.py download` stored for
    this leaf — names, never paths. They sit under `_raw/`, which is
    machine-local and prunable, and a committed page never links into it."""
    try:
        entries = json.loads((directory / ASSETS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("status") != "downloaded" or entry.get("type") == "image":
            continue
        local = entry.get("local_path")
        if isinstance(local, str) and local:
            path = posixpath.normpath(posixpath.join(leaf_rel, local))
            name = posixpath.basename(path)
            if path.startswith(RAW_DIRNAME + "/") and name not in found:
                found.append(name)
    return found


def frontmatter_for(
    plan: dict, leaf: dict, body: str, meta: dict, *, author: str | None = None, media: list | None = None
) -> dict:
    """The lesson's exact facts: scalars and flat lists, unknown ones OMITTED."""
    path = urlsplit(leaf["url"]).path
    section, lesson = SECTION_RE.search(path), LESSON_RE.search(path)
    position = POSITION_RE.search(body)
    captions = [c["file"] for c in meta.get("captions") or [] if isinstance(c, dict) and isinstance(c.get("file"), str)]
    facts = {
        "type": "lesson",
        "course": plan.get("course"),
        "space": plan.get("space"),
        "section_id": section.group(1) if section else None,
        "lesson_id": lesson.group(1) if lesson else None,
        "position": f"Topic {position.group(1)} of {position.group(2)}" if position else None,
        "duration": leaf.get("duration"),
        "author": author,
        "captions": captions,
        "media": media or [],
    }
    return {k: v for k, v in facts.items() if v not in (None, "", []) and k not in HOST_OWNED}


FACT_LABELS = (
    ("type", "Type"),
    ("course", "Course"),
    ("space", "Space"),
    ("position", "Position"),
    ("duration", "Duration"),
    ("author", "Author"),
    ("captions", "Captions"),
    ("media", "Media"),
)


def facts_block(front: dict, item: str) -> str:
    rows = []
    for key, label in FACT_LABELS:
        value = front.get(key)
        if value:
            rows.append(f"- **{label}**: {', '.join(value) if isinstance(value, list) else value}")
    rows.append(f"- **Source**: <{item}>")
    return "\n".join(rows)


def with_facts(body: str, title: str | None, block: str) -> str:
    """`body` opening with its heading and the facts block under it. Re-running
    replaces the block this wrote before rather than stacking a second one. The
    result always opens with `# …`, so it can never open with a `---` line the
    extractor's own frontmatter would collide with."""
    body = body.lstrip("﻿\n")
    found = HEADING_RE.match(body)
    if found:
        heading, rest = found.group(0).rstrip("\n"), body[found.end():]
    else:
        heading, rest = f"# {title or 'Untitled lesson'}", body
    lines = rest.lstrip("\n").split("\n")
    if lines and lines[0] == FACTS_OPEN:
        while lines and FACT_LINE_RE.match(lines[0]):
            lines.pop(0)
    rest = "\n".join(lines).lstrip("\n")
    return f"{heading}\n\n{block}\n\n{rest}".rstrip() + "\n"


TRANSCRIPT_OPEN = "## Transcript\n\n*From the lesson's own caption track"


def with_transcript(body: str, transcript: str, source: str | None) -> str:
    """`body` closing with the formatted captions — the extractor reads the
    body and nothing beside it, so a transcript left as a sibling file never
    reaches the page. Replaces the section this wrote before."""
    cut = body.find(TRANSCRIPT_OPEN)
    if cut != -1:
        body = body[:cut]
    if not transcript.strip():
        return body.rstrip() + "\n"
    note = f"{TRANSCRIPT_OPEN}{f' (`{source}`)' if source else ''}, cleaned. Not manually corrected.*"
    return f"{body.rstrip()}\n\n{note}\n\n{transcript.strip()}\n"


def title_for(leaf: dict, body: str, meta: dict) -> str | None:
    heading = HEADING_RE.match(body.lstrip("﻿\n"))
    return leaf.get("title") or (heading.group(1) if heading else None) or (meta.get("title") or "").strip() or None


def fetched_at_of(path: Path) -> str:
    """When the capture wrote `meta.json` — that IS the fetch time, and it
    keeps a re-run over the same capture byte-identical."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def leaf_path(capture_dir: Path, leaf: dict) -> Path:
    """Where a planned leaf is on disk. Every leaf is `_raw/<slug>/<one>`, so
    each is the ticket directory itself or a sibling of it."""
    return capture_dir if leaf.get("root") else capture_dir.parent / leaf["dir"].rsplit("/", 1)[-1]


def find_leaf(plan: dict, url: str) -> dict | None:
    key = same_key(clean_url(url) or url)
    return next((leaf for leaf in plan.get("leaves") or [] if same_key(leaf["url"]) == key), None)


def record_leaf(capture_dir: Path, plan: dict, leaf: dict, *, author: str | None = None) -> dict:
    directory = leaf_path(capture_dir, leaf)
    body_path = directory / BODY_NAME
    try:
        body = body_path.read_text(encoding="utf-8")
    except OSError:
        raise ValueError(f"{body_path}: no {BODY_NAME} — convert the leaf's page.html with to_markdown.py first")
    if not body.strip():
        raise ValueError(f"{body_path}: empty — nothing to record")
    meta = read_json(directory / META_NAME)
    title = title_for(leaf, body, meta)
    front = frontmatter_for(plan, leaf, body, meta, author=author, media=downloaded_media(directory, leaf["dir"]))
    body = with_facts(body, title, facts_block(front, leaf["url"]))
    try:
        transcript = (directory / TRANSCRIPT_NAME).read_text(encoding="utf-8")
    except OSError:
        transcript = ""
    body = with_transcript(body, transcript, (front.get("captions") or [None])[0])
    body_path.write_text(body, encoding="utf-8")
    record = {
        "v": CAPTURE_V,
        "slug": plan["slug"],
        "item": leaf["url"],
        "title": title,
        "body": BODY_NAME,
        "content_type": "text/markdown",
        "fetched_at": fetched_at_of(directory / META_NAME),
        "frontmatter": front,
    }
    write_json(directory / CAPTURE_NAME, record)
    return record


# --- report -------------------------------------------------------------------


def landed(directory: Path) -> dict | None:
    """The leaf's capture record, if it names a body file that is there."""
    record = read_json(directory / CAPTURE_NAME)
    body = record.get("body")
    if not isinstance(body, str) or not body or not (directory / body).is_file():
        return None
    return record


def build_report(capture_dir: Path, ticket: dict, plan: dict, *, missing=(), auth_expired=False, reason=None) -> dict:
    captured, pending = [], []
    for leaf in plan.get("leaves") or []:
        record = landed(leaf_path(capture_dir, leaf))
        if record is None:
            pending.append(leaf["url"])
        else:
            captured.append({"item": leaf["url"], "dir": leaf["dir"], "title": record.get("title")})

    missing = [dict(entry) for entry in missing]
    named = {same_key(entry["url"]) for entry in missing}
    target = plan.get("target") or ticket.get("target") or ticket.get("item")
    if auth_expired:
        # The stored session died: every lesson not on disk is unreachable
        # until a person logs in again, and nothing more is fetched.
        for url in pending or ([] if plan else [target] if target else []):
            if same_key(url) not in named:
                missing.append({"host": host_of(url), "url": url, "why": "auth"})
                named.add(same_key(url))
    unreached = [url for url in pending if same_key(url) not in named]

    planned = len(plan.get("leaves") or [])
    if captured and not missing and not unreached:
        outcome, derived = "ok", None
    elif captured:
        outcome = "partial"
        derived = f"{len(captured)} of {planned} lessons captured"
        if unreached:
            derived += f"; {len(unreached)} not reached — the next run resumes from known[]"
    elif plan and not planned and any(d.get("why") == "known" for d in plan.get("dropped") or []):
        outcome, derived = "skipped", "known: every lesson in scope is already held"
    elif plan and not planned and any(d.get("why") == "access" for d in plan.get("dropped") or []):
        outcome, derived = "skipped", "harvest.access is free: every observed Circle lesson is behind the community login"
    else:
        outcome = "failed"
        derived = "no plan.json: the root capture did not land" if not plan else "nothing was captured"
    if auth_expired and target:
        derived = f"auth_expired:{host_of(target)}" + (f" — {derived}" if derived else "")

    return {
        "v": REPORT_V,
        "ticket": ticket.get("ticket") or plan.get("ticket"),
        "outcome": outcome,
        "reason": reason or derived,
        "captured": captured,
        "written": [],
        "missing": missing,
        "discovered": [],
    }


# --- cli ----------------------------------------------------------------------


def _ticket(capture_dir: Path, args) -> dict:
    ticket = read_json(capture_dir / TICKET_NAME)
    for key in ("target", "slug", "ticket"):
        if getattr(args, key, None):
            ticket[key] = getattr(args, key)
    if getattr(args, "scope", None):
        ticket["harvest"] = {**(ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}), "scope": args.scope}
    return ticket


def _capture_rel(capture_dir: Path, ticket: dict, given: str) -> str:
    """The ticket's own `capture_dir`; on a hand run, the `_raw/<slug>/<one>`
    tail of the path given."""
    if isinstance(ticket.get("capture_dir"), str) and ticket["capture_dir"]:
        return ticket["capture_dir"]
    parts = capture_dir.resolve().parts
    if len(parts) >= 3 and parts[-3] == RAW_DIRNAME:
        return "/".join(parts[-3:])
    return Path(given).as_posix()


def cmd_plan(args) -> int:
    capture_dir = Path(args.capture_dir)
    ticket = _ticket(capture_dir, args)
    meta = read_json(capture_dir / META_NAME)
    if not meta:
        print(f"error: {capture_dir / META_NAME}: no root capture — run capture_lesson.py on the target first", file=sys.stderr)
        return 1
    try:
        plan = build_plan(ticket, meta, capture_rel=_capture_rel(capture_dir, ticket, args.capture_dir))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    write_json(capture_dir / PLAN_NAME, plan)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


def cmd_record(args) -> int:
    capture_dir = Path(args.capture_dir)
    plan = read_json(capture_dir / PLAN_NAME)
    if not plan:
        print(f"error: {capture_dir / PLAN_NAME}: no plan — run `plan` first", file=sys.stderr)
        return 1
    leaf = find_leaf(plan, args.url)
    if leaf is None:
        print(f"error: {args.url} is not a leaf of this plan — only planned lessons are recorded", file=sys.stderr)
        return 1
    try:
        record = record_leaf(capture_dir, plan, leaf, author=args.author)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"dir": leaf["dir"], "title": record["title"], "frontmatter": record["frontmatter"]}, ensure_ascii=False))
    return 0


def cmd_report(args) -> int:
    capture_dir = Path(args.capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)
    ticket = _ticket(capture_dir, args)
    plan = read_json(capture_dir / PLAN_NAME)
    missing = [{"host": host, "url": url, "why": why} for host, url, why in args.missing or []]
    bad = [entry["why"] for entry in missing if entry["why"] not in WHYS]
    if bad:
        print(f"error: --missing why is one of {', '.join(WHYS)}; got {', '.join(bad)}", file=sys.stderr)
        return 2
    report = build_report(capture_dir, ticket, plan, missing=missing, auth_expired=args.auth_expired, reason=args.reason)
    write_json(capture_dir / REPORT_NAME, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["outcome"] == "failed" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="write plan.json: the ordered leaf work list")
    p.add_argument("capture_dir", help="the ticket's capture dir (ticket.json + the root capture's meta.json)")
    p.add_argument("--target", help="the job's url, when there is no ticket.json")
    p.add_argument("--slug", help="the job's slug, when there is no ticket.json")
    p.add_argument("--scope", choices=SCOPES, help="overrides ticket.json's harvest.scope")
    p.add_argument("--ticket", help="the ticket id, when there is no ticket.json")
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("record", help="facts block into one leaf's page.md, and its capture.json")
    r.add_argument("capture_dir", help="the ticket's capture dir (holds plan.json)")
    r.add_argument("url", help="the planned lesson url")
    r.add_argument("--author", help="the lesson's author, when the page names one")
    r.set_defaults(fn=cmd_record)

    w = sub.add_parser("report", help="write report.json — last")
    w.add_argument("capture_dir", help="the ticket's capture dir")
    w.add_argument("--auth-expired", action="store_true", help="every lesson not on disk goes to missing[] as why=auth")
    w.add_argument("--reason", help="overrides the derived reason")
    w.add_argument("--missing", nargs=3, action="append", metavar=("HOST", "URL", "WHY"))
    w.add_argument("--ticket", help="the ticket id, when there is no ticket.json")
    w.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
