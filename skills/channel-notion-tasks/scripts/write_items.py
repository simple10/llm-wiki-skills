#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The two arms of `channel-notion-tasks`: the pull harvest writes down, and the
ledger process writes.

  write_items.py since  <capture_dir> [--lookback-days 14]
  write_items.py write  <capture_dir> [--from pull.json] \\
                 [--database ID]... [--exclude-status S]... [--cap N] \\
                 [--partial <reason>] [--missing <host> <url> <denied|timeout|auth|error>]...
  write_items.py write  <capture_dir> --failed <reason> [--missing ...]
  write_items.py ledger <capture_dir> --dest <dest> [--from lines.json] [--partial <reason>]

`<capture_dir>` is the ticket's own `capture_dir`, VERBATIM — the job's DAY
directory, `_raw/<slug>/<YYYY-MM-DD>`, WIKI-RELATIVE: `llm-wiki-ops run` starts
a script at the wiki root, not in the capture directory the worker stands in,
so `.` is the wrong answer here. `ticket.json` in it is read for the ticket id,
the `capture_dir` the report names, `options.workspace`, `min_date` and — on
`ledger` — `dest`; a directory with none is refused unless `--ticket` says this
is a hand run (`--workspace`, `--min-date` and `--dest` stand in for the rest).
A bare `--from` name is looked for INSIDE the capture directory.

Which arm runs is the caller's word, never a guess: `since` and `write` are
harvest, `ledger` is process.

`since` is HARVEST's first step, and removes a stale `report.json` before it
answers: the day directory is stable across the day's pulls, every pull of a
job carries the same ticket id, and `apply` does not check whose ticket a
report answers — so a unit that died before its last step would otherwise
leave an old `ok` to land.

`since` answers where this pull starts, as JSON: the watermark in
`_raw/<slug>/.cursor.json` when it is there, else now minus the lookback;
never earlier than the ticket's `min_date`.

`write` is HARVEST's last step. It takes the tasks the worker read off the
connector AS THEY ARRIVED — a JSON list on stdin or in `--from` — and writes
them down with no judgment of its own. No summary and no junk rule: both are
judgment, and judgment is the process step's.

    [{"id": "<task-id>", "last_edited": "<ISO-8601>", "database": "<id or name>",
      "title": "<the task's own>", "status": "…", "due": "…", "assignee": "…",
      "url": "https://www.notion.so/…", "body": "<plain-text notes>"}]

Oldest `--cap` first, so the watermark it leaves is a clean resume point; then
the mechanical filters (databases, statuses, `min_date`, already behind the
watermark); then one file per task, `items/<last-edited>--<task-id>.json`. A
task edited twice in one day is ONE file: the newer replaces the older, so the
day carries one entry per task however often it is pulled.

