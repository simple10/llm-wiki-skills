#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Plan, record and report one Circle harvest ticket — the deterministic half.

platform: circle
scope: platform-general. No browser, no network of its own, stdlib only:
`plan`, `record` and `report` are pure functions of files already in the job's
`_raw/<slug>/` slice; `detect` only starts another script.

One harvest ticket walks a whole section. `capture_lesson.py` fetches the
bytes; this script decides WHICH lessons, WHERE each one lands, writes the
record that names those bytes, and writes the one report that leaves the
slice. The PAGE is the process step's, off those same bytes.

  section_plan.py plan   <capture_dir> [--target URL] [--slug SLUG]
                         [--scope page|section|domain] [--ticket ID]
                         [--budget-s SECONDS]
  section_plan.py detect <capture_dir> --leaf N
  section_plan.py record <capture_dir> (--leaf N | <lesson-url>)
  section_plan.py report <capture_dir> [--auth-expired] [--gone] [--reason TEXT]
                         [--missing-leaf N <why>]... [--missing-host HOST <why>]...
                         [--missing <host> <url> <why>]...
                         [--stage harvest|process] [--written PATH]... [--ticket ID]

`<capture_dir>` is the TICKET's capture directory — the one holding
`ticket.json` and the root capture (`meta.json`, `page.html`). It is REQUIRED
and WIKI-RELATIVE: `llm-wiki-ops run` starts a script at the wiki root, not in
the directory the worker stands in, so the `capture_dir` value off
`ticket.json`, verbatim, is the path to pass. A directory with no `ticket.json`
is refused unless the hand-run flags (`--target --slug`, or `--ticket` for
`report`) stand in for it.

A lesson is named by its NUMBER — `--leaf N`, the leaf's `order` in
`plan.json` — never by its url. A url is venue data; the worker's command line
is a shell. `plan` refuses any url outside a conservative character set
(`unsafe_url`), and no documented command takes one. The positional url of
`record` and the three-part `--missing` are for hand runs.

