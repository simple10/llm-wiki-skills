#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The deterministic half of a HubSpot section HARVEST: which pages, which
directories, when to stop, and the report.

Harvest is BYTES. This script never renders a page: it applies
`harvest.scope`, `harvest.exclude_urls`, `min_date` and `known[]` to the
enumerated URLs, names one capture directory per page, records what the venue
served in a flat `capture.json`, and posts `tickets update`. The venue's own
rendering — the site's selectors, the facts, the video reference — is the
PROCESS step's, which SKILL.md walks.

`plan` opens the ticket through `tickets open`, given `--ticket` — the one
command that needs THIS pull's own fields (`slug`, `target`, `harvest.*`,
`min_date`, `known[]`, `refresh`, `resource`). It writes them into
`plan.json`'s own `job` object, and every other command (`next`, `assets`,
`record`, `report`) reads that copy back rather than opening the ticket
again — no ticket lookup on every per-leaf command. A hand run with no
`--ticket` at all passes them as flags instead; every other command then
reads them back off `plan.json`.

**No command takes a venue's url on its command line.** A sitemap is the
venue's text, and a `<loc>` ending `;$(touch${IFS}PWNED)` typed onto a shell
line is a command. So `plan` DROPS (why: `unsafe_url`) every url that is not
http(s), does not parse, or carries a character outside a conservative set —
no whitespace, quote, `;`, `&`, `$`, backtick, bracket, brace, `|`, `<`, `>`,
`*`, `!` or backslash — and every per-leaf command names its page as
`--leaf <n>`, the index in `plan.json`'s `leaves[]`, and reads the url from
there. (`&` is refused too, so a page addressed by two query parameters is
dropped: name the one that does not change the page in the site's
`strip_params`.)

  plan   <capture_dir> --urls <file> [--urls <file> ...] [--limit N]
         [--budget-minutes M] --ticket <id>
         FIRST removes what an earlier run left in the ticket's capture
         directory — `plan.json` — because that directory is stable across
         pulls and a respawn that fails must not be read as the success the
         run before it had. Then reads the enumeration (a saved
         sitemap.xml, a JSON list of urls or of {url, lastmod} objects, or one
         url per line), normalizes each url (fragment and `hsLang` stripped,
         plus the site's own `strip_params`), filters, orders newest-first,
         and writes `<capture_dir>/plan.json`:
           {"leaves": [{"item", "dir", "lastmod"[, "landed"]}],
            "skipped": [{"url", "why"}], "deadline", "deadline_epoch",
            "hard_stop_epoch", "limit", "job", ...}
         The ticket's own item keeps the ticket's own `capture_dir`; every
         other page gets `_raw/<slug>/<page-slug>--<hash8>`. The host has no
         verb for that name, so it is composed here in the host's own shape
         (`pipeline/jobs.py::capture_dir_for`): the url's path slugified, then
         the first 8 hex of sha1(item url).
         A leaf whose directory ALREADY holds a finished capture of the same
         url — a run the slice cap killed before its report was read — is
         marked `landed`: it is not captured again, does not count against
         `--limit`, and `report` lists it.
         `--limit` caps the pages ONE run attempts (default 6 where the job
         downloads, none where `harvest.assets` is `reference`; `0` is none).
         The deadline is keyed to THIS run's own first write — there is no
         per-run timestamp on disk to anchor it on any more — plus
         `--budget-minutes` (default 20 of the slice's 30).
         A REFRESH ticket (`refresh: true`) plans exactly its `resource`, into
         the ticket's own `capture_dir`, whatever `known[]` says, and removes
         the capture an earlier pull left there: `apply` hashes the body in
         that directory, so it has to be this run's. `--urls` is not needed.

  next   <capture_dir>
         The next planned page with no capture yet, as {"n", "dir", ...}; or
         {"done": true}; or — exit 5 — {"stop": true} once the deadline has
         passed. Ask it before every leaf.

  assets <capture_dir> --leaf <n>
         The plugin's `assets.py detect`, this unit's `patch-assets`, then the
         plugin's `assets.py download` in the job's `harvest.assets` mode —
         each started with an argv list, never a shell, because `--base-url`
         and `--referer` are the page's url. Exit 3 when a download was asked
         for and the video did not arrive (`"video"` says why).

  record <capture_dir> --leaf <n> [--media-file <path> | --no-media]
         Writes the leaf's FLAT `capture.json` — `slug`, `item`, `title`,
         `body` (`page.html`, the file as it arrived), `content_type`,
         `fetched_at` — and nothing else: no page, no summary, no
         `frontmatter` object. `title` is `safe_title(<the venue's title off
         meta.json>)`, because the page's FILE is named from it and the host
         refuses one its filename rule cannot hold.
         `fetched_at` is the render's own time off `meta.json`, else
         `page.html`'s mtime — never the time this command ran.
         Where the video was downloaded — the leaf's `assets.json` marks the
         stable Mux master `downloaded`, or `--media-file` names the file —
         the file is placed in the leaf as `media.<ext>` (hard-linked out of
         the job's asset store), which is what the process step turns into the
         lesson's transcript stub. Never on a refresh ticket.
         Prints `video`: the checked `embed_url`, `stream_url`, `player_url`
         and `mux_playback_id`, which is where the process step copies them
         from rather than reading `meta.json` raw.
         Exit 5 = written, and the deadline has passed: report and stop.

  report <capture_dir> --ticket <id> [--missing-leaf <why> <n>]
         [--missing-host <why> <host>] [--reason TEXT] [--failed | --gone]
         [--written-from <file>] [--skipped]
         Posts `tickets update` — run it after EVERY leaf, not only last: the
         posted status is then true whenever the worker stops. A HARVEST
         report (no `--written-from`, no `--skipped`) reads `plan.json`,
         lists every leaf that really holds a capture in `captured=`. Before
         that it settles the titles: a page is filed under its TITLE and
         overwrites what is there, so a later leaf whose title makes the
         filename an earlier one made is retitled `<title> (<the url path
         segment that tells them apart>)` in its own `capture.json` (see
         "page names" below).
         Given `--written-from <file>`, it is a PROCESS report instead:
         `<file>`, inside the capture dir, is a JSON list of the pages this
         run wrote; `written_from=` is posted and no capture is claimed.
         `--skipped` is the process report for a capture that earned no page
         (P-4: `ok`, named in `--reason`).

Usage (`<capture_dir>` is the ticket's `capture_dir`, verbatim — it is
WIKI-RELATIVE, and `run` starts every script at the wiki root; every other
relative path here is wiki-relative too):
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py plan <capture_dir> --urls <capture_dir>/sitemap.xml --ticket <id>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py next <capture_dir>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py assets <capture_dir> --leaf <n>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py record <capture_dir> --leaf <n>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py report <capture_dir> --ticket <id>

(The leading `ops/` is the run verb's frozen argument grammar, resolved by
the front door to wherever this wiki's machinery tree lives.)

Stdlib only, and nothing is imported from the plugin.
"""

from __future__ import annotations

import unicodedata
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
SITES = HERE.parent / "references" / "sites.json"
CAPTURER = HERE / "capture_hubspot_video.py"

PLAN_NAME = "plan.json"
CAPTURE_NAME = "capture.json"
HTML_NAME = "page.html"
MEDIA_STEM = "media"
PUBLISHED_NAME = "published.txt"
EXTERNAL_NAME = "external_url.txt"
RAW = "_raw"

# The front door, by the bare name every SKILL.md runs this script under.
OPS = "llm-wiki-ops"


def front_door() -> list:
    """The front door, as an argv prefix.

    A hosted run exports `LLM_WIKI_OPS`, naming the CLI it was itself reached
    by — a command LINE, not a path — and that is the one spelling a jail is
    sure to carry. Otherwise the bare name on PATH. Empty when there is
    neither."""
    named = os.environ.get("LLM_WIKI_OPS")
    if named:
        return shlex.split(named)
    found = shutil.which(OPS)
    return [found] if found else []


def open_ticket(ticket: str, stage: str | None = None) -> dict:
    """This worker's own ticket (A-1), through the front door. Exits naming
    the refusal."""
    me = Path(__file__).stem
    door = front_door()
    if not door:
        sys.exit(f"{me}: `{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — the front door is how this unit reaches the plugin")
    argv = [*door, "--json", "pipeline", "tickets", "open", ticket]
    if stage:
        argv.append(f"stage={stage}")
    cp = subprocess.run(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        sys.exit(f"{me}: `tickets open {ticket}` refused — {(cp.stdout + cp.stderr).strip()}")
    try:
        return json.loads(cp.stdout)["ticket"]
    except (ValueError, KeyError) as exc:
        sys.exit(f"{me}: `tickets open {ticket}` did not answer a ticket ({exc}) — {cp.stdout}")


def post_update(
    ticket: str,
    stage: str,
    status: str,
    *,
    reason: str | None = None,
    captured=(),
    missing=(),
    written_from: str | None = None,
    produced: int | None = None,
    note: str | None = None,
) -> int:
    """This worker's progress (A-2), through the front door. `missing` is an
    iterable of `(host, url, why)`; a `,` inside `url` is typed as `%2C`,
    the side note every unit's `missing=` build follows the same way."""
    me = Path(__file__).stem
    door = front_door()
    if not door:
        sys.exit(f"{me}: `{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — the front door is how this unit posts progress")
    argv = [*door, "--json", "pipeline", "tickets", "update", ticket, f"stage={stage}", f"status={status}"]
    if reason:
        argv.append(f"reason={reason}")
    for directory in captured:
        argv.append(f"captured={directory}")
    for host, url, why in missing:
        argv.append(f"missing={host},{url.replace(',', '%2C')},{why}")
    if written_from:
        argv.append(f"written_from={written_from}")
    if produced is not None:
        argv.append(f"produced={produced}")
    if note:
        argv.append(f"note={note}")
    cp = subprocess.run(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        print(f"{me}: `tickets update` refused — {(cp.stdout + cp.stderr).strip()}", file=sys.stderr)
    return cp.returncode


# What a nested front-door call must NOT inherit from the one that ran this
# script. `CLAUDE_PROJECT_DIR` is the harness's project directory, never a wiki
# root: the `cwd=<root>` this script was handed is what binds the nested call
# to THIS wiki.
NOT_INHERITED = ("CLAUDE_PROJECT_DIR",)
# Addresses `run` serves out of the plugin, not paths in this wiki.
ASSETS_SCRIPT = "scripts/assets.py"  # G3: the plugin's address after PR 4; this unit types no other

# Always stripped: HubSpot appends `?hsLang=<lang>` to internal nav links, so
# one page is otherwise two urls. A site adds its own under `strip_params`.
STRIP_PARAMS = ("hsLang",)

# The suffixes a downloaded lesson may have. The process step writes the
# transcript stub that names the file, and the transcriber reads it.
MEDIA_SUFFIXES = frozenset({".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".mp4", ".mov", ".mkv", ".webm", ".m4v"})

WHY = ("denied", "timeout", "auth", "error")

# The clock. A slice is killed at thirty minutes (`schedule/runner/slice.py::
# SLICE_CAP_SECONDS`) and the kill fails the ticket WITHOUT reading a report
# (`schedule/broker.py::_kill`), so the run has to end itself first. No new
# leaf is started past the BUDGET; a download still running at the HARD STOP
# is ended so the last `report` is written by this worker and not lost.
SLICE_CAP_SECONDS = 1800
BUDGET_MINUTES = 20
HARD_STOP_SECONDS = SLICE_CAP_SECONDS - 180
# Render (~45 s) plus the download of a ~28-minute video is ESTIMATED at three
# to five minutes a page (unmeasured inside a live slice), so six fit the budget.
# The deadline ends the run either way; the limit keeps the PLAN honest.
DOWNLOAD_LIMIT = 6
STOP = 5  # exit code: the deadline has passed — report, and stop

# What a skipped row must say for "nothing new" to be a true, lasting `ok`:
# the job already holds the page, or it is too old, or this run left it for
# the next. Rows that are ALL `scope`/`excluded`/`unsafe_url` are a
# mis-rooted job.
CLOSES = ("known", "older_than_min_date", "over_limit")

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# A url this unit will carry: http(s), and nothing a shell, a markdown link or
# an HTML attribute reads as anything but text.
_SAFE_URL = re.compile(r"^https?://[A-Za-z0-9._~:/?#@=%+,-]+$")
_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# What `capture_hubspot_video.py render` writes into `meta.json` is read off
# the venue's DOM and network log, so each value is checked against the one
# shape it can honestly have before it reaches a page. A HubSpot player on
# another host is added HERE, in the wiki's copy, once it has been seen.
PLAYER_HOSTS = ("play.hubspotvideo.com",)
_PLAYER_PATH = re.compile(r"^/v/\d+/id/\d+$")
_EMBED_QUERY = re.compile(r"^[A-Za-z0-9._~%&=+:/,-]*$")
_MUX_ID = re.compile(r"^[A-Za-z0-9]{20,}$")
_STREAM = re.compile(r"^https://stream\.mux\.com/[A-Za-z0-9]{20,}\.m3u8$")


class Problem(Exception):
    """Something the caller got wrong; said on stderr, exit 2."""


# ------------------------------------------------------------ pure: venue text


# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "\u2019", "'": "\u2019", "<": "(", ">": ")"}
TITLE_MAX = 120        # characters
TITLE_MAX_BYTES = 200  # UTF-8 bytes: the host checks no length, and a filename is capped in BYTES (255 on
                       # ext4/APFS) with `.md` appended — 100 CJK characters is 300 bytes and the REAL extractor
                       # dies `OSError: [Errno 36] File name too long` (measured, channel-youtube)

def safe_title(text, fallback="Untitled"):
    text = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))  # control chars, newlines, tabs
    for bad, good in _TITLE_SWAPS.items():
        text = text.replace(bad, good)
    text = " ".join(text.split()).lstrip(". ").rstrip(" .")
    cut = text[:TITLE_MAX]
    while len(cut.encode("utf-8")) > TITLE_MAX_BYTES:
        cut = cut[:-1]
    if cut != text:
        text = cut.rstrip(" .-") + "…"
    if text.casefold() == "index":
        # `index.md` is the host's one RESERVED page name (note.py::RESERVED): its page walker skips
        # it, so the page never appears in `known[]` and the item is re-pulled forever.
        text = f"{text} (page)"
    return text or fallback


def fold(text) -> str:
    """Venue text as ONE line: newlines, tabs and control characters become a
    space. What every title and fact value passes through before it reaches
    a page or `capture.json`, so none of them can start a line of its own."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())


def safe_url(url) -> str | None:
    """The url, when it is one this unit will carry; else None."""
    if not isinstance(url, str) or not _SAFE_URL.match(url):
        return None
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — a port that is not a number raises here
    except ValueError:
        return None
    return url if parts.hostname and _HOST.match(parts.hostname.lower()) else None


def player_url(url, *, query: bool) -> str | None:
    """A HubSpot player address, `https` on an expected host, in the one shape
    the platform serves: `/v/<portal>/id/<video>` — with the live iframe's
    own query where `query` allows one."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc not in PLAYER_HOSTS or not _PLAYER_PATH.match(parts.path) or parts.fragment:
        return None
    if parts.query and (not query or not _EMBED_QUERY.match(parts.query)):
        return None
    return url


def clean_meta(meta: dict) -> dict:
    """`meta.json` with every value that reaches the page checked, the rest dropped."""
    stream = meta.get("stream_url") if isinstance(meta.get("stream_url"), str) and _STREAM.match(meta["stream_url"]) else None
    mux_id = meta.get("mux_playback_id") if isinstance(meta.get("mux_playback_id"), str) and _MUX_ID.match(meta["mux_playback_id"]) else None
    return {
        "title": meta.get("title") if isinstance(meta.get("title"), str) else None,
        "stream_url": stream,
        "mux_playback_id": mux_id,
        "player_url": player_url(meta.get("player_url"), query=False),
        "embed_url": player_url(meta.get("embed_url"), query=True),
        "final_url": safe_url(meta.get("final_url")),
        "status": meta.get("status") if isinstance(meta.get("status"), int) else None,
        "fetched_at": meta.get("fetched_at") if isinstance(meta.get("fetched_at"), str) and _STAMP.match(meta["fetched_at"]) else None,
    }


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


def _day(value) -> str | None:
    return value[:10] if isinstance(value, str) and _DATE.match(value) else None


def parse_urls(text: str) -> tuple[list[dict], list[str]]:
    """`([{url, lastmod}], [child sitemap urls])` from a sitemap, a JSON list
    or plain lines. A sitemap INDEX yields no pages, only the children to
    fetch. Raises `ValueError` (XML's and JSON's parse errors are both one) on
    a file that is neither."""
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("<"):
        try:
            root = ET.fromstring(stripped)
        except ET.ParseError as exc:
            raise ValueError(f"not a sitemap — {exc}") from None
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


def _normal(url, strip_params) -> str | None:
    """The normalized url, or None for one this unit will not carry."""
    if safe_url(url.strip() if isinstance(url, str) else url) is None:
        return None
    try:
        return safe_url(normalize_url(url, strip_params))
    except ValueError:
        return None


def plan_leaves(pages: list[dict], *, slug: str, target: str, item: str | None, capture_dir: str | None,
                scope: str = "section", exclude_urls=(), min_date: str | None = None, known=(),
                strip_params=(), limit: int | None = None, refresh: str | None = None, landed=None) -> dict:
    """The filter, as data in and data out. Order of the tests is the order a
    reader would ask them: can this url be carried at all, is it a page of
    this job, was it excluded, is it too old, do we already hold it.

    `refresh` is a refresh ticket's `resource`: the plan is exactly that page,
    in the ticket's own directory, and nothing else is asked. `landed(leaf)`
    says a leaf's directory already holds its finished capture."""
    if refresh is not None:
        return {"leaves": [{"item": refresh, "dir": capture_dir or leaf_dir(slug, refresh), "lastmod": None}], "skipped": []}
    target_n = normalize_url(target, strip_params)
    item_n = normalize_url(item, strip_params) if item else target_n
    held = {normalize_url(row["resource"], strip_params) for row in known if isinstance(row, dict) and isinstance(row.get("resource"), str)}
    if scope == "page" and not any(_normal(p["url"], strip_params) == target_n for p in pages):
        pages = [*pages, {"url": target, "lastmod": None}]
    seen, kept, skipped = set(), [], []
    for page in pages:
        url = _normal(page["url"], strip_params)
        if url is None:
            # Kept as evidence, folded to a line and capped: a row in a JSON
            # file, never a thing to type.
            skipped.append({"url": fold(page["url"])[:300], "why": "unsafe_url"})
            continue
        if url in seen:
            continue
        seen.add(url)
        if not in_scope(url, target_n, scope):
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
    # Newest first, undated last: what this run does not reach is what the
    # next one plans, once the pages this one landed are in `known[]`.
    kept.sort(key=lambda leaf: leaf["lastmod"] or "", reverse=True)
    if landed is not None:
        kept = [{**leaf, "landed": True} if landed(leaf) else leaf for leaf in kept]
    if limit:
        fresh = [leaf for leaf in kept if not leaf.get("landed")]
        over = {leaf["item"] for leaf in fresh[limit:]}
        skipped += [{"url": leaf["item"], "why": "over_limit"} for leaf in kept if leaf["item"] in over]
        kept = [leaf for leaf in kept if leaf["item"] not in over]
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


# ------------------------------------------------------------ pure: the capture


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_record(*, slug: str, item: str, title: str | None, body: str, content_type: str, fetched_at: str | None = None) -> dict:
    """What harvest leaves behind, FLAT: what was fetched, and the file it is
    in. No page, no summary, no frontmatter object, and never a key a host
    verb owns (`status`, `resource`, `harvested`, `extracted`,
    `document_id`, `document_revision`) — those are the process step's, on the
    page itself."""
    return {
        "slug": slug,
        "item": item,
        "title": title,
        "body": body,
        "content_type": content_type,
        "fetched_at": fetched_at or now_stamp(),
    }


# ------------------------------------------------------------ pure: the update


def how_to_continue(job: dict) -> str:
    """What an operator does after a `partial`. `pipeline/dueness.py::
    _harvest` never finds an `every: once` job due again once an `ok` exists —
    this unit's manifest default. `ticket.json` no longer exists to say which
    cadence the job has, so the reason says both."""
    ticket, slug = job.get("ticket") or "<ticket>", job.get("slug") or "<slug>"
    return (
        f"a periodic job is pulled again on its own; an `every: once` job is NOT (a partial lands as ok) — "
        f"`llm-wiki-ops pipeline tickets retry {ticket}` re-runs this ticket (three attempts a ticket), or "
        f"`llm-wiki-ops pipeline jobs edit {slug} every=1h` until a run reports `ok` with nothing new, then `every=once`"
    )


def process_update(*, written: list[str], reason: str | None = None, failed: bool = False, skipped: bool = False) -> tuple[str, str | None]:
    """The PROCESS step's status/reason: the pages this unit wrote under
    `dest`. P-4: a capture excluded by `process.exclude_rules` or `min_date`
    earns no page and is `ok`, named in `reason` — `skipped` is no unit's
    word any more."""
    status = "failed" if failed else ("ok" if (skipped or written) else "failed")
    if status == "failed" and not reason:
        reason = "no page was written"
    return status, reason


def build_update(*, planned: list[dict], captured: list[dict], skipped: list[dict],
                 missing: list[tuple], reason: str | None = None, failed: bool = False, gone: bool = False,
                 job: dict | None = None) -> dict:
    """`ok` when every page of the job's that is not already held landed,
    `partial` when some did — or all the PLANNED ones did and the limit left
    others — `ok` (nothing new, named in the reason) when there was truly
    nothing new to get, `gone` on a refresh whose source answered 404/410,
    `failed` otherwise.

    "Nothing new" is only true when some row says the job already HOLDS a page
    (`known`), or it is too old, or it was left for the next run. Rows that
    are all `scope`/`excluded` mean the job is rooted where the section is
    not: closing having captured nothing is reported `failed`.

    P-5: a `missing[]` entry (a denied host, an asset that never answered) is
    a LASTING shortfall — it never bumps `ok` to `partial`. `partial` means
    only "a re-run of this stage in this directory gets more.\""""
    job = job or {}
    pages = list(planned)
    landed_dirs = {row["dir"] for row in captured}
    got = [leaf for leaf in pages if leaf["dir"] in landed_dirs]
    over = sum(1 for row in skipped if row.get("why") == "over_limit")
    reasons = [reason] if reason else []
    if failed:
        status = "failed"
        reasons = reasons or ["failed"]
    elif gone:
        status = "gone"
    elif pages and len(got) == len(pages) and not over:
        status = "ok"
    elif got and (over or len(pages) - len(got) > len(missing)):
        # P-5: a page remains un-attempted (over_limit, or neither captured
        # nor named in `missing`) — a re-run of this stage gets more.
        status = "partial"
        total = len(pages) + over
        reasons.append(f"{len(got)} of {total} pages captured, {total - len(got)} left for another run; {how_to_continue(job)}")
    elif got:
        # Every page was attempted: what did not land is a LASTING shortfall,
        # named in `missing[]` and the reason below, never `partial` — a
        # re-run of this stage gets nothing more (P-5).
        status = "ok"
    elif pages:
        status = "failed"
        reasons = reasons or [f"none of {len(pages)} pages captured"]
    elif skipped:
        counts: dict = {}
        for row in skipped:
            counts[row["why"]] = counts.get(row["why"], 0) + 1
        said = ", ".join(f"{why}: {n}" for why, n in sorted(counts.items()))
        if any(why in counts for why in CLOSES):
            status = "ok"
            reasons.append(f"nothing new in scope — {said}")
        else:
            status = "failed"
            reasons.append(
                f"nothing enumerated is inside harvest.scope `{job.get('scope') or 'section'}` of the job's target "
                f"{job.get('target') or '<target>'} ({said}) — a job rooted at a leaf, or on another host, captures "
                f"nothing: point it at the section ROOT"
            )
    else:
        status = "failed"
        reasons = reasons or ["no pages enumerated"]
    if missing:
        reasons.append(f"{len(missing)} url(s) could not be reached")
    return {"status": status, "reason": "; ".join(reasons) or None, "captured": captured if status not in ("failed", "gone") else []}


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


def job_facts(capture: Path, args=None, *, stage: str | None = None, fresh: bool = False) -> dict:
    """The job. `fresh` (`plan` alone) opens the ticket through `tickets
    open` (A-1), given `--ticket` — the only command that needs THIS pull's
    own fields. Every other command reads `plan.json`'s own copy instead,
    written once by `plan` and carried forward: no ticket lookup on every
    per-leaf command. Explicit flags override either source, for a hand run
    (the first time, with no ticket and no `plan.json` yet)."""
    blank = {"ticket": None, "slug": None, "item": None, "target": None, "capture_dir": None, "scope": "section",
             "exclude_urls": [], "min_date": None, "known": [], "assets": "download", "refresh": False, "resource": None}
    ticket_id = getattr(args, "ticket", None) if args is not None else None
    ticket = open_ticket(ticket_id, stage) if (fresh and ticket_id) else None
    if isinstance(ticket, dict):
        harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
        facts = {
            **blank,
            "ticket": ticket.get("ticket"), "slug": ticket.get("slug"), "item": ticket.get("item"),
            "target": ticket.get("target") or ticket.get("item"), "capture_dir": ticket.get("capture_dir"),
            "scope": harvest.get("scope") or "section", "exclude_urls": harvest.get("exclude_urls") or [],
            "min_date": ticket.get("min_date"), "known": ticket.get("known") or [],
            "assets": harvest.get("assets") or "download",
            "refresh": ticket.get("refresh") is True,
            "resource": ticket.get("resource") or ticket.get("item"),
        }
    else:
        plan = _read_json(capture / PLAN_NAME)
        facts = {**blank, **plan["job"]} if isinstance(plan, dict) and isinstance(plan.get("job"), dict) else dict(blank)
    for key in ("ticket", "slug", "target", "scope", "min_date", "assets"):  # explicit flags win: a hand run
        if args is not None and getattr(args, key, None):
            facts[key] = getattr(args, key)
    if args is not None and getattr(args, "exclude_url", None):
        facts["exclude_urls"] = [*facts["exclude_urls"], *args.exclude_url]
    facts["item"] = facts.get("item") or facts["target"]
    if facts.get("slug") and not facts.get("capture_dir"):
        facts["capture_dir"] = f"{RAW}/{facts['slug']}/{capture.name}"
    return facts


def _capture_dir(given: str) -> Path:
    capture = Path(given).resolve()
    if not capture.is_dir():
        raise Problem(
            f"{given} is not a directory here ({Path.cwd()}) — pass the ticket's `capture_dir` verbatim: it is "
            f"wiki-relative, and `run` starts this script at the wiki root"
        )
    return capture


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(plan: dict) -> dict:
    """`{"stop", "seconds_left"}` against the plan's deadline; never stops a plan that carries none."""
    deadline = plan.get("deadline_epoch")
    if not isinstance(deadline, (int, float)):
        return {"stop": False, "seconds_left": None}
    left = int(deadline - time.time())
    return {"stop": left <= 0, "seconds_left": max(left, 0)}


def _urls_text(name: str) -> str:
    if name == "-":
        return sys.stdin.read()
    try:
        return Path(name).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise Problem(
            f"--urls {name}: {exc.strerror or exc} — save the enumeration into the capture directory first; the path "
            f"is wiki-relative. Then `report --failed --reason <why>` if it cannot be had"
        ) from None


def cmd_plan(args) -> int:
    capture = _capture_dir(args.capture_dir)
    job = job_facts(capture, args, stage="harvest", fresh=True)
    if not job.get("slug") or not job.get("target"):
        raise Problem(f"no ticket for {capture}: pass --ticket, or --slug and --target for a hand run")
    root = wiki_root(capture, job["capture_dir"])
    # Before anything else: this directory is stable across pulls, so what an
    # earlier run left must not outlive a run that fails.
    stale = [PLAN_NAME]
    if job["refresh"]:
        # `apply` hashes the body in THIS directory against the page's stamp:
        # it has to be this run's bytes, or `unchanged` is a claim nobody checked.
        stale += [CAPTURE_NAME, HTML_NAME, "meta.json", "net.json", "assets.json", PUBLISHED_NAME, EXTERNAL_NAME]
    for name in stale:
        (capture / name).unlink(missing_ok=True)
    rules = site_rules(load_sites(args.sites), urlsplit(job["target"]).netloc)
    strip = rules.get("strip_params") or ()
    pages, children, refresh = [], [], None
    if job["refresh"]:
        refresh = _normal(job["resource"], strip)
        if refresh is None or _STREAM.match(job["resource"] or ""):
            raise Problem(
                f"refresh of {fold(job['resource'])[:200]}: not a page this unit can re-fetch — a media stub's resource "
                f"is the Mux master, which is re-downloaded with its PAGE, never alone. `report --failed --reason "
                f"refresh_unsupported:media`"
            )
    else:
        if not args.urls:
            raise Problem("plan needs --urls <file> (the saved sitemap) on a ticket that is not a refresh")
        for name in args.urls:
            try:
                found, more = parse_urls(_urls_text(name))
            except ValueError as exc:
                raise Problem(f"--urls {name}: {exc}. Save what the site really served, or `report --failed --reason <why>`") from None
            pages += found
            children += more
    limit = args.limit if args.limit is not None else (None if job["assets"] == "reference" else DOWNLOAD_LIMIT)

    def landed(leaf: dict) -> bool:
        record = _read_json(root / leaf["dir"] / CAPTURE_NAME)
        return held(root, leaf) is not None and isinstance(record, dict) and record.get("item") == leaf["item"]

    plan = plan_leaves(
        pages, slug=job["slug"], target=job["target"], item=job["item"], capture_dir=job["capture_dir"],
        scope=job["scope"], exclude_urls=[*job["exclude_urls"], *(rules.get("exclude_urls") or [])],
        min_date=job["min_date"], known=job["known"], strip_params=strip, limit=limit or None,
        refresh=refresh, landed=None if job["refresh"] else landed,
    )
    # This run's own first write (P-8): there is no per-run timestamp on disk
    # to anchor the deadline on any more — the ticket id is the same on
    # every pull, but `open` answers no timestamp for it.
    spawned = time.time()
    plan = {
        # The one kind of venue url a worker fetches by hand: only a safe one, on the target's own host.
        "v": 1, "job": {**job, "known": []}, "sitemaps": [url for url in children if safe_url(url) and in_scope(url, job["target"], "domain")],
        "spawned_at": _iso(spawned), "deadline": _iso(spawned + args.budget_minutes * 60),
        "deadline_epoch": spawned + args.budget_minutes * 60, "hard_stop_epoch": spawned + HARD_STOP_SECONDS,
        "limit": limit or None, **plan,
    }
    _write_json(capture / PLAN_NAME, plan)
    counts: dict = {}
    for row in plan["skipped"]:
        counts[row["why"]] = counts.get(row["why"], 0) + 1
    print(json.dumps({
        "leaves": [{"n": n, "dir": leaf["dir"], "item": leaf["item"], "landed": bool(leaf.get("landed"))} for n, leaf in enumerate(plan["leaves"])],
        "skipped": counts, "sitemaps": plan["sitemaps"], "limit": plan["limit"], "deadline": plan["deadline"], **clock(plan),
    }, indent=1))
    return 0


def _plan_of(capture: Path) -> dict:
    plan = _read_json(capture / PLAN_NAME)
    return plan if isinstance(plan, dict) else {}


def _leaf_at(plan: dict, n: int) -> dict:
    leaves = plan.get("leaves") or []
    if not 0 <= n < len(leaves):
        raise Problem(f"--leaf {n}: {PLAN_NAME} names no page at that index (it holds {len(leaves)} rows; run `plan`, then `next`)")
    return leaves[n]


def _leaf_of(plan: dict, root: Path, leaf: Path) -> dict | None:
    return next((row for row in plan.get("leaves") or [] if (root / row["dir"]).resolve() == leaf), None)


def cmd_next(args) -> int:
    capture = _capture_dir(args.capture_dir)
    job = job_facts(capture)
    root = wiki_root(capture, job["capture_dir"])
    plan = _plan_of(capture)
    pages = list(enumerate(plan.get("leaves") or []))
    todo = [(n, leaf) for n, leaf in pages if held(root, leaf) is None]
    now = clock(plan)
    if not todo:
        print(json.dumps({"done": True, "captured": len(pages), **now}))
        return 0
    if now["stop"]:
        print(json.dumps({"stop": True, "why": "deadline", "left": len(todo), **now}))
        return STOP
    n, leaf = todo[0]
    print(json.dumps({"n": n, "dir": leaf["dir"], "item": leaf["item"], "left": len(todo), **now}))
    return 0


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


def _line_of(path: Path) -> str | None:
    """The first non-empty line of a small file the worker left in the leaf."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    return next((line.strip() for line in text.splitlines() if line.strip()), None)


def _spawn(root: Path, *args, timeout: float | None = None) -> int:
    """One front-door command by ARGV — no shell ever reads these words — bound
    to the wiki by running from its root. Its stdout goes to our stderr, so
    this script's own stdout stays the one JSON answer. -1 = ended at the hard stop."""
    env = {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}
    try:
        return subprocess.run([*(front_door() or [OPS]), *args], cwd=str(root), env=env, stdout=sys.stderr, timeout=timeout, check=False).returncode
    except FileNotFoundError:
        raise Problem(f"`{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — this command runs the plugin's assets.py through the front door") from None
    except subprocess.TimeoutExpired:
        return -1


def assets_steps(job: dict, leaf: dict) -> list[list[str]]:
    """The front-door argv of each plugin step for one leaf, as data: every
    path wiki-relative, the page's url an argv element of its own."""
    directory, item = leaf["dir"], leaf["item"]
    manifest = f"{directory}/assets.json"
    detect = ["run", ASSETS_SCRIPT, "detect", f"{directory}/page.html", "--base-url", item, "--out", manifest]
    download = ["run", ASSETS_SCRIPT, "download", manifest, "--dest", f"{RAW}/{job['slug']}/assets"]
    if job["assets"] == "reference":
        download += ["--mode", "reference"]
    else:
        download += ["--referer", item, *(["--audio-only"] if job["assets"] == "download-audio" else [])]
    return [detect, download]


def cmd_assets(args) -> int:
    capture = _capture_dir(args.capture_dir)
    job = job_facts(capture)
    root = wiki_root(capture, job["capture_dir"])
    plan = _plan_of(capture)
    leaf = _leaf_at(plan, args.leaf)
    directory = root / leaf["dir"]
    if not (directory / "page.html").is_file():
        raise Problem(f"{leaf['dir']}/page.html is not there — render the page first")
    detect, download = assets_steps(job, leaf)
    if (directory / "net.json").is_file():
        detect += ["--network-log", f"{leaf['dir']}/net.json"]
    answer = {"n": args.leaf, "dir": leaf["dir"], "mode": job["assets"], "video": "none"}
    answer["detect"] = _spawn(root, *detect)
    if answer["detect"] != 0:
        raise Problem(f"assets.py detect exited {answer['detect']} on {leaf['dir']}")
    patched = subprocess.run([sys.executable, str(CAPTURER), "patch-assets", str(directory / "assets.json"), "--meta", str(directory / "meta.json")],
                             stdout=sys.stderr, check=False)
    if patched.returncode != 0:
        raise Problem(f"patch-assets exited {patched.returncode} on {leaf['dir']}")
    hard_stop = plan.get("hard_stop_epoch")
    left = max(hard_stop - time.time(), 1) if isinstance(hard_stop, (int, float)) else None
    answer["download"] = _spawn(root, *download, timeout=left)
    meta = clean_meta(_read_json(directory / "meta.json") or {})
    for asset in _read_json(directory / "assets.json") or []:
        if isinstance(asset, dict) and meta["stream_url"] and asset.get("src_url") == meta["stream_url"]:
            answer["video"] = fold(asset.get("status") or "pending")
    if answer["download"] == -1:
        answer["video"] = "timed_out"
    print(json.dumps({**answer, **clock(plan)}))
    wanted = job["assets"] != "reference" and meta["stream_url"]
    return 3 if wanted and answer["video"] != "downloaded" else 0


def cmd_record(args) -> int:
    capture = _capture_dir(args.capture_dir)
    job = job_facts(capture)
    root = wiki_root(capture, job["capture_dir"])
    plan = _plan_of(capture)
    if args.leaf is not None:
        row = _leaf_at(plan, args.leaf)
        leaf = (root / row["dir"]).resolve()
    elif args.leaf_dir:
        leaf = Path(args.leaf_dir).resolve()
        row = _leaf_of(plan, root, leaf)
    else:
        raise Problem("record needs --leaf <n> (the index `next` printed)")
    item = (row or {}).get("item") or (job["item"] if leaf == capture else None)
    if args.url:  # a hand run, on a page `plan.json` does not name
        item = _normal(args.url, ())
        if item is None:
            raise Problem("--url: not an http(s) url this unit will carry")
    if not item:
        raise Problem(f"{leaf} is not a leaf {PLAN_NAME} names; run `plan` first")
    html_file = leaf / HTML_NAME
    if not html_file.is_file():
        raise Problem(f"{html_file} is not there — render the page first")
    raw_meta = _read_json(leaf / "meta.json")
    meta = clean_meta(raw_meta if isinstance(raw_meta, dict) else {})
    if meta["status"] in (404, 410):
        raise Problem(
            f"the source answered {meta['status']} for leaf {leaf.name}: that is not a capture. On a refresh ticket "
            f"`report --gone`; otherwise `report --missing-leaf error <n>`"
        )
    if args.no_media or job["refresh"]:
        # A refresh is of the PAGE: a second transcript stub for a video the
        # wiki already transcribed would overwrite the transcript it holds.
        source = None
    elif args.media_file:
        source = Path(args.media_file).resolve()
    else:
        source = downloaded_media(leaf, meta)
    if source is not None and (not source.is_file() or source.suffix.lower() not in MEDIA_SUFFIXES):
        # Before anything is written: a leaf half-made is a leaf `report` would list.
        raise Problem(f"{source} is not a media file this unit can queue for transcription ({', '.join(sorted(MEDIA_SUFFIXES))})")
    # The venue's title, made a name the host's filename rule holds. The site's
    # own `title_selector` is the PROCESS step's — this is the render's guess
    # (the rendered h2, then `<title>`), settled across the run by `report`.
    segment = next((unquote(bit) for bit in reversed(urlsplit(item).path.split("/")) if bit), "")
    title = safe_title(fold(meta["title"]) or segment)
    # The render's own time, else the rendered file's: this may run long after.
    fetched_at, fetched_from = (meta["fetched_at"], "meta.json") if meta["fetched_at"] else (_iso(html_file.stat().st_mtime), "page.html mtime")
    record = capture_record(slug=job["slug"], item=item, title=title, body=HTML_NAME,
                            content_type="text/html", fetched_at=fetched_at)
    _write_json(leaf / CAPTURE_NAME, record)
    # The venue values the process step puts on the page, each already checked
    # against the one shape it can honestly have: the worker copies them from
    # here rather than reading `meta.json` raw.
    video = {key: meta[key] for key in ("embed_url", "stream_url", "player_url", "mux_playback_id")}
    out = {"item": item, "dir": str(leaf.relative_to(root)), "title": title, "fetched_at_from": fetched_from,
           "media": None, "video": video}
    if source is not None:
        # Beside the bytes it belongs to, hard-linked where the filesystem
        # allows one — a lesson video is hundreds of megabytes and the job's
        # asset store already holds it.
        body = f"{MEDIA_STEM}{source.suffix.lower()}"
        _place(source, leaf / body)
        out["media"] = body
    now = clock(plan)
    print(json.dumps({"captured": out, **now}, indent=1))
    return STOP if now["stop"] else 0


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
# KEEP IN SYNC with the host. A page is filed under its TITLE: the filename is
# `title.strip() + ".md"` and nothing else (llm-wiki-ops
# `commands/page/note.py::filename_for`) — nothing folded, nothing dropped; a
# title carrying one of `ILLEGAL` or a control character is REFUSED, not
# altered. So two leaves of one run whose titles differ only in outer
# whitespace make ONE filename, and `page create` answers the second with
# `already exists` — a lesson edited over another lesson. On a filesystem that
# folds case (macOS, Windows) so are two that differ only in that. `page_key`
# folds both: a
# needless qualifier costs nothing, an overwritten page is lost. NOT folded:
# Unicode form (NFC/NFD), which the same filesystems also fold — stdlib has it
# only in `unicodedata`, and one venue spelling one title two ways is rare.
# Duplicated per unit on purpose — units install one by one, nothing is shared.

TITLE_ILLEGAL = '/\\:*?"<>|'  # `page/note.py::ILLEGAL`
QUALIFIER_MAX = 60


def page_key(title: str) -> str:
    """What two titles share when they make one page file: the host strips, a
    case-insensitive filesystem folds case, and APFS folds Unicode form too —
    `é` composed and `e` + combining accent are one name there."""
    return unicodedata.normalize("NFC", title.strip()).casefold()


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
    `/learn/module-2/intro`) — then the hash of its item."""
    path = urlsplit(leaf["item"]).path
    bits = [unquote(bit) for bit in path.split("/") if bit]
    return [*reversed(bits), hashlib.sha1(leaf["item"].encode("utf-8")).hexdigest()[:8]]


def settle_titles(root: Path, planned: list[dict]) -> None:
    """One page per leaf: in plan order the first leaf to make a filename keeps
    its title, and a later one is retitled in its own `capture.json`.

    In `report` and not in `record`, because `record` sees one leaf, leaves are
    captured in whatever order the worker reaches them, and `report` runs
    last, over all of them, before the process step names a single page. A
    title already qualified is free on the next pass, so a second report
    renames nothing twice.

    The lesson's transcript stub is `<this title> (video)`, written by the
    process step off the settled title, so it follows its page by itself.
    """
    taken: dict = {}
    for leaf in planned:
        directory = root / leaf["dir"]
        record = _read_json(directory / CAPTURE_NAME)
        if held(root, leaf) is None:
            continue
        title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
        on_disk = title or Path(record["body"]).stem
        final = unique_title(on_disk, leaf_qualifiers(leaf), taken)
        if final != on_disk:
            record["title"] = final
            _write_json(directory / CAPTURE_NAME, record)


# ------------------------------------------------------------ page names, in bytes
#
# NOT part of the shared block above. `safe_title` caps a title at
# `TITLE_MAX_BYTES`, and then `settle_titles` appends a qualifier and the
# process step a ` (video)` — which can carry the FILENAME past what a
# filesystem holds (255 bytes, `.md` included; the host checks no length and
# the write dies `File name too long`). So after the titles are settled, one
# that is over the cap is cut in its BASE, the qualifier kept whole.

FILENAME_TITLE_MAX_BYTES = 232  # + ` (video).md`, and room for the `_versions/<page>.<YYYY-MM-DD>.md` copy a refresh makes


def _cut_to_bytes(text: str, budget: int) -> str:
    while text and len(text.encode("utf-8")) > budget:
        text = text[:-1]
    return text


def fit_title(final: str, bases, *, unique: str = "") -> str:
    """`final` within the byte cap: its base — the longest of `bases` it starts
    with — cut, everything after it (qualifier, ` (video)`) kept. `unique` is
    worked into the cut base where the plain cut is already another leaf's."""
    if len(final.encode("utf-8")) <= FILENAME_TITLE_MAX_BYTES:
        return final
    base = max((b for b in bases if b and final.startswith(b)), key=len, default=final)
    suffix = final[len(base):]
    tail = f"… ({unique})" if unique else "…"
    budget = FILENAME_TITLE_MAX_BYTES - len(suffix.encode("utf-8")) - len(tail.encode("utf-8"))
    if budget < 1:  # a suffix that alone is over the cap: nothing left to keep whole
        return _cut_to_bytes(final, FILENAME_TITLE_MAX_BYTES - 3).rstrip(" .-(") + "…"
    return _cut_to_bytes(base, budget).rstrip(" .-…") + tail + suffix


def titles_on_disk(root: Path, planned: list[dict]) -> dict:
    found = {}
    for leaf in planned:
        record = _read_json(root / leaf["dir"] / CAPTURE_NAME)
        if held(root, leaf) is not None and isinstance(record.get("title"), str):
            found[leaf["dir"]] = record["title"]
    return found


def fit_titles(root: Path, planned: list[dict], before: dict) -> None:
    """Every settled title back under the byte cap. `before` is what each leaf's
    `capture.json` said BEFORE `settle_titles` ran — the base a qualifier was
    appended to."""
    after = titles_on_disk(root, planned)
    taken = {page_key(title) for title in after.values()}
    for leaf in planned:
        final = after.get(leaf["dir"])
        if final is None or len(final.encode("utf-8")) <= FILENAME_TITLE_MAX_BYTES:
            continue
        bases = [(before.get(leaf["dir"]) or "").strip()]
        fitted = fit_title(final, bases)
        if page_key(fitted) in taken:
            fitted = fit_title(final, bases, unique=hashlib.sha1(leaf["dir"].encode("utf-8")).hexdigest()[:8])
        taken.add(page_key(fitted))
        record = _read_json(root / leaf["dir"] / CAPTURE_NAME)
        record["title"] = fitted
        _write_json(root / leaf["dir"] / CAPTURE_NAME, record)


def cmd_report(args) -> int:
    capture = _capture_dir(args.capture_dir)
    ticket_id = args.ticket
    if not ticket_id:
        raise Problem("no --ticket")
    job = job_facts(capture, args)
    job["ticket"] = ticket_id
    root = wiki_root(capture, job["capture_dir"]) if job.get("capture_dir") else None
    plan = _plan_of(capture)
    planned = plan.get("leaves") or []

    if args.written_from or args.skipped:
        # A PROCESS report: the pages are already under `dest`, so there is
        # nothing here to settle and no plan to read.
        written = []
        if args.written_from:
            loaded = _read_json(capture / args.written_from)
            if not isinstance(loaded, list):
                raise Problem(f"--written-from {args.written_from}: not a JSON list of pages, in {capture}")
            written = [str(w) for w in loaded]
        status, reason = process_update(written=written, reason=fold(args.reason) or None, failed=args.failed, skipped=args.skipped)
        code = post_update(ticket_id, "process", status, reason=reason, written_from=args.written_from if written else None)
        print(json.dumps({"status": status, "reason": reason, "written": len(written)}))
        if code:
            return 2
        return 0 if status != "failed" else 1
    if args.gone and not job["refresh"]:
        raise Problem("--gone is a refresh ticket's answer alone (the ticket's own `refresh: true`)")
    missing = []
    for why, n in args.missing_leaf or []:
        if why not in WHY or not n.isdigit():
            raise Problem(f"--missing-leaf {why} {n}: <why> is one of {', '.join(WHY)}, <n> an index in {PLAN_NAME}")
        leaf = _leaf_at(plan, int(n))
        # The page's video where the render resolved one — a Mux host the jail
        # refused is the usual miss — else the page itself.
        url = clean_meta(_read_json(root / leaf["dir"] / "meta.json") or {})["stream_url"] if (why == "denied" and root is not None) else None
        url = url or leaf["item"]
        missing.append((urlsplit(url).netloc.lower(), url, why))
    for why, host in args.missing_host or []:
        host = host.lower()
        if why not in WHY or not _HOST.match(host):
            raise Problem(f"--missing-host {why} <host>: <why> is one of {', '.join(WHY)}, <host> a bare hostname")
        missing.append((host, f"https://{host}/", why))
    if not (args.failed or args.gone) and root is not None:
        before = titles_on_disk(root, planned)
        settle_titles(root, planned)
        fit_titles(root, planned, before)
    captured = [row for row in (held(root, leaf) for leaf in planned) if row] if root is not None else []
    update = build_update(planned=planned, captured=captured, skipped=plan.get("skipped") or [],
                          missing=missing, reason=fold(args.reason) or None, failed=args.failed, gone=args.gone, job=job)
    code = post_update(ticket_id, "harvest", update["status"], reason=update["reason"],
                       captured=[row["dir"] for row in update["captured"]], missing=missing)
    print(json.dumps({"status": update["status"], "reason": update["reason"], "captured": len(update["captured"]), **clock(plan)}))
    if code:
        return 2
    return 0 if update["status"] != "failed" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    capture_help = "the ticket's `capture_dir`, verbatim: WIKI-RELATIVE (`run` starts this script at the wiki root)"

    p = sub.add_parser("plan", help="open the ticket, filter the enumerated urls and name one capture directory per page")
    p.add_argument("capture_dir", help=capture_help)
    p.add_argument("--ticket", default=None, help="the ticket id, opened for the rest of these defaults; REQUIRED unless every other flag names a hand run's inputs")
    p.add_argument("--urls", action="append", help="sitemap.xml, a JSON list, or one url per line, wiki-relative; `-` is stdin (repeatable)")
    p.add_argument("--limit", type=int, default=None,
                   help=f"attempt at most N pages this run, newest first (default {DOWNLOAD_LIMIT} where the job downloads, none for `assets: reference`; 0 = none)")
    p.add_argument("--budget-minutes", type=float, default=BUDGET_MINUTES,
                   help=f"no new leaf is started this long after the spawn (default {BUDGET_MINUTES}; the slice is killed at 30)")
    p.add_argument("--sites", type=Path, default=None, help="default: this unit's references/sites.json")
    for flag in ("--slug", "--target", "--min-date"):
        p.add_argument(flag, default=None, help="hand run with no --ticket: the ticket's own value")
    p.add_argument("--scope", choices=["page", "section", "domain"], default=None, help="hand run: harvest.scope")
    p.add_argument("--assets", choices=["reference", "download", "download-audio"], default=None, help="hand run: harvest.assets")
    p.add_argument("--exclude-url", action="append", default=[], help="hand run: one more harvest.exclude_urls glob")
    p.set_defaults(fn=cmd_plan)

    n = sub.add_parser("next", help="the next page to capture, or stop (exit 5) once the deadline has passed")
    n.add_argument("capture_dir", help=capture_help)
    n.set_defaults(fn=cmd_next)

    a = sub.add_parser("assets", help="detect, patch and (by harvest.assets) download one leaf's assets — by argv, never a shell")
    a.add_argument("capture_dir", help=capture_help)
    a.add_argument("--leaf", type=int, required=True, help="the page's index in plan.json's leaves[]")
    a.set_defaults(fn=cmd_assets)

    g = sub.add_parser("record", help="the leaf's flat capture.json, and the downloaded video beside it")
    g.add_argument("capture_dir", help=capture_help)
    g.add_argument("leaf_dir", nargs="?", default=None, help="a hand run: the leaf's directory, in place of --leaf")
    g.add_argument("--leaf", type=int, default=None, help="the page's index in plan.json's leaves[] — what `next` printed")
    g.add_argument("--url", default=None, help="a HAND run on a page plan.json does not name; never a url read off a venue")
    g.add_argument("--media-file", default=None, help="the downloaded video/audio, when the leaf's assets.json does not name it")
    g.add_argument("--no-media", action="store_true", help="record the page's bytes alone, even where the video was downloaded")
    g.set_defaults(fn=cmd_record)

    r = sub.add_parser("report", help="post `tickets update` — after every leaf, and last")
    r.add_argument("capture_dir", help=capture_help)
    r.add_argument("--ticket", required=True, help="the ticket id")
    r.add_argument("--missing-leaf", nargs=2, action="append", metavar=("WHY", "N"), help="a planned page (or, for `denied`, its video) that could not be reached")
    r.add_argument("--missing-host", nargs=2, action="append", metavar=("WHY", "HOST"), help="a bare hostname the jail refused or that never answered")
    r.add_argument("--reason", default=None)
    r.add_argument("--failed", action="store_true", help="nothing usable landed; say why with --reason")
    r.add_argument("--gone", action="store_true", help="a refresh ticket whose source answered 404 or 410")
    r.add_argument("--written-from", default=None, metavar="FILE",
                   help="a JSON list of wiki-relative pages the PROCESS step wrote, inside the capture dir: makes this a process report")
    r.add_argument("--skipped", action="store_true", help="the process report for a capture that earned no page; say why with --reason")
    for flag in ("--slug", "--target"):
        r.add_argument(flag, default=None, help="hand run with neither --ticket nor plan.json: the ticket's own value")
    r.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Problem as exc:
        print(f"leaves.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
