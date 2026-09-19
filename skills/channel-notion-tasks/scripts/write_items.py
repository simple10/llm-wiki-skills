#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One notion-tasks pull, written down: the day's item files, the watermark, `report.json`.

  write_items.py since <capture_dir> [--lookback-days 14]
  write_items.py write <capture_dir> [--from pull.json] \\
                 [--database ID]... [--exclude-status S]... [--cap N] \\
                 [--partial <reason>] [--missing <host> <url> <denied|timeout|auth|error>]...
  write_items.py write <capture_dir> --failed <reason> [--missing ...]

`<capture_dir>` is the ticket's own `capture_dir`, VERBATIM — the job's DAY
directory, `_raw/<slug>/<YYYY-MM-DD>`, WIKI-RELATIVE: `llm-wiki-ops run` starts
a script at the wiki root, not in the capture directory the worker stands
in, so `.` is the wrong answer here. `ticket.json` in it is read for the ticket
id, the `capture_dir` the report names, `options.workspace` and `min_date`; a
directory with none is refused unless `--ticket` says this is a hand run
(`--workspace` and `--min-date` stand in for the rest). A bare `--from` name is
looked for INSIDE the capture directory; no `--from` is `pull.json` there,
else stdin.

`since` is the run's FIRST step, and removes a stale `report.json` before it
answers: the day directory is stable across the day's pulls, every pull of a
job carries the same ticket id, and the extractor writes its own report into
this directory — so a unit that died before its last step would otherwise
leave an old `ok` for `apply` to land.

`since` answers where this pull starts, as JSON: the watermark in
`_raw/<slug>/.cursor.json` when it is there, else now minus the lookback;
never earlier than the ticket's `min_date`.

`write` takes the tasks the worker read off the connector — a JSON list on
stdin or in `--from` — and does the rest with no judgment of its own:

    [{"id": "<task-id>", "last_edited": "<ISO-8601>", "database": "<id or name>",
      "title": "<the task's own>", "status": "…", "due": "…", "assignee": "…",
      "url": "https://www.notion.so/…", "body": "<plain-text notes>",
      "summary": "<one factual line, the WORKER's words>", "junk": null | "<rule>"}]

Oldest `--cap` first, so the watermark it leaves is a clean resume point;
then the mechanical filters (databases, statuses, `min_date`, already behind
the watermark); then one file per task under `<capture_dir>/items/`:

- kept   → `items/<last-edited>--<task-id>.json`
- junked → `items/.<last-edited>--<task-id>.json`, holding the pointer and the
  rule's name and NONE of the task. The host's ledger counts a dot-file into
  its `discarded: N (junk rules)` tally and never renders one.

A task edited twice in one day is ONE file: the newer replaces the older, so
the day's ledger carries one bullet per task.

What the host's ledger reads from a kept file is `subject|title|summary|text`
(first present) and `id|source` — `pipeline/extract.py::_item_bullet`. So a
task that carries a `summary` is written with NO `title` key (the task's own
title is kept as `venue_title`, which the host does not read) and the
ledger's bullet is the worker's own words. One with no summary falls back to
`title`, and the bullet is the task's title as the host neutralizes it.
Either way what the host's folding leaves live is swapped for look-alikes in
that one field here (`host_line`): raw HTML's `<` `>`, the ` — ` the host's own
bullet puts before its pointer, a bare url, `*` and `|`.

An item with an id and no trustworthy time — none, unparseable, or more than
a day ahead of this machine's clock (one seconds/ms/µs slip) — is KEPT, filed
under the pull's own clock, marked `time_untrusted` and counted `bad_time`; it
never moves the watermark. A watermark file that is unreadable or ahead of the
clock is ignored, out loud, and the lookback stands in.

Then `report.json`, and only THEN the watermark (never backwards, never past
the clock, never by a failed pull). `captured[]` names the day directory
whenever its `items/` holds a file — not only when THIS invocation wrote one —
so a rerun never reports `captured: []` over items no ledger has; and a
report an earlier `write` on this ticket left with captures is never
downgraded: a later failure makes it `partial` and says why. `apply` mints
the extraction from `captured[]`, and the extractor regenerates the day's
ledger WHOLE.