`capture.json` beside them is FLAT — `slug`, `item`, `title` (the day, which is
the ledger's title), `body`, `content_type`, `fetched_at` — with no key a host
verb owns and no `frontmatter` object. Its `body` names the day's `items/`
DIRECTORY rather than one file: this route accumulates a whole day across
sub-daily pulls, and one file per task is what keeps a task edited twice one
entry.

Then `report.json`, and only THEN the watermark (never backwards, never past
the clock, never by a failed pull). `captured[]` names the day directory
whenever its `items/` holds a file — not only when THIS invocation wrote one —
so a rerun never reports `captured: []` over items no ledger has; and a report
an earlier `write` on this ticket left with captures is never downgraded: a
later failure makes it `partial` and says why.

An item with an id and no trustworthy time — none, unparseable, or more than a
day ahead of this machine's clock (one seconds/ms/µs slip) — is KEPT, filed
under the pull's own clock, marked `time_untrusted` and counted `bad_time`; it
never moves the watermark. A watermark file that is unreadable or ahead of the
clock is ignored, out loud, and the lookback stands in.

`ledger` is the PROCESS arm. It reads the day's `items/` and the process step's
own words — `[{"id": "<task-id>", "line": "<one factual line>", "junk": null |
"<rule>"}]`, one row per item, on stdin or in `--from` — and writes the day's
ledger under `dest` through the front door: `page create`, or `page edit` over
the page that day already has. The page is regenerated WHOLE every run, because
the day directory is the source of truth. An item no row names keeps its own
title as its bullet and the run reports `partial`: a rerun loses no item, and
no run claims words it did not write. Then `report.json`, LAST, naming the page
in `written[]`.

A bullet is the host's own shape — `- <line> — <pointer>`, folded to one line
and capped at 200 — so a ledger reads the same whoever wrote it, and what that
fold leaves live is swapped for look-alikes first (`host_line`): raw HTML's `<`
`>`, the ` — ` before the pointer, a bare url, `*` and `|`.

Wiki-owned, stdlib only, imports nothing from the plugin.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VENUE = "notion"
INPUT_KEY = "workspace"  # the unit's one `watch.inputs` answer, under the ticket's `options`
LOOKBACK_DAYS = 14
CAP = None  # none: the old unit had no per-run cap, and the slice's own clock is the bound

REPORT_V = 1
ITEM_V = 1
REPORT_NAME = "report.json"
TICKET_NAME = "ticket.json"
CURSOR_NAME = ".cursor.json"
ITEMS_DIRNAME = "items"  # the host's own name for it: `pipeline/extract.py::ITEMS_DIRNAME`
WHYS = ("denied", "timeout", "auth", "error")
BODY_MAX = 50_000
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
# What `host_line` swaps: look-alikes for the characters the host's own folding leaves live.
_SWAPS = str.maketrans({"<": "‹", ">": "›", "—": "-", "―": "-", "*": "∗", "|": "¦"})
_WWW = re.compile(r"(?i)\bwww\.")
PULL_NAME = "pull.json"
LINES_NAME = "lines.json"  # the process step's own words, one row per item in the day
CAPTURE_NAME = "capture.json"
LEDGER_TYPE = "ledger"
BULLET_MAX = 200  # the host's own cap on a ledger bullet: `pipeline/extract.py::BULLET_MAX`
_FOLD = re.compile(r"\s+")
_FORGES = str.maketrans({"`": "'", "[": "(", "]": ")"})  # `pipeline/extract.py::_FORGES`

# The front door, by the bare name every SKILL.md already runs this script
# under — never a path into the wiki, which stops carrying a shim.
OPS = "llm-wiki-ops"
# What a nested front-door call must NOT inherit from the one that ran this
# script. `CLAUDE_PROJECT_DIR` is the harness's project directory, never a wiki
# root: the `cwd=<root>` this script was handed is what binds the nested call
# to THIS wiki.
NOT_INHERITED = ("CLAUDE_PROJECT_DIR",)
# `page create`'s one refusal that is not a failure: this day already has a
# ledger. The TAIL only: the CLI says `<path> already exists — the filename is
# the title`, and `--json` prints that em dash escaped, so a marker carrying
# one matches the prose voice and never the JSON one this script asks for.
EXISTS = "the filename is the title"
EXTRACTED = "true"  # the string `pipeline/pages.py` reads as DONE
AHEAD_MS = 86_400_000  # how far ahead of this machine's clock an item's own time is still believed: a day of skew


# ------------------------------------------------------------------ the venue

_PAGE_ID = re.compile(r"^[0-9a-fA-F]{32}$")


def _parsed(value):
    """An ISO-8601 time as epoch milliseconds, or None. A bare date is midnight UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        at = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return int(at.timestamp() * 1000)


def when_of(item):
    """A task's own clock — Notion's `last_edited_time` — as epoch milliseconds, or None."""
    return _parsed(item.get("last_edited"))


def show(when):
    """A time as this venue's connector is asked for it: ISO-8601, UTC."""
    return datetime.fromtimestamp(when / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{when % 1000:03d}Z"


def stamp_of(when):
    """The sortable head of an item's filename, so names sort by time."""
    return datetime.fromtimestamp(when / 1000, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def day_of(when):
    return datetime.fromtimestamp(when / 1000, timezone.utc).strftime("%Y-%m-%d")


def from_day(day):
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def ago(now, days):
    return int((now - timedelta(days=days)).timestamp() * 1000)


def pointer_of(item, safe_id):
    """Built from the ID, never taken from the venue's url. A Notion url is
    `…/<the task's title>-<id>`: the host caps a pointer at 200 characters, so
    a long title cost the pointer its id — the one part that points — and put
    the task's own words in the half of the bullet that reads as ours. A page
    id (32 hex, dashed or not) becomes the short page url (unverified from here:
    no run of this port reached the venue); any other id is `notion:<id>`. The venue's url is kept in the item file."""
    bare = safe_id.replace("-", "")
    if _PAGE_ID.fullmatch(bare):
        return f"https://www.notion.so/{bare.lower()}"
    return f"{VENUE}:{safe_id}"


def cursor_when(cursor):
    return _parsed(cursor.get("last_edited_watermark"))


def cursor_for(when, safe_id):
    return {"last_edited_watermark": show(when)}


def behind_cursor(when, safe_id, cursor):
    """Strictly older than the watermark. A TIE is kept: Notion rounds
    `last_edited_time` to the minute (unverified), so a task edited again
    inside the watermark's own minute would otherwise be lost."""
    at = cursor_when(cursor)
    return at is not None and when < at


def venue_args(parser):
    parser.add_argument("--database", action="append", default=[], metavar="ID", help="pull ONLY these; none named is all")
    parser.add_argument("--exclude-status", action="append", default=[], metavar="STATUS")


def filtered_by(item, args):
    """The mechanical filter this task fails, by name, or None."""
    if args.database and item.get("database") not in args.database:
        return "database"
    status = item.get("status")
    if isinstance(status, str) and status.strip().lower() in {one.strip().lower() for one in args.exclude_status}:
        return "status"
    return None


def stored_of(item):
    """What is kept of a task beside the line the ledger reads."""
    kept = {key: _text(item.get(key)) for key in ("database", "status", "due", "assignee", "url")}
    when = when_of(item)  # normalized where it parses; else the venue's own text, for whoever reads the file
    kept["last_edited"] = show(when) if when is not None else _text(item.get("last_edited"))
    return kept


RAW_LINE_KEY = "title"  # the venue's own one-line name for an item, in the input
HOST_LINE_KEY = "title"  # the key the fallback is written under; no `subject` is ever written, so it is read first


# ------------------------------------------------------------------ the shape


def _text(value):
    return value if isinstance(value, str) else None


def _read_json(path):
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write_json(path, doc):
    """Whole or not at all: a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name.lstrip('.')}.tmp")  # dot-first: never a bullet if a crash leaves it
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _ms(moment):
    return int(moment.timestamp() * 1000)


def safe_id(value):
    """An id as one filename-safe token, or None. The id is the venue's, and a
    venue's bytes do not get to name a path."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    token = _UNSAFE.sub("_", str(value)).strip("_")[:120]
    return token or None


def host_line(value):
    """The one field the host's ledger reads, with what the host's own folding
    leaves open swapped out. `extract.py::_plain` folds whitespace, caps at 200
    and turns a backtick and the two square brackets; it leaves raw HTML, the
    ` — ` its own bullet puts before the pointer, a bare url a renderer
    autolinks, `*` emphasis and a table's `|`. Look-alikes, not deletions: the
    line still reads as what was written."""
    return _WWW.sub("www․", value.translate(_SWAPS).replace("://", ":∕∕"))


def item_doc(item, sid, *, trusted=True):
    """One pulled task as it arrived. The pointer is built from the safe id
    alone, so it needs no neutralizing; the venue's own one-line name is kept
    twice — under `title`, ready for a bullet, and raw under `venue_title` for
    the process step, which has the whole task to judge from."""
    doc = {"v": ITEM_V, "id": pointer_of(item, sid)}
    raw = _text(item.get(RAW_LINE_KEY))
    if raw and raw.strip():
        doc[HOST_LINE_KEY] = host_line(raw)
    doc[f"venue_{RAW_LINE_KEY}"] = raw
    doc.update(stored_of(item))
    if not trusted:
        doc["time_untrusted"] = True  # filed under the PULL's clock; the venue's own value is in `stored_of`'s keys
    body = _text(item.get("body")) or ""
    doc["body"] = body[:BODY_MAX]
    doc["body_truncated"] = len(body) > BODY_MAX
    return doc


def id_in(name):
    """The id an item file is named for — EXACTLY: `<stamp>--<id>.json`, dot or
    no dot. A stamp never carries `--`; an id may, so the split is the first."""
    stem = name[1:] if name.startswith(".") else name
    _stamp, sep, rest = stem.partition("--")
    return rest[: -len(".json")] if sep and rest.endswith(".json") else None


def holds_items(day_dir):
    """Is there anything in this day for the extractor to make a ledger from —
    a kept item, or a junked one its `discarded:` line counts?"""
    items_dir = day_dir / ITEMS_DIRNAME
    return items_dir.is_dir() and any(path.is_file() and id_in(path.name) for path in items_dir.iterdir())


def held_elsewhere(slice_dir, day_dir, names):
    """Is this item already in ANOTHER day's directory? A tie with the cursor
    is pulled twice, and must not become a second day's bullet."""
    for other in slice_dir.iterdir() if slice_dir.is_dir() else []:
        if other == day_dir or not DAY_RE.fullmatch(other.name):
            continue
        if any((other / ITEMS_DIRNAME / name).is_file() for name in names):
            return True
    return False


def read_cursor(slice_dir, now):
    """`(cursor, problem)`. A cursor that cannot be trusted — unreadable, not
    the shape this unit writes, or AHEAD of the clock — is no cursor: the pull
    falls back to the lookback and says so, instead of raising on every later
    `since` or calling every later item `already_pulled`. Items the wider pull
    hands over again are dropped by `held_elsewhere`, and the next good write
    replaces the file."""
    path = slice_dir / CURSOR_NAME
    if not path.exists():
        return {}, None
    doc = _read_json(path)
    try:
        at = cursor_when(doc)
    except (TypeError, ValueError, OverflowError):
        at = None
    if at is None:
        return {}, f"{CURSOR_NAME} is unreadable or not this unit's shape — ignored, the lookback stands in"
    if at > now + AHEAD_MS:
        return {}, f"{CURSOR_NAME} is ahead of the clock — ignored, the lookback stands in"
    return doc, None


def plan(items, args, *, cursor, min_date, slice_dir, day_dir, now):
    """The pure half: which messages become which files, and the counts.

    Returns `(files, newest, counts)` — `files` is `[(name, doc, safe_id)]`,
    `newest` the `(when, safe_id)` the cursor moves to, or None.

    An item with an id and NO trustworthy time — absent, unparseable, or more
    than a day ahead of `now` (one seconds/ms/µs slip is 1 000× the clock) — is
    KEPT, filed under the pull's own clock and marked `time_untrusted`: the
    day directory is the PULL's day anyway, so all it loses is its place in the
    day's order, where dropping it would lose the item for good once the
    cursor passed its true time. It never moves the cursor, and it is counted
    under `bad_time` so the report says it happened.
    """
    counts = {"fetched": len(items), "written": 0, "invalid": 0, "bad_time": 0, "held_back": 0}
    skipped = {}
    usable = []
    for item in items:
        sid = safe_id(item.get("id")) if isinstance(item, dict) else None
        if sid is None:
            counts["invalid"] += 1
            continue
        when = when_of(item)
        trusted = when is not None and when <= now + AHEAD_MS
        if not trusted:
            counts["bad_time"] += 1
        usable.append((when if trusted else now, sid, item, trusted))
    usable.sort(key=lambda row: (row[0], row[1]))
    if args.cap and len(usable) > args.cap:
        counts["held_back"] = len(usable) - args.cap
        usable = usable[: args.cap]
    files, newest = [], None
    floor = from_day(min_date) if min_date else None
    for when, sid, item, trusted in usable:
        if trusted:
            newest = (when, sid)
        names = (f"{stamp_of(when)}--{sid}.json",)
        why = None
        if trusted and (behind_cursor(when, sid, cursor) or held_elsewhere(slice_dir, day_dir, names)):
            why = "already_pulled"
        elif trusted and floor is not None and when < floor:
            why = "min_date"
        else:
            why = filtered_by(item, args)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        files.append((names[0], item_doc(item, sid, trusted=trusted), sid))
        counts["written"] += 1
    counts["filtered"] = skipped
    return files, newest, counts


def land(day_dir, files):
    """Write the files, each replacing any earlier file for the same id in this
    day — kept or junked — so one item is one bullet however often it is pulled."""
    items_dir = day_dir / ITEMS_DIRNAME
    items_dir.mkdir(parents=True, exist_ok=True)
    for name, doc, sid in files:
        for old in items_dir.iterdir():
            if old.name != name and id_in(old.name) == sid:
                old.unlink()
        _write_json(items_dir / name, doc)


def report_for(job, *, outcome, reason, captured, missing, written=()):
    return {
        "v": REPORT_V,
        "ticket": job.get("ticket"),
        "outcome": outcome,
        "reason": reason,
        "captured": captured,
        "written": list(written),
        "missing": missing,
        "discovered": [],
    }


def capture_record(directory, job, now):
    """What harvest leaves to say the day landed. FLAT, and it carries no key a
    host verb owns and no `frontmatter` object: the facts reach the page
    because this unit's process step writes the page.

    `body` names the day's `items/` DIRECTORY, where every other route names
    one file. A channel day accumulates across sub-daily pulls, and one file
    per task is what keeps a task edited twice one entry."""
    return {
        "slug": job.get("slug") or directory.parent.name,
        "item": job.get("item") or job.get("target"),
        "title": directory.name,  # the day, which is the ledger's title; `DAY_RE` already holds it to a filename
        "body": ITEMS_DIRNAME,
        "content_type": "application/json",
        "fetched_at": show(now),
    }


def carried(directory, job):
    """The report an EARLIER `write` on this same ticket left, when it carries
    captures — else None. `since` removes a stale report first, so one found
    here is this run's own: a second `write` must not turn it into `failed`
    or `captured: []` while the items it named sit on disk with no ledger."""
    prior = _read_json(directory / REPORT_NAME)
    same = job.get("ticket") is not None and prior.get("ticket") == job.get("ticket")
    landed = prior.get("outcome") in ("ok", "partial") and isinstance(prior.get("captured"), list) and prior["captured"]
    return prior if same and landed else None


def load_job(directory, args):
    job = _read_json(directory / TICKET_NAME)
    if args.ticket:
        job["ticket"] = args.ticket
    options = job.get("options") if isinstance(job.get("options"), dict) else {}
    answer = getattr(args, INPUT_KEY, None) or options.get(INPUT_KEY)
    job[INPUT_KEY] = answer if isinstance(answer, str) and answer.strip() else None
    job.setdefault("capture_dir", directory.as_posix())
    given = getattr(args, "dest", None)
    if given:
        job["dest"] = given
    floor = getattr(args, "min_date", None) or job.get("min_date")
    try:
        job["min_date"] = floor if isinstance(floor, str) and DAY_RE.fullmatch(floor) and from_day(floor) >= 0 else None
    except (ValueError, OverflowError):
        job["min_date"] = None  # `2026-13-45` has the shape and is no day
    return job


def source_of(directory, given, default=PULL_NAME):
    """Where the arm's input is read from — a path, or None for stdin.

    `llm-wiki-ops run` starts this script at the WIKI ROOT, not in the capture
    directory the worker stands in. So a bare name (`pull.json`, `lines.json`)
    is looked for INSIDE the capture directory, which is where the worker wrote
    it; anything with a directory in it is as given — wiki-relative, like
    `capture_dir` itself. No `--from`: the arm's own default name in the capture
    directory when it is there, else stdin. `-` is stdin outright."""
    if given == "-":
        return None
    if given is None:
        fallback = directory / default
        return fallback if fallback.is_file() or sys.stdin is None or sys.stdin.isatty() else None
    path = Path(given)
    return directory / path if not path.is_absolute() and len(path.parts) == 1 else path


def misplaced(directory, job):
    """A pull written as `<capture_dir>/pull.json` by a worker already standing
    IN the capture directory lands nested — inside the grant, so nothing fails
    until the pull is looked for. Name it rather than lose it."""
    nested = directory / str(job.get("capture_dir") or "") / PULL_NAME
    return nested if nested != directory / PULL_NAME and nested.is_file() else None


def since(directory, args, *, now=None):
    # FIRST, before anything can stop this run: a day directory is stable across
    # the day's pulls, the ticket id is the same for every pull of a job, `apply`
    # never checks either, and the extractor writes its own `report.json` here.
    # A unit that died before its last step would leave yesterday's `ok` to land.
    (directory / REPORT_NAME).unlink(missing_ok=True)
    job = load_job(directory, args)
    if job[INPUT_KEY] is None:
        print(f"write_items: the job names no {INPUT_KEY} — `options.{INPUT_KEY}` is this unit's one input", file=sys.stderr)
        return 1
    now = now or datetime.now(timezone.utc)
    cursor, problem = read_cursor(directory.parent, _ms(now))
    if problem:
        print(f"write_items: {problem}", file=sys.stderr)
    at = cursor_when(cursor) if cursor else None
    start = at if at is not None else ago(now, args.lookback_days)
    if job["min_date"]:
        start = max(start, from_day(job["min_date"]))
    answer = {
        INPUT_KEY: job[INPUT_KEY],
        "first_pull": at is None,
        "since": show(start),
        "since_day": day_of(start),
        "min_date": job["min_date"],
        "cursor": cursor or None,
        "cursor_ignored": problem,
    }
    print(json.dumps(answer, indent=2))
    return 0


def write(directory, args, *, now=None):
    job = load_job(directory, args)
    now = _ms(now or datetime.now(timezone.utc))
    missing = [{"host": host, "url": url, "why": why} for host, url, why in args.missing]
    for entry in missing:
        if entry["why"] not in WHYS:
            print(f"write_items: --missing why must be one of {', '.join(WHYS)}", file=sys.stderr)
            return 2
    day = {"item": job.get("item") or job.get("target"), "dir": job["capture_dir"], "title": None}

    def finish(outcome, reason, counts=None):
        """The report, LAST. `captured[]` names the day whenever the day holds
        items — not only when THIS invocation wrote one — and a report this
        ticket already left with captures is never downgraded."""
        failed = outcome == "failed"
        prior = carried(directory, job)
        if prior is not None:
            if failed:
                reason = f"a later write on this ticket failed and the captures stand: {reason}"
            if failed or prior["outcome"] == "partial":
                outcome = "partial"
            reason = "; ".join(one for one in (prior.get("reason"), reason) if one) or None
            every = [*(prior.get("missing") if isinstance(prior.get("missing"), list) else []), *missing]
            missing[:] = [entry for index, entry in enumerate(every) if entry not in every[:index]]
        captured = [day] if outcome != "failed" and holds_items(directory) else []
        _write_json(directory / REPORT_NAME, report_for(job, outcome=outcome, reason=reason, captured=captured, missing=missing))
        print(json.dumps({"outcome": outcome, "reason": reason, "captured": len(captured), **(counts or {})}, indent=2))
        return 1 if failed else 0

    if args.failed:
        return finish("failed", args.failed)
    source = source_of(directory, args.source)
    try:
        raw = source.read_text(encoding="utf-8") if source is not None else sys.stdin.read()
        items = json.loads(raw)
    except (OSError, ValueError) as exc:
        nested = misplaced(directory, job)
        hint = f" — there is one at {nested.as_posix()}: it was written relative to the capture directory it was already in" if nested else ""
        return finish("failed", f"the pull could not be read: {type(exc).__name__}: {exc}{hint}")
    if not isinstance(items, list):
        return finish("failed", "the pull is not a JSON list of items")

    slice_dir = directory.parent
    cursor, problem = read_cursor(slice_dir, now)
    files, newest, counts = plan(items, args, cursor=cursor, min_date=job["min_date"], slice_dir=slice_dir, day_dir=directory, now=now)
    if items and counts["invalid"] == len(items):
        return finish("failed", f"{len(items)} item(s) and none carries a usable id", counts)
    try:
        land(directory, files)
        if holds_items(directory):
            _write_json(directory / CAPTURE_NAME, capture_record(directory, job, now))
    except OSError as exc:
        return finish("failed", f"{type(exc).__name__}: {exc}", counts)

    reasons = [args.partial] if args.partial else []
    if counts["held_back"]:
        reasons.append(f"capped at {args.cap}: {counts['held_back']} newer item(s) left for the next pull")
    if counts["bad_time"]:
        reasons.append(f"{counts['bad_time']} item(s) carried no trustworthy time and were filed under the pull's own clock")
    if problem:
        reasons.append(problem)
    if not files and not reasons:
        reasons.append("nothing new since the cursor")
    # The cursor moves only once the report is safely down: a crash between the
    # two then costs a re-pull the filenames dedupe, never an item the cursor
    # has passed and no report names. Never backwards, and never past the clock.
    at = cursor_when(cursor) if cursor else None
    moved = cursor_for(min(newest[0], now), newest[1]) if newest is not None and (at is None or min(newest[0], now) >= at) else None
    if moved:
        counts["cursor"] = moved
    partial = bool(args.partial or counts["held_back"])
    code = finish("partial" if partial else "ok", "; ".join(reasons) or None, counts)
    try:
        if moved:
            _write_json(slice_dir / CURSOR_NAME, moved)
        if source is not None and source.resolve().is_relative_to(directory.resolve()):
            source.unlink(missing_ok=True)  # consumed: the items are the record
    except OSError as exc:
        print(f"write_items: the report is written; the cursor did not move — {type(exc).__name__}: {exc}", file=sys.stderr)
    return code


# ------------------------------------------------------------------ the ledger


class PageRefused(Exception):
    """The front door refused, and NO page was written. The caller still owes
    a report, so this is raised rather than exited on."""


def _front_door_env():
    return {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}


def plain(value):
    """One line as a ledger bullet carries it — the host's own `_plain`
    (`pipeline/extract.py`), so a ledger reads the same whoever wrote it."""
    folded = _FOLD.sub(" ", value).strip().translate(_FORGES)
    return folded[: BULLET_MAX - 1] + "\u2026" if len(folded) > BULLET_MAX else folded


def lines_by_id(rows):
    """The process step's own words, keyed by the safe id the item files carry.
    A row that is not a dict, or names no usable id, is counted, not obeyed."""
    by_id, unusable = {}, 0
    for row in rows if isinstance(rows, list) else []:
        sid = safe_id(row.get("id")) if isinstance(row, dict) else None
        if sid is None:
            unusable += 1
            continue
        by_id[sid] = row
    return by_id, unusable


def ledger_body(items, by_id):
    """`(body, counts)` — one bullet per kept item, oldest first, then the tally
    of what junk rules dropped. The discarded content itself never appears.

    An item no row names keeps its OWN title as its bullet and is counted
    `unlined`: dropping it would lose a task the day pulled, and inventing a
    line for it would be words this unit did not write."""
    bullets = []
    counts = {"items": len(items), "kept": 0, "discarded": 0, "unlined": 0}
    for path in items:
        doc = _read_json(path)
        row = by_id.get(id_in(path.name)) or {}
        junk = _text(row.get("junk"))
        if junk and junk.strip():
            counts["discarded"] += 1
            continue
        line = _text(row.get("line"))
        if not (line and line.strip()):
            line = _text(doc.get(HOST_LINE_KEY)) or ""
            counts["unlined"] += 1
        # `host_line` first, then the fold and the cap: the values started as
        # the venue's text, and a line written here is read as this unit's.
        line = plain(host_line(line)) if line else ""
        pointer = plain(str(doc.get("id") or path.name))
        bullets.append(f"- {line} \u2014 {pointer}" if line else f"- {pointer}")
        counts["kept"] += 1
    body = "\n".join(bullets) + f"\n\ndiscarded: {counts['discarded']} (junk rules)\n"
    return body, counts


def write_page(dest, title, front, body):
    """The day's ledger under `dest`, through the front door. Returns it,
    wiki-relative.

    `page create` first, `page edit` on the one refusal that means this day
    already has a ledger — the day directory is the source of truth and the
    page is regenerated whole from it every run. The arguments are an argv
    LIST and never a shell line: every value on one started as the venue's
    text, and a venue that can type onto a Bash line can run a command.

    The wiki is the cwd: `llm-wiki-ops run` starts this script at the wiki
    root, and that is what binds the front door to THIS wiki."""
    ops = shutil.which(OPS)
    if ops is None:
        raise PageRefused(f"`{OPS}` is not on PATH \u2014 the front door is how this unit writes a page; install the ops plugin on this machine")
    where = {"cwd": os.getcwd(), "env": _front_door_env()}
    keys = [f"{key}={value}" for key, value in front.items()]
    # `status=` EMPTY, on create only. A ledger is outside the corpus and the
    # lifecycle by location, and a `draft` here would put every day of every
    # channel in curate's list. `page edit` REFUSES the same key (exit 2 —
    # curate, revise and retire are what move a status) and does not need it:
    # a page created without a status still has none after an edit.
    created = subprocess.run(
        [ops, "--json", "page", "create", f"title={title}", f"dest={dest}", *keys, "status=", "--stdin"],
        input=body, capture_output=True, text=True, **where,
    )
    rel = f"{dest}/{title}.md"
    if created.returncode == 0:
        return rel
    said = ((created.stdout or "") + (created.stderr or "")).strip()
    if EXISTS not in said:
        raise PageRefused(f"`page create` refused (exit {created.returncode}): {said[-300:]}")
    edited = subprocess.run(
        [ops, "--json", "page", "edit", rel, *keys, "--stdin"],
        input=body, capture_output=True, text=True, **where,
    )
    if edited.returncode != 0:
        said = ((edited.stdout or "") + (edited.stderr or "")).strip()
        raise PageRefused(f"`page edit` refused (exit {edited.returncode}) over {rel}: {said[-300:]}")
    return rel


def ledger(directory, args, *, now=None):
    # FIRST, as harvest's `since` does: the capture directory is the same one
    # on every pull, and `apply` does not check whose ticket a report answers.
    # The freshness rule harvest reports under does NOT apply here — a process
    # ticket rewrites `ticket.json` long after harvest wrote the items.
    (directory / REPORT_NAME).unlink(missing_ok=True)
    job = load_job(directory, args)
    now = _ms(now or datetime.now(timezone.utc))
    dest = str(job.get("dest") or "").strip().rstrip("/")

    def finish(outcome, reason, written=(), counts=None):
        """The report, LAST. A process report names its pages in `written[]`
        and captures nothing: the day was captured by harvest."""
        _write_json(directory / REPORT_NAME, report_for(job, outcome=outcome, reason=reason, captured=[], missing=[], written=written))
        print(json.dumps({"outcome": outcome, "reason": reason, "written": list(written), **(counts or {})}, indent=2))
        return 1 if outcome == "failed" else 0

    if not dest or Path(dest).is_absolute() or ".." in Path(dest).parts:
        return finish("failed", f"no usable dest: the ticket's `dest` is the directory the day's ledger goes in, wiki-relative (got {dest!r})")
    items_dir = directory / ITEMS_DIRNAME
    items = sorted(path for path in items_dir.iterdir() if path.is_file() and id_in(path.name)) if items_dir.is_dir() else []
    if not items:
        return finish("skipped", f"{directory.name} holds no {ITEMS_DIRNAME}/ to make a ledger from")

    source = source_of(directory, args.source, default=LINES_NAME)
    rows, no_lines = [], f"no {LINES_NAME}: every bullet is the task's own title, not this unit's words"
    try:
        raw = source.read_text(encoding="utf-8") if source is not None else sys.stdin.read()
    except OSError as exc:
        # Only a `--from` the caller named must exist; the default name and an
        # empty stdin are a run with no words of its own, which still renders.
        if args.source:
            return finish("failed", f"the lines could not be read: {type(exc).__name__}: {exc}")
        raw = ""
    if raw.strip():
        no_lines = None
        try:
            rows = json.loads(raw)
        except ValueError as exc:
            return finish("failed", f"the lines could not be read: {type(exc).__name__}: {exc}")
        if not isinstance(rows, list):
            return finish("failed", "the lines are not a JSON list of `{id, line, junk}` rows")

    by_id, unusable = lines_by_id(rows)
    body, counts = ledger_body(items, by_id)
    counts["unmatched"] = len(set(by_id) - {id_in(path.name) for path in items})
    if not counts["kept"]:
        return finish("skipped", f"every one of {counts['items']} item(s) was dropped by a junk rule", counts=counts)

    front = {
        "type": LEDGER_TYPE,
        "channel": job.get("slug") or directory.parent.name,
        "date": directory.name,
        "items": str(counts["kept"]),
        "extracted": EXTRACTED,
    }
    try:
        written = [write_page(dest, directory.name, front, body)]
    except PageRefused as exc:
        return finish("failed", str(exc), counts=counts)

    reasons = [one for one in (args.partial, no_lines) if one]
    if counts["unlined"]:
        reasons.append(f"{counts['unlined']} item(s) had no line of this unit's own and carry the task's own title")
    if counts["unmatched"] or unusable:
        reasons.append(f"{counts['unmatched'] + unusable} line(s) named no task this day holds")
    return finish("partial" if reasons else "ok", "; ".join(reasons) or None, written=written, counts=counts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("since", "write", "ledger"):
        sub = subs.add_parser(verb)
        sub.add_argument("capture_dir", help="the ticket's own `capture_dir`, WIKI-RELATIVE as the ticket spells it: the job's day directory")
        sub.add_argument("--ticket", help=f"the ticket id, when there is no {TICKET_NAME} (a hand run, or `spawn: none`)")
        sub.add_argument(f"--{INPUT_KEY}", help=f"`options.{INPUT_KEY}`, when there is no {TICKET_NAME}")
        sub.add_argument("--min-date", metavar="YYYY-MM-DD", help=f"the ticket's `min_date`, when there is no {TICKET_NAME}; overrides it when there is")
        if verb == "since":
            sub.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS, help="the FIRST pull's window")
            continue
        default_name = LINES_NAME if verb == "ledger" else PULL_NAME
        sub.add_argument("--from", dest="source", help=f"this arm's input as a JSON file: a bare name is looked for IN the capture directory, a path is wiki-relative, `-` is stdin; absent, `{default_name}` in the capture directory, else stdin")
        sub.add_argument("--partial", metavar="REASON", help="this step stopped early, and why")
        if verb == "ledger":
            sub.add_argument("--dest", help="the ticket's `dest`: the directory the day's ledger goes in, wiki-relative")
            continue
        sub.add_argument("--cap", type=int, default=CAP, help="items per run, oldest first; at least 1" + ("" if CAP else " (absent: no cap)"))
        sub.add_argument("--failed", metavar="REASON", help="nothing was pulled, and why; writes the report alone")
        sub.add_argument("--missing", nargs=3, action="append", default=[], metavar=("HOST", "URL", "WHY"))
        venue_args(sub)
    args = parser.parse_args(argv)
    directory = Path(args.capture_dir)
    if not DAY_RE.fullmatch(directory.name):
        print(
            f"write_items: {args.capture_dir!r} is not a day directory — give the ticket's own `capture_dir` "
            f"(`_raw/<slug>/<YYYY-MM-DD>`), wiki-relative: this script runs at the wiki root, not where you stand",
            file=sys.stderr,
        )
        return 2
    if not (directory / TICKET_NAME).is_file() and not args.ticket:
        print(
            f"write_items: {args.capture_dir!r} holds no {TICKET_NAME} — it is the ticket's own `capture_dir`, wiki-relative "
            f"(this script runs at the wiki root); a hand run names `--ticket` and `--{INPUT_KEY}` instead",
            file=sys.stderr,
        )
        return 2
    if args.verb == "write" and args.cap is not None and args.cap < 1:
        print("write_items: --cap is at least 1 — a cap below that would drop the newest item(s) in silence", file=sys.stderr)
        return 2
    return {"since": since, "write": write, "ledger": ledger}[args.verb](directory, args)


if __name__ == "__main__":
    sys.exit(main())