plan    reads `ticket.json` (target, slug, `harvest.scope`, `harvest.access`,
        `harvest.exclude_urls`, `known[]`) and the root capture's `meta.json`
        (`discovered_lesson_links`, in the order the course lists them), and
        writes `plan.json`: the ordered leaf work list, each with the
        directory it is captured into, plus every link dropped and why.
        The flags stand in for `ticket.json` on a hand run. FIRST it removes
        a stale `report.json` — the capture dir is stable across pulls, and a
        respawn must never be read as a success it did not have. It stamps a
        `deadline`: `ticket.json`'s mtime (the spawner rewrites the file on
        every dispatch, so that IS the spawn) plus `--budget-s` (default 1500
        of the slice's 1800 seconds). A leaf whose `capture.json` already
        names it and a body on disk is marked `landed: true` — a slice killed
        at the cap left it there, and the next one skips it. A REFRESH ticket
        (`ticket.json` `refresh: true`) plans exactly its `resource`, as the
        root leaf, whatever `known[]` and scope say, and clears that
        directory's old record so the page is really fetched again.
detect  the plugin's `assets.py detect` over one leaf, with the leaf's url
        as `--base-url` taken from `plan.json` — started through the front
        door, as an argument list, never through a shell.
record  once `capture_lesson.py --leaf` has left a leaf's bytes: writes its
        FLAT `capture.json` — `slug`, `item`, `title`, `body` (`page.html`, as
        the venue served it), `content_type`, `fetched_at` and nothing else —
        and `facts.json` beside it, holding what the SIDEBAR knew about this
        lesson (course, section, duration, the venue's own title). A leaf's
        own capture carries none of those, and the process step cannot reach
        the plan. The record's `title` is `safe_title(venue title)` — a legal
        FILENAME, because the host names the page file from it. Safe to
        re-run. It renders no page.
report  Cheap, and safe to run after EVERY leaf as well as last: a slice
        killed at the cap then still leaves a truthful `report.json` behind.
        Writes `report.json` in the ticket's capture dir. `captured[]`
        is derived, never claimed — a leaf counts when its `capture.json`
        names a body file that is there. First it settles the titles: the
        extractor files a page under its TITLE and overwrites what is there,
        so a later lesson whose title makes the filename an earlier one made
        is retitled `<title> (<section name>)` — in its `capture.json` and in
        `captured[]` (see "page names" below).

Scope is the ticket's own: `page` keeps only the target; `section` keeps the
lessons under the target's path (so the target must be the space root,
`/c/<slug>`); `domain` keeps every lesson on the target's host. Nothing
downstream filters a second time — what this plans is what gets fetched.

Leaf directories follow the host's own namer, which has no verb, so the same
shape is composed here: `_raw/<slug>/<slugified url path>--<first 8 hex of
sha1(url)>`. The target itself, where it is a leaf, lands in the ticket's own
`capture_dir`, which is never composed.

Given `--stage process` (or any `--written`), `report` is the PROCESS step's
instead: `written[]` is the pages that step wrote, `captured[]` is empty, and
no plan is read. The step is always told, never read off
`ticket.json`: a single-item job's process ticket has the SAME capture dir as
its harvest ticket, and the file lands there under one name.

Exit status: `plan`, `detect` and `record` exit 0 on success, 1 when
an input is missing or unusable (said on stderr). `record` exits 3 — and says
`"stop": true` — when the leaf WAS recorded but the plan's deadline has passed:
start no further lesson, run `report`, exit. `detect` passes on the exit
status of the script it starts (127: it could not be started). `report` writes
the report and exits 0 for `ok`, `partial`, `skipped` and `gone`, 1 for
`failed`, 2 for an argument it refuses (nothing written).

History:
  2026-09-19  created — the rebuilt pipeline fans nothing out: one ticket
              captures every leaf, and the unit applies scope itself.
  2026-09-19  `report` settles titles first: two lessons of one run sharing a
              title ("Introduction" in every section) were ONE page under
              `dest`, the second silently overwriting the first. `plan` reads
              each section's name off the sidebar for the qualifier.
  2026-09-19  review fixes: titles are legal filenames (`safe_title`); leaves
              are named by number and an unsafe url never reaches a plan; a
              deadline, `landed` leaves and a re-runnable report make "resume"
              real; a refresh ticket plans exactly its resource; fact values
              are folded to one line; `www.` is not a second host.
  2026-09-19  harvest and process split apart again: harvest leaves BYTES and
              a flat `capture.json`, the page is the process step's, and
              `report --stage process --written` is its report.
"""

import unicodedata
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

PLAN_V = 1
REPORT_V = 1
CAPTURE_V = 1
TICKET_NAME = "ticket.json"
META_NAME = "meta.json"
PLAN_NAME = "plan.json"
CAPTURE_NAME = "capture.json"
FACTS_NAME = "facts.json"
REPORT_NAME = "report.json"
BODY_HTML = "page.html"
ASSETS_NAME = "assets.json"

RAW_DIRNAME = "_raw"
SCOPES = ("page", "section", "domain")
DEFAULT_SCOPE = "section"  # the unit manifest's `watch.defaults.harvest.scope`
WHYS = ("denied", "timeout", "auth", "error")

# The slice cap is the host's (`schedule/runner/slice.py::SLICE_CAP_SECONDS`,
# 1800): a slice that outruns it is killed and its ticket failed with NO
# report. The budget is what this unit lets itself start new lessons in; the
# rest is for the lesson in hand and the report.
SLICE_CAP_SECONDS = 1800
DEFAULT_BUDGET_SECONDS = 1500
EXIT_STOP = 3  # `record`: recorded, and the deadline has passed
GONE_STATUSES = (404, 410)
STAGES = ("harvest", "process")

LESSON_RE = re.compile(r"/lessons/([^/?#]+)")
SECTION_RE = re.compile(r"/sections/([^/?#]+)")
SPACE_RE = re.compile(r"^/c/([^/?#]+)")
DURATION_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")
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


# What a PLANNED url may be made of. A sidebar href is venue data, and a plan
# is read by an agent with a shell: `;`, `$`, `(`, `&`, a quote, a backtick, a
# space, a brace never reach one. Deliberately narrower than RFC 3986 — every
# observed Circle lesson url is `/c/<slug>/sections/<id>/lessons/<id>`, and a
# url this refuses is DROPPED where a person can see it (`unsafe_url`), which
# costs one lesson; a url this wrongly admits costs the machine.
_URL_SAFE = re.compile(r"\Ahttps?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:[/?][A-Za-z0-9._~/?=%+,:@-]*)?\Z")
_HOST_SAFE = re.compile(r"\A[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
URL_MAX = 2000


def clean_url(url: str) -> str | None:
    """A SAFE http(s) url with its fragment dropped, or None for anything else:
    not text, not parseable, another scheme, userinfo, or a character outside
    `_URL_SAFE`."""
    if not isinstance(url, str) or any(ch.isspace() or not ch.isprintable() for ch in url.strip()):
        return None  # `urlsplit` silently DROPS a tab or a newline inside a url; refuse it instead
    try:
        parts = urlsplit(url.strip())
        host, _port = parts.hostname, parts.port  # `.port` raises on a bad one
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or "@" in parts.netloc:
        return None
    cleaned = urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))
    if len(cleaned) > URL_MAX or not _URL_SAFE.match(cleaned):
        return None
    return cleaned


def redacted(text, cap: int = 200) -> str:
    """A refused url as it is written down: every character a plan would not
    admit replaced, so the record of a hostile href is not itself one."""
    return re.sub(r"[^A-Za-z0-9._~/?=%+,:@-]", "_", str(text))[:cap]


def same_key(url: str) -> str:
    """What two spellings of one page share: host lowercased, no trailing
    slash, no fragment. Used to compare, never to rewrite."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), (parts.netloc or "").lower(), parts.path.rstrip("/"), parts.query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def bare_host(url: str) -> str:
    """`www.` is not a second host: a job declared on the apex still owns the
    lessons the venue links as `www.`, and the other way round."""
    host = host_of(url)
    return host[4:] if host.startswith("www.") else host


def in_scope(url: str, target: str, scope: str) -> bool:
    if scope == "page":
        return same_key(url) == same_key(target)
    if bare_host(url) != bare_host(target):
        return False
    if scope == "domain":
        return True
    prefix = urlsplit(target).path.rstrip("/")
    path = urlsplit(url).path
    return path == prefix or path.startswith(prefix + "/")


def excluded(url: str, exclude_urls) -> bool:
    """THIS UNIT'S READING — the host defines no matching rule for
    `harvest.exclude_urls`: an entry drops the url it names and everything
    UNDER it, by whole path segments (`/c/a` drops `/c/a/…`, never `/c/ab`).
    Scheme, `www.`, case of the host, a trailing slash and a query are not
    told apart; an entry that opens with `/` is a path on any host."""
    parts = urlsplit(url)
    host, path = bare_host(url), parts.path.rstrip("/")
    for entry in exclude_urls or []:
        if not isinstance(entry, str) or not entry.strip():
            continue
        entry = entry.strip()
        if entry.startswith("/"):
            entry_host, base = None, entry.split("?")[0].split("#")[0].rstrip("/")
        else:
            try:
                named = urlsplit(entry if "://" in entry else "https://" + entry)
            except ValueError:
                continue
            entry_host, base = bare_host(urlunsplit(named)), named.path.rstrip("/")
        if entry_host is not None and entry_host != host:
            continue
        if path == base or path.startswith(base + "/"):
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


# --- venue text ---------------------------------------------------------------


def fold(text) -> str:
    """Venue text on ONE line: every newline, tab, control or other
    non-printing character becomes a space, runs of space collapse. What is
    written as `- **Key**: value` — or as a heading — cannot then open a
    heading, a rule or a fence of its own."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())


# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "\u2019", "'": "\u2019", "<": "(", ">": ")"}
TITLE_MAX = 120  # characters
TITLE_MAX_BYTES = 200  # UTF-8 bytes: the host checks no length, and a filename is capped in BYTES (255 on
# ext4/APFS) with `.md` appended — 100 CJK characters is 300 bytes and the REAL extractor
# dies `OSError: [Errno 36] File name too long` (measured, channel-youtube)
UNTITLED = "Untitled lesson"


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


def valid_date(text) -> str | None:
    """`YYYY-MM-DD` that is a real day, or None."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        time.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


# --- plan ---------------------------------------------------------------------


def link_facts(text: str) -> tuple[str | None, str | None]:
    """`(title, duration)` off a sidebar link's text — "Lesson title\\n\\n04:07".
    A text that filled the capture's cap may be cut mid-title, so it names no
    title."""
    lines = [fold(line) for line in (text or "").splitlines() if fold(line)]
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
        raise ValueError("no usable target: none in ticket.json and no --target, or not a safe http(s) url")
    if not isinstance(slug, str) or not slug:
        raise ValueError("no slug: none in ticket.json and no --slug")
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    scope = harvest.get("scope") or DEFAULT_SCOPE
    if scope not in SCOPES:
        raise ValueError(f"harvest.scope={scope!r} — one of {', '.join(SCOPES)}")
    access = harvest.get("access") or "licensed"
    held = known_keys(ticket.get("known"))

    # A REFRESH ticket re-fetches ONE page the job already holds
    # (`pipeline/dispatch.py::mint_refresh`: `target` IS the resource, and the
    # capture dir is that page's own). `known[]` names it by design, and the
    # rest of the section is another ticket's: plan exactly it.
    refresh = ticket.get("refresh") is True
    if refresh:
        target = clean_url(ticket.get("resource") or "") or target

    links = [link for link in meta.get("discovered_lesson_links") or [] if isinstance(link, dict)]
    candidates = []  # (url, text, is_root)
    if refresh or scope == "page" or is_lesson(target):
        # The page lists ITSELF in its own sidebar, and that text is the title
        # the first pull filed it under: a refresh must not rename the page.
        own = next((link.get("text") or "" for link in links
                    if clean_url(link.get("href")) and same_key(clean_url(link["href"])) == same_key(target)), "")
        candidates.append((target, own, True))
    if scope != "page" and not refresh:
        candidates += [(link.get("href"), link.get("text") or "", False) for link in links]

    leaves, dropped, seen, sections = [], [], set(), {}
    for raw, text, is_root in candidates:
        url = clean_url(raw)
        if url is None:
            # Not http(s), not parseable, or a character no plan admits: it
            # never becomes a leaf, and what is written down is not the href.
            if isinstance(raw, str) and raw.strip():
                dropped.append({"url": redacted(raw.strip()), "why": "unsafe_url"})
            continue
        key = same_key(url)
        if not is_lesson(url):
            # A bare `/sections/<id>` link is the sidebar's section header: no
            # leaf, but its text is the section's NAME — what tells two
            # "Introduction" lessons apart (see "page names").
            header = SECTION_RE.search(urlsplit(url).path)
            if header and link_facts(text)[0]:
                sections.setdefault(header.group(1), link_facts(text)[0])
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
        if refresh:
            why = None
        elif not is_root and not is_lesson(url):
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
                # The SAFE form: it is what the page will be called, and what a
                # lesson that never lands still reserves when titles are settled.
                "title": safe_title(title) if title else None,
                "source_title": title if title and title != safe_title(title) else None,
                "duration": duration,
                "root": is_root,
                "landed": False,
            }
        )

    for leaf in leaves:
        within = SECTION_RE.search(urlsplit(leaf["url"]).path)
        leaf["section"] = sections.get(within.group(1)) if within else None

    space = SPACE_RE.match(urlsplit(target).path)
    return {
        "v": PLAN_V,
        "ticket": ticket.get("ticket"),
        "slug": slug,
        "target": target,
        "capture_dir": capture_rel,
        "scope": scope,
        "access": access,
        "refresh": refresh,
        # Circle declares no per-lesson date anywhere this unit has seen, so a
        # `min_date` cannot be applied; it rides the plan and `report` says so.
        "min_date": valid_date(ticket.get("min_date")),
        "deadline": None,  # `cmd_plan` stamps it: it is a fact of the disk, not of the inputs
        "deadline_epoch": None,
        "space": space.group(1) if space else None,
        "course": (meta.get("title") or "").strip() or None,
        "leaves": leaves,
        "dropped": dropped,
    }