Wiki-owned, stdlib only, imports nothing from the plugin.
"""

import argparse
import json
import os
import re
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
    """One kept message as the file the host's `_item_bullet` reads. The
    pointer is built from the safe id alone, so it needs no neutralizing."""
    doc = {"v": ITEM_V, "id": pointer_of(item, sid)}
    raw = _text(item.get(RAW_LINE_KEY))
    summary = _text(item.get("summary"))
    if summary and summary.strip():
        # No `subject`/`title` key on purpose: either would win over `summary`.
        doc["summary"] = host_line(summary.strip())
    elif raw and raw.strip():
        doc[HOST_LINE_KEY] = host_line(raw)
    doc[f"venue_{RAW_LINE_KEY}"] = raw
    doc.update(stored_of(item))
    if not trusted:
        doc["time_untrusted"] = True  # filed under the PULL's clock; the venue's own value is in `stored_of`'s keys
    body = _text(item.get("body")) or ""
    doc["body"] = body[:BODY_MAX]
    doc["body_truncated"] = len(body) > BODY_MAX
    return doc


def junk_doc(item, sid):
    return {"v": ITEM_V, "id": pointer_of(item, sid), "junk": host_line(_text(item.get("junk")) or "junk")[:80]}


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
    counts = {"fetched": len(items), "written": 0, "junked": 0, "invalid": 0, "bad_time": 0, "held_back": 0, "unsummarised": 0}
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
        names = (f"{stamp_of(when)}--{sid}.json", f".{stamp_of(when)}--{sid}.json")
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
        if _text(item.get("junk")) and item["junk"].strip():
            files.append((names[1], junk_doc(item, sid), sid))
            counts["junked"] += 1
            continue
        doc = item_doc(item, sid, trusted=trusted)
        if "summary" not in doc:
            counts["unsummarised"] += 1
        files.append((names[0], doc, sid))
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


def report_for(job, *, outcome, reason, captured, missing):
    return {
        "v": REPORT_V,
        "ticket": job.get("ticket"),
        "outcome": outcome,
        "reason": reason,
        "captured": captured,
        "written": [],
        "missing": missing,
        "discovered": [],
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
    floor = getattr(args, "min_date", None) or job.get("min_date")
    try:
        job["min_date"] = floor if isinstance(floor, str) and DAY_RE.fullmatch(floor) and from_day(floor) >= 0 else None
    except (ValueError, OverflowError):
        job["min_date"] = None  # `2026-13-45` has the shape and is no day
    return job


def source_of(directory, given):
    """Where the pull is read from — a path, or None for stdin.

    `llm-wiki-ops run` starts this script at the WIKI ROOT, not in the capture
    directory the worker stands in. So a bare name (`pull.json`) is looked for
    INSIDE the capture directory, which is where the worker wrote it; anything
    with a directory in it is as given — wiki-relative, like `capture_dir`
    itself. No `--from`: `pull.json` in the capture directory when it is
    there, else stdin. `-` is stdin outright."""
    if given == "-":
        return None
    if given is None:
        default = directory / PULL_NAME
        return default if default.is_file() or sys.stdin is None or sys.stdin.isatty() else None
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("since", "write"):
        sub = subs.add_parser(verb)
        sub.add_argument("capture_dir", help="the ticket's own `capture_dir`, WIKI-RELATIVE as the ticket spells it: the job's day directory")
        sub.add_argument("--ticket", help=f"the ticket id, when there is no {TICKET_NAME} (a hand run, or `spawn: none`)")
        sub.add_argument(f"--{INPUT_KEY}", help=f"`options.{INPUT_KEY}`, when there is no {TICKET_NAME}")
        sub.add_argument("--min-date", metavar="YYYY-MM-DD", help=f"the ticket's `min_date`, when there is no {TICKET_NAME}; overrides it when there is")
        if verb == "since":
            sub.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS, help="the FIRST pull's window")
            continue
        sub.add_argument("--from", dest="source", help=f"the pull as a JSON file: a bare name is looked for IN the capture directory, a path is wiki-relative, `-` is stdin; absent, `{PULL_NAME}` in the capture directory, else stdin")
        sub.add_argument("--cap", type=int, default=CAP, help="items per run, oldest first; at least 1" + ("" if CAP else " (absent: no cap)"))
        sub.add_argument("--partial", metavar="REASON", help="the pull stopped early, and why")
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
    return since(directory, args) if args.verb == "since" else write(directory, args)


if __name__ == "__main__":
    sys.exit(main())