# --- record -------------------------------------------------------------------


def leaf_titles(leaf: dict, meta: dict) -> tuple[str, str | None]:
    """`(the page's title — a legal filename, the venue's own title)`. Harvest
    holds bytes, not a rendered body: the sidebar's text names the lesson, and
    where the sidebar's link text was cut short, the browser's own title does.
    The body's heading is the process step's fallback, once there is one."""
    venue = fold(leaf.get("source_title") or leaf.get("title")) or fold(meta.get("title")) or None
    return safe_title(venue, fallback=UNTITLED), venue


def fetched_at_of(path: Path) -> str:
    """When the capture wrote `meta.json` — that IS the fetch time, and it
    keeps a re-run over the same capture byte-identical."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = time.time()
    return iso(stamp)


def leaf_path(capture_dir: Path, leaf: dict) -> Path:
    """Where a planned leaf is on disk. Every leaf is `_raw/<slug>/<one>`, so
    each is the ticket directory itself or a sibling of it."""
    return capture_dir if leaf.get("root") else capture_dir.parent / leaf["dir"].rsplit("/", 1)[-1]


def find_leaf(plan: dict, url: str) -> dict | None:
    key = same_key(clean_url(url) or url)
    return next((leaf for leaf in plan.get("leaves") or [] if same_key(leaf["url"]) == key), None)


def leaf_numbered(plan: dict, number) -> dict | None:
    """The leaf whose `order` is `number` — how every documented command names
    a lesson, so no url is ever typed."""
    return next((leaf for leaf in plan.get("leaves") or [] if leaf.get("order") == number), None)


def record_leaf(capture_dir: Path, plan: dict, leaf: dict) -> dict:
    """The leaf's flat `capture.json`, naming the bytes the venue served, and
    `facts.json` beside it — what the SIDEBAR knew about this lesson (the
    course, its section, its duration), which the leaf's own capture does not
    carry and the process step cannot otherwise reach."""
    directory = leaf_path(capture_dir, leaf)
    if not (directory / BODY_HTML).is_file():
        raise ValueError(f"{directory / BODY_HTML}: nothing captured here — run capture_lesson.py --leaf first")
    meta = read_json(directory / META_NAME)
    title, venue = leaf_titles(leaf, meta)
    write_json(
        directory / FACTS_NAME,
        {
            "course": fold(plan.get("course")) or None,
            "space": plan.get("space"),
            "section": fold(leaf.get("section")) or None,
            "duration": fold(leaf.get("duration")) or None,
            "source_title": venue,
        },
    )
    record = {
        "v": CAPTURE_V,
        "slug": plan["slug"],
        "item": leaf["url"],
        "title": title,
        "body": BODY_HTML,
        "content_type": "text/html",
        "fetched_at": fetched_at_of(directory / META_NAME),
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


def landed_as(directory: Path, leaf: dict) -> bool:
    """Landed, AND as this lesson: the record names the leaf's own url."""
    record = landed(directory)
    return bool(record) and isinstance(record.get("item"), str) and same_key(record["item"]) == same_key(leaf["url"])


# --- the deadline ---------------------------------------------------------------
#
# A slice that outruns the cap is killed and its ticket failed `slice_cap` with
# NO report (`schedule/broker.py::_kill`, `reference pipeline`): nothing is
# minted from what it captured. So the unit stops ITSELF, early, and what makes
# the next slice cheaper is on disk, not in a report: a landed leaf is skipped.


def spawn_time(capture_dir: Path) -> float | None:
    """When this slice was spawned: `ticket.json`'s mtime. The ticket ID is the
    same on every pull, but the spawner rewrites the file on every dispatch
    (`pipeline/dispatch.py::write_ticket`, called from `start_slice`). None on a
    hand run, which nothing kills."""
    try:
        return (capture_dir / TICKET_NAME).stat().st_mtime
    except OSError:
        return None


def iso(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def past_deadline(plan: dict, now: float | None = None) -> bool:
    deadline = plan.get("deadline_epoch")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        return False
    return (time.time() if now is None else now) >= deadline


# --- page names ---------------------------------------------------------------
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
    """What tells this lesson from a namesake, most meaningful first: its
    section's name off the sidebar, and last the hash of its url. The lesson's
    "Topic N of M" is in the page BODY, which harvest does not render — and
    titles are settled here, before any body exists."""
    return [leaf.get("section"), hashlib.sha1(leaf["url"].encode("utf-8")).hexdigest()[:8]]


def fitted_title(title: str, qualifiers, taken: dict) -> str:
    """`unique_title`, with the BYTE cap re-applied after qualifying: a
    qualifier can push a title `safe_title` had capped back over what a
    filename holds. The BASE is trimmed and the qualifier kept — it is the
    part that tells the two pages apart. `unique_title` itself is untouched:
    it answers on a copy of `taken`, and only an answer that does not fit is
    rebuilt here — same qualifier, shorter base — and claimed in its place. A
    title this fitted is free, and fits, on the next pass: it comes back as it
    is."""
    # `safe_title` answers up to the cap PLUS its ellipsis; the same ceiling
    # here, so a title it made is never trimmed a second time.
    ceiling = TITLE_MAX_BYTES + len("…".encode("utf-8"))
    probe = dict(taken)
    final = unique_title(title, qualifiers, probe)
    if len(final.encode("utf-8")) <= ceiling:
        taken.update(probe)
        return final
    base = title.strip()
    suffix = final[len(base):] if final.startswith(base) else ""  # ` (<qualifier>)`, or nothing
    n = 1
    while True:
        tail = suffix if n == 1 else f"{suffix} ({n})"
        room = ceiling - len(tail.encode("utf-8")) - len("…".encode("utf-8"))
        cut = base.rstrip("…")
        while cut and len(cut.encode("utf-8")) > max(room, 0):
            cut = cut[:-1]
        fitted = f"{cut.rstrip(' .-')}…{tail}" if cut.rstrip(" .-") else final  # no room at all: the host's to refuse
        if fitted == final or page_key(fitted) not in taken:
            taken[page_key(fitted)] = probe[page_key(final)]
            return fitted
        n += 1


def settle_titles(capture_dir: Path, plan: dict) -> None:
    """One page per leaf: in plan order the first leaf to make a filename
    keeps its title, and a later one is retitled in its own `capture.json`.

    Here and not in `record`, because a lesson's final title is only known
    once it is recorded (a truncated sidebar text falls back to the body's
    heading) and lessons may be recorded in any order; `report` runs last,
    sees every one, and runs before anything is extracted. A planned lesson
    that has not landed still holds the name the sidebar gave it, so what
    landed is titled the same whether or not its namesake did.
    """
    taken: dict = {}
    for leaf in plan.get("leaves") or []:
        directory = leaf_path(capture_dir, leaf)
        record = landed(directory)
        if record is None:
            if leaf.get("title"):
                fitted_title(leaf["title"], leaf_qualifiers(leaf), taken)
            continue
        title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
        final = fitted_title(title or Path(record["body"]).stem, leaf_qualifiers(leaf), taken)
        if final != (title or Path(record["body"]).stem):
            record["title"] = final
            write_json(directory / CAPTURE_NAME, record)


# What an operator does about a course one slice could not finish. VERIFIED
# against the host: `apply` records `partial` as `ok`, and
# `pipeline/dueness.py::_harvest` never finds an `every: once` job due again
# once any `ok` exists — so nothing pulls this job a second time by itself.
RESUME = (
    "an `every: once` job (this unit's default) is NOT pulled again by itself: re-queue this ticket with "
    "`llm-wiki-ops pipeline queue retry {ticket}` (refused once a ticket has had 3 attempts), or give the job a "
    "period with `llm-wiki-ops pipeline edit {slug} every=1d` until the course is held; either run skips what "
    "is on disk or in known[]"
)


def process_report(ticket: dict, written, missing, reason) -> dict:
    """The PROCESS step's report: pages written, nothing captured. The capture
    dir is the harvest's, and its `capture.json` is older than this ticket by
    design — nothing here reads freshness off it."""
    written = list(written)
    if written and not missing:
        outcome, derived = "ok", None
    elif written:
        outcome, derived = "partial", f"{len(written)} page(s) written; {len(missing)} not reached"
    elif missing:
        outcome, derived = "failed", "no page written"
    else:
        outcome, derived = "skipped", "the capture earned no page under this ticket's own rules"
    return {
        "v": REPORT_V,
        "ticket": ticket.get("ticket"),
        "outcome": outcome,
        "reason": fold(reason) if reason else derived,
        "captured": [],
        "written": written,
        "missing": [dict(entry) for entry in missing],
        "discovered": [],
    }


def build_report(
    capture_dir: Path, ticket: dict, plan: dict, *, missing=(), written=(), stage=None, auth_expired=False,
    gone=False, reason=None, now=None,
) -> dict:
    # The step is the caller's to say — never read off `ticket.json`: a
    # single-item job's process ticket has the SAME capture dir as its
    # harvest ticket, and the file lands there under one name.
    if stage == "process" or written:
        return process_report(ticket, written, missing, reason)
    settle_titles(capture_dir, plan)
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
    refresh = plan.get("refresh") is True or ticket.get("refresh") is True
    if not gone and refresh and not captured:
        # A refresh whose page answered 404/410 (`capture_lesson.py` writes the
        # navigation's status into meta.json): the source is gone.
        gone = read_json(capture_dir / META_NAME).get("http_status") in GONE_STATUSES
    if gone and refresh and not captured:
        outcome, derived = "gone", "the lesson answered 404/410"
    elif captured and not missing and not unreached:
        outcome, derived = "ok", None
    elif captured:
        outcome = "partial"
        derived = f"{len(captured)} of {planned} lessons captured"
        if unreached:
            late = "the slice's deadline passed; " if past_deadline(plan, now) else ""
            derived += f"; {len(unreached)} not reached — {late}" + RESUME.format(
                ticket=ticket.get("ticket") or plan.get("ticket") or "<ticket-id>", slug=plan.get("slug") or "<slug>"
            )
    elif plan and not planned and any(d.get("why") == "known" for d in plan.get("dropped") or []):
        outcome, derived = "skipped", "known: every lesson in scope is already held"
    elif plan and not planned and any(d.get("why") == "access" for d in plan.get("dropped") or []):
        outcome, derived = "skipped", "harvest.access is free: every observed Circle lesson is behind the community login"
    else:
        outcome = "failed"
        derived = "no plan.json: the root capture did not land" if not plan else "nothing was captured"
    if auth_expired and target:
        derived = f"auth_expired:{host_of(target)}" + (f" — {derived}" if derived else "")
    unsafe = sum(1 for d in plan.get("dropped") or [] if d.get("why") == "unsafe_url")
    if unsafe:
        derived = (f"{derived}; " if derived else "") + f"{unsafe} sidebar link(s) refused as unsafe_url (see plan.json dropped[])"
    if plan.get("min_date") and outcome not in ("gone", "failed"):
        derived = (f"{derived}; " if derived else "") + (
            f"min_date {plan['min_date']} NOT applied: Circle declares no per-lesson date this unit has found"
        )

    return {
        "v": REPORT_V,
        "ticket": ticket.get("ticket") or plan.get("ticket"),
        "outcome": outcome,
        "reason": fold(reason) if reason else derived,
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


def forget(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def cmd_plan(args) -> int:
    capture_dir = Path(args.capture_dir)
    # FIRST: this directory is stable across pulls, `apply` does not check whose
    # report it reads, and the extractor leaves one of its own here. Nothing a
    # respawn does — refusing included — may leave an older run's `ok` behind.
    forget(capture_dir / REPORT_NAME)
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
    if plan["refresh"]:
        # Force the re-capture: the directory is the page's own and still holds
        # the FIRST pull's record, which would read as landed. `unchanged` is
        # `apply`'s verdict, reached by hashing a body really fetched again.
        for name in (CAPTURE_NAME, FACTS_NAME, BODY_HTML):
            forget(capture_dir / name)
    spawned = spawn_time(capture_dir)
    if spawned is not None:
        plan["deadline_epoch"] = spawned + max(0, min(args.budget_s, SLICE_CAP_SECONDS))
        plan["deadline"] = iso(plan["deadline_epoch"])
    for leaf in plan["leaves"]:
        leaf["landed"] = landed_as(leaf_path(capture_dir, leaf), leaf)
    write_json(capture_dir / PLAN_NAME, plan)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


def _plan_and_leaf(args):
    """`(capture_dir, plan, leaf)` for a per-leaf command, or an exit status."""
    capture_dir = Path(args.capture_dir)
    plan = read_json(capture_dir / PLAN_NAME)
    if not plan:
        print(f"error: {capture_dir / PLAN_NAME}: no plan — run `plan` first (the path is wiki-relative)", file=sys.stderr)
        return 1
    if args.leaf is not None:
        leaf = leaf_numbered(plan, args.leaf)
        named = f"--leaf {args.leaf}"
    elif getattr(args, "url", None):
        leaf = find_leaf(plan, args.url)
        named = redacted(args.url)
    else:
        print("error: name the lesson: --leaf N (its `order` in plan.json)", file=sys.stderr)
        return 1
    if leaf is None or clean_url(leaf.get("url")) is None:
        print(f"error: {named} is not a leaf of this plan — only planned lessons are worked", file=sys.stderr)
        return 1
    return capture_dir, plan, leaf


# The front door, by bare name, for the one plugin script that needs a lesson's
# url as an argument. Same rule as `capture_lesson.py`'s `_ops`.
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


# What a nested front-door call must NOT inherit from the one that ran this
# script. `CLAUDE_PROJECT_DIR` is the harness's project directory, never a wiki
# root: the `cwd=<root>` this script was handed is what binds the nested call
# to THIS wiki.
NOT_INHERITED = ("CLAUDE_PROJECT_DIR",)
ASSETS_SCRIPT = "scripts/assets.py"


def _started(argv, env=None) -> int:
    """Run an ARGUMENT LIST — never a shell — and pass on its exit status."""
    try:
        return subprocess.run(argv, env=env, check=False).returncode
    except OSError as exc:
        print(f"error: could not start {argv[0]}: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 127


def cmd_detect(args) -> int:
    found = _plan_and_leaf(args)
    if isinstance(found, int):
        return found
    capture_dir, _plan, leaf = found
    directory = leaf_path(capture_dir, leaf)
    env = {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}
    return _started(
        [*(front_door() or [OPS]), "run", ASSETS_SCRIPT, "detect", str(directory / "page.html"), "--base-url", leaf["url"],
         "--network-log", str(directory / "net.json"), "--out", str(directory / ASSETS_NAME)],
        env=env,
    )  # fmt: skip


def cmd_record(args) -> int:
    found = _plan_and_leaf(args)
    if isinstance(found, int):
        return found
    capture_dir, plan, leaf = found
    try:
        record = record_leaf(capture_dir, plan, leaf)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    late = past_deadline(plan)
    waiting = [
        other["order"] for other in plan.get("leaves") or [] if not landed_as(leaf_path(capture_dir, other), other)
    ]
    print(json.dumps({
        "leaf": leaf["order"], "dir": leaf["dir"], "title": record["title"], "body": record["body"],
        "deadline": plan.get("deadline"), "deadline_passed": late, "remaining": waiting,
        # true: start no further lesson — run `report` and exit.
        "stop": late and bool(waiting),
    }, ensure_ascii=False))  # fmt: skip
    return EXIT_STOP if late and waiting else 0


def page_path(path) -> str | None:
    """A page this run wrote, as `report.json` records it: wiki-relative, no
    escape out of the wiki, printable, and a markdown file."""
    if not isinstance(path, str) or not path.strip() or any(not ch.isprintable() for ch in path):
        return None
    if path.strip().startswith("/"):
        return None  # an absolute path is outside the wiki, whatever it is named
    cleaned = path.strip().strip("/")
    parts = PurePosixPath(cleaned).parts
    if not cleaned.endswith(".md") or not parts or ".." in parts or parts[0].startswith("."):
        return None
    return cleaned


def _host_arg(host: str) -> str | None:
    host = (host or "").strip().lower()
    return host if _HOST_SAFE.match(host) else None


def cmd_report(args) -> int:
    capture_dir = Path(args.capture_dir)
    # Before anything can refuse: a refusal that left the LAST run's report
    # here would hand `apply` a success this run did not have.
    forget(capture_dir / REPORT_NAME)
    ticket = _ticket(capture_dir, args)
    if not ticket.get("ticket"):
        print(
            f"error: {capture_dir / TICKET_NAME}: no ticket here and no --ticket. The capture dir is REQUIRED and "
            "wiki-relative (ticket.json's `capture_dir`, verbatim) — `llm-wiki-ops run` starts a script at the wiki root",
            file=sys.stderr,
        )
        return 2
    capture_dir.mkdir(parents=True, exist_ok=True)
    plan = read_json(capture_dir / PLAN_NAME)

    missing, bad = [], []
    for host, url, why in args.missing or []:
        if why not in WHYS or clean_url(url) is None or _host_arg(host) is None:
            bad.append(f"--missing {redacted(host)} {redacted(url)} {redacted(why)}")
        else:
            missing.append({"host": _host_arg(host), "url": clean_url(url), "why": why})
    for number, why in args.missing_leaf or []:
        leaf = leaf_numbered(plan, int(number)) if number.isdigit() else None
        if why not in WHYS or leaf is None:
            bad.append(f"--missing-leaf {redacted(number)} {redacted(why)}")
        else:
            missing.append({"host": host_of(leaf["url"]), "url": leaf["url"], "why": why})
    for host, why in args.missing_host or []:
        if why not in WHYS or _host_arg(host) is None:
            bad.append(f"--missing-host {redacted(host)} {redacted(why)}")
        else:
            # The HOST is what a widen is decided on; a signed media url is
            # neither safe to type nor alive by the time anyone reads it.
            missing.append({"host": _host_arg(host), "url": f"https://{_host_arg(host)}/", "why": why})
    if bad:
        print(
            f"error: refused: {'; '.join(bad)} — why is one of {', '.join(WHYS)}; a lesson is --missing-leaf N <why>, "
            "a media host is --missing-host <host> <why>; a url must be a plain http(s) one",
            file=sys.stderr,
        )
        return 2
    written, bad_paths = [], []
    for path in args.written or []:
        # A page's path carries its TITLE, which is venue text. It is written
        # down, never typed onto a command line — and never outside the wiki.
        if not page_path(path):
            bad_paths.append(redacted(path))
        else:
            written.append(page_path(path))
    if bad_paths:
        print(f"error: --written {', '.join(bad_paths)} — a wiki-relative path under the job's dest", file=sys.stderr)
        return 2
    if args.gone and not (plan.get("refresh") is True or ticket.get("refresh") is True):
        print("error: --gone is a refresh ticket's answer alone (ticket.json `refresh: true`)", file=sys.stderr)
        return 2
    report = build_report(
        capture_dir, ticket, plan, missing=missing, written=written, stage=args.stage,
        auth_expired=args.auth_expired, gone=args.gone, reason=args.reason,
    )
    write_json(capture_dir / REPORT_NAME, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["outcome"] == "failed" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    where = "the ticket's capture dir, WIKI-RELATIVE (ticket.json's `capture_dir`, verbatim)"

    p = sub.add_parser("plan", help="write plan.json: the ordered leaf work list")
    p.add_argument("capture_dir", help=f"{where}: ticket.json + the root capture's meta.json")
    p.add_argument("--target", help="the job's url, when there is no ticket.json")
    p.add_argument("--slug", help="the job's slug, when there is no ticket.json")
    p.add_argument("--scope", choices=SCOPES, help="overrides ticket.json's harvest.scope")
    p.add_argument("--ticket", help="the ticket id, when there is no ticket.json")
    p.add_argument("--budget-s", type=int, default=DEFAULT_BUDGET_SECONDS,
                   help=f"seconds after the spawn in which a new lesson may start (default {DEFAULT_BUDGET_SECONDS}; "
                        f"the slice is killed at {SLICE_CAP_SECONDS})")  # fmt: skip
    p.set_defaults(fn=cmd_plan)

    for name, fn, text in (
        ("detect", cmd_detect, "the plugin's assets.py detect over one leaf, its url read from plan.json"),
        ("record", cmd_record, "one leaf's capture.json and facts.json, over the bytes it captured"),
    ):
        r = sub.add_parser(name, help=text)
        r.add_argument("capture_dir", help=f"{where}: holds plan.json")
        r.add_argument("--leaf", type=int, metavar="N", help="the lesson, by its `order` in plan.json")
        if name == "record":
            r.add_argument("url", nargs="?", help="HAND RUNS ONLY: the planned lesson url, in place of --leaf")
        r.set_defaults(fn=fn)

    w = sub.add_parser("report", help="write report.json — after every leaf, and last")
    w.add_argument("capture_dir", help=where)
    w.add_argument("--auth-expired", action="store_true", help="every lesson not on disk goes to missing[] as why=auth")
    w.add_argument("--gone", action="store_true", help="a REFRESH ticket whose lesson answered 404/410")
    w.add_argument("--reason", help="overrides the derived reason — your own words, never venue text")
    w.add_argument("--missing-leaf", nargs=2, action="append", metavar=("N", "WHY"), help="a planned lesson, by number")
    w.add_argument("--missing-host", nargs=2, action="append", metavar=("HOST", "WHY"), help="a media host, by name")
    w.add_argument("--missing", nargs=3, action="append", metavar=("HOST", "URL", "WHY"), help="HAND RUNS ONLY")
    w.add_argument("--stage", choices=STAGES, help="which step this report answers; `process` where no page was "
                                                    "written either")  # fmt: skip
    w.add_argument("--written", action="append", metavar="PATH",
                   help="a page THIS process ticket wrote, wiki-relative; repeatable. Given it, the report is the "
                        "process step's: written[] is these, captured[] is empty")  # fmt: skip
    w.add_argument("--ticket", help="the ticket id, when there is no ticket.json")
    w.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
