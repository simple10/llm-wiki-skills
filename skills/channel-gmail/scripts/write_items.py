#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Both steps of one gmail channel: the day's items, then the day's ledger.

  harvest:
  write_items.py since <capture_dir> [--lookback-days 7]
  write_items.py write <capture_dir> [--from pull.json] \\
                 [--exclude-label L]... [--exclude-sender a@b.c|@b.c]... [--cap 200] \\
                 [--partial <reason>] [--missing <host> <url> <denied|timeout|auth|error>]...
  write_items.py write <capture_dir> --failed <reason> [--missing ...]

  process:
  write_items.py ledger <capture_dir> --dest <dest> [--from lines.json] [--partial <reason>]

`<capture_dir>` is the ticket's own `capture_dir`, VERBATIM — the job's DAY
directory, `_raw/<slug>/<YYYY-MM-DD>`, WIKI-RELATIVE: `llm-wiki-ops run` starts
a script at the wiki root, not in the capture directory the worker stands
in, so `.` is the wrong answer here. `ticket.json` in it is read for the ticket
id, the `capture_dir` the report names, `options.mailbox` and `min_date`; a
directory with none is refused unless `--ticket` says this is a hand run
(`--mailbox` and `--min-date` stand in for the rest). A bare `--from` name is
looked for INSIDE the capture directory; no `--from` is `pull.json` there,
else stdin.

`since` is the run's FIRST step, and removes a stale `report.json` before it
answers: the day directory is stable across the day's pulls, every pull of a
job carries the same ticket id, and the extractor writes its own report into
this directory — so a unit that died before its last step would otherwise
leave an old `ok` for `apply` to land.

`since` answers where this pull starts, as JSON: the cursor's
`newest_internal_date` when `_raw/<slug>/.cursor.json` is there, else
now minus the lookback; never earlier than the ticket's `min_date`.

`write` takes the messages the worker read off the connector — a JSON list on
stdin or in `--from` — and writes them down as they arrived, judging nothing:

    [{"id": "<msg-id>", "internal_date": <epoch-ms>, "thread": "…", "from": "…",
      "to": "…", "date": "…", "subject": "<the sender's>", "labels": ["…"],
      "attachments": [{"name": "…", "mime": "…"}], "body": "<plain text>"}]

Oldest `--cap` first, so the cursor it leaves is a clean resume point; then
the mechanical filters (labels, senders, `min_date`, already behind the
cursor); then one file per message under
`<capture_dir>/items/<internal-date>--<msg-id>.json`, and a flat
`capture.json` naming that directory as the pull's payload.

`ledger` is the process step, and the one that judges. It reads the day's
items, takes one line per item from `--from` —

    [{"id": "gmail:<msg-id>", "line": "<one factual line, in the WIKI's words>",
      "junk": null | "<the rule that discards it>"}]

— and writes the day's page WHOLE through the front door: `page create`, or
`page edit` over the page a earlier pull of the same day already left. An item
with no line keeps its bullet, with the sender's own subject standing in.

What the plugin's extractor reads from an item file, on a day it reaches
first, is `subject|title|summary|text` (first present) and `id|source`
(`pipeline/extract.py::_item_bullet`) — so the sender's line is stored twice:
neutralized under `subject`, which is what such a bullet would carry, and
untouched under `venue_subject`, which nothing reads by accident. What a
folding leaves live is swapped for look-alikes (`host_line`): raw HTML's `<`
`>`, the ` — ` a bullet puts before its pointer, a bare url, `*` and `|`.

An item with an id and no trustworthy time — none, unparseable, or more than
a day ahead of this machine's clock (one seconds/ms/µs slip) — is KEPT, filed
under the pull's own clock, marked `time_untrusted` and counted `bad_time`; it
never moves the cursor. A cursor file that is unreadable or ahead of the
clock is ignored, out loud, and the lookback stands in.

Then `report.json`, and only THEN the cursor (never backwards, never past
the clock, never by a failed pull). `captured[]` names the day directory
whenever its `items/` holds a file — not only when THIS invocation wrote one —
so a rerun never reports `captured: []` over items no ledger has; and a
report an earlier `write` on this ticket left with captures is never
downgraded: a later failure makes it `partial` and says why. `apply` mints
the process ticket from `captured[]`, and `ledger` regenerates the day's
page WHOLE. A `ledger` report is the other shape: `written[]` names the page,
`captured[]` is empty, and a stale report goes first there too.

Wiki-owned, stdlib only, imports nothing from the plugin.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path

VENUE = "gmail"
INPUT_KEY = "mailbox"  # the unit's one `watch.inputs` answer, under the ticket's `options`
LOOKBACK_DAYS = 7
CAP = 200

REPORT_V = 1
ITEM_V = 1
CAPTURE_V = 1
REPORT_NAME = "report.json"
CAPTURE_NAME = "capture.json"
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
# What a bullet swaps beyond those: the three `extract.py::_plain` turns, so one
# line reads the same whoever wrote the page — this unit's process step, or the
# plugin's extractor on a day it reaches first.
_PLAIN = str.maketrans({"`": "'", "[": "(", "]": ")"})
PULL_NAME = "pull.json"
LINES_NAME = "lines.json"
BULLET_MAX = 200  # `extract.py::_plain`'s cap, kept so a ledger reads the same from either writer
LEDGER_TYPE = "ledger"
OPS = "llm-wiki-ops"
# What a nested front-door call must NOT inherit from the one that ran this
# script. `CLAUDE_PROJECT_DIR` is the harness's project directory, never a wiki
# root: the `cwd=<root>` this script was handed is what binds the nested call
# to THIS wiki.
NOT_INHERITED = ("CLAUDE_PROJECT_DIR",)
AHEAD_MS = 86_400_000  # how far ahead of this machine's clock an item's own time is still believed: a day of skew


# ------------------------------------------------------------------ the venue


def when_of(item):
    """A message's own clock — Gmail's `internalDate`, epoch milliseconds — or None."""
    value = item.get("internal_date")
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    return value if isinstance(value, int) and value >= 0 else None


def stamp_of(when):
    """The sortable head of an item's filename: 13 digits, so names sort by time."""
    return f"{when:013d}"


def day_of(when):
    return datetime.fromtimestamp(when / 1000, timezone.utc).strftime("%Y-%m-%d")


def from_day(day):
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def ago(now, days):
    return int((now - timedelta(days=days)).timestamp() * 1000)


def pointer_of(item, safe_id):
    return f"{VENUE}:{safe_id}"


def show(when):
    """A time as this venue's connector is asked for it: epoch milliseconds."""
    return when


def cursor_when(cursor):
    value = cursor.get("newest_internal_date")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def cursor_for(when, safe_id):
    return {"newest_internal_date": when, "newest_id": safe_id}


def behind_cursor(when, safe_id, cursor):
    at = cursor_when(cursor)
    if at is None:
        return False
    return when < at or (when == at and safe_id == cursor.get("newest_id"))


def venue_args(parser):
    parser.add_argument("--exclude-label", action="append", default=[], metavar="LABEL")
    parser.add_argument(
        "--exclude-sender", action="append", default=[], metavar="ADDR", help="an address, or `@domain` for a whole one"
    )


def filtered_by(item, args):
    """The mechanical filter this message fails, by name, or None."""
    labels = {label for label in item.get("labels") or [] if isinstance(label, str)}
    if labels & set(args.exclude_label):
        return "label"
    sender = parseaddr(item.get("from") if isinstance(item.get("from"), str) else "")[1].lower()
    for entry in args.exclude_sender:
        entry = entry.lower()
        if sender and (sender == entry or (entry.startswith("@") and sender.endswith(entry))):
            return "sender"
    return None


def stored_of(item):
    """What is kept of a message beside the sender's own line. Attachments are
    names and MIME types, never bytes."""
    kept = {key: _text(item.get(key)) for key in ("thread", "from", "to", "date")}
    kept["labels"] = [label for label in item.get("labels") or [] if isinstance(label, str)]
    kept["attachments"] = [
        {"name": _text(one.get("name")), "mime": _text(one.get("mime"))}
        for one in item.get("attachments") or []
        if isinstance(one, dict)
    ]
    return kept


RAW_LINE_KEY = "subject"  # the venue's own one-line name for an item, in the input
HOST_LINE_KEY = "subject"  # where the neutralized copy lives: the bullet's line when the process step wrote none


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
    """A venue's own line with what a bullet's folding leaves open swapped
    out. A folding pass turns a backtick and the two square brackets and caps
    the line; it leaves raw HTML, the ` — ` a bullet puts before its pointer,
    a bare url a renderer autolinks, `*` emphasis and a table's `|`.
    Look-alikes, not deletions: the line still reads as what was written."""
    return _WWW.sub("www․", value.translate(_SWAPS).replace("://", ":∕∕"))


def item_doc(item, sid, *, trusted=True):
    """One message as it arrived, written down. Harvest judges nothing: the
    line the ledger carries is the process step's, off this file. The pointer
    is built from the safe id alone, so it needs no neutralizing."""
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


def capture_doc(job, day, *, now):
    """The flat `capture.json` every unit's harvest leaves. Nothing on the
    ledger route reads it — the route is decided by the job's `dest` before
    anything in the directory is opened — so it carries the day and its
    payload and no key a host verb owns. `body` is the day's items DIRECTORY:
    this route's payload is a message per file, accumulated across the day's
    pulls, and there is no one file that is the pull."""
    return {
        "v": CAPTURE_V,
        "slug": job.get("slug"),
        "item": job.get("item") or job.get("target"),
        "title": day,  # the day directory's own name, matched by DAY_RE: no venue text reaches a title here
        "body": ITEMS_DIRNAME,
        "content_type": "application/json",
        "fetched_at": datetime.fromtimestamp(now / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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


def source_of(directory, given, default=PULL_NAME):
    """Where the pull is read from — a path, or None for stdin.

    `llm-wiki-ops run` starts this script at the WIKI ROOT, not in the capture
    directory the worker stands in. So a bare name (`pull.json`) is looked for
    INSIDE the capture directory, which is where the worker wrote it; anything
    with a directory in it is as given — wiki-relative, like `capture_dir`
    itself. No `--from`: the step's own default name in the capture directory
    when it is there, else stdin. `-` is stdin outright."""
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
        if captured:
            _write_json(directory / CAPTURE_NAME, capture_doc(job, directory.name, now=now))
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


# ----------------------------------------------------------------- the ledger


def bullet_line(value):
    """One item's text as one bullet's worth: folded to a single line, the
    look-alikes `host_line` swaps, the three characters `extract.py::_plain`
    turns, and capped. Whoever writes the day's page — this step, or the
    plugin's extractor over the same items — a bullet reads the same and
    forges nothing."""
    folded = host_line(" ".join(str(value).split()).translate(_PLAIN))
    return folded[: BULLET_MAX - 1] + "…" if len(folded) > BULLET_MAX else folded


def read_items(day_dir):
    """The day's item files, oldest first — the filename is the time. A
    dot-file is a discard an older copy of this unit filed: counted, never
    rendered, exactly as the extractor counts it."""
    items_dir = day_dir / ITEMS_DIRNAME
    paths = sorted(path for path in items_dir.iterdir() if path.is_file() and id_in(path.name)) if items_dir.is_dir() else []
    return [(path.name, _read_json(path)) for path in paths]


def verdicts_in(rows):
    """The process step's judgment, by the pointer the item file carries:
    `{"<pointer>": {"line": …, "junk": …}}`. Anything else in the list is
    dropped — a verdict with no id names no item."""
    found = {}
    for row in rows if isinstance(rows, list) else []:
        pointer = _text(row.get("id")) if isinstance(row, dict) else None
        if pointer:
            found[pointer] = row
    return found


def ledger_body(items, verdicts):
    """The day's page body, regenerated WHOLE: one bullet per kept item in the
    order the filenames give, then the tally of what the junk rules dropped.

    An item the process step wrote no line for keeps its bullet, with the
    sender's own subject standing in — a missing line loses a day's order or a
    wording, never the item."""
    bullets, discarded, unjudged = [], 0, 0
    for name, doc in items:
        pointer = _text(doc.get("id")) or name
        verdict = verdicts.get(pointer) or {}
        if name.startswith(".") or (_text(verdict.get("junk")) or "").strip():
            discarded += 1
            continue
        line = _text(verdict.get("line")) or ""
        if not line.strip():
            unjudged += 1
            line = _text(doc.get(HOST_LINE_KEY)) or _text(doc.get(f"venue_{RAW_LINE_KEY}")) or ""
        head = f"{bullet_line(line)} — " if line.strip() else ""
        bullets.append(f"- {head}{bullet_line(pointer)}")
    body = "\n".join(bullets) + f"\n\ndiscarded: {discarded} (junk rules)\n"
    return body, len(bullets), discarded, unjudged


def _ops(argv, body):
    """The front door, as an ARGUMENT LIST and never a shell line: every value
    here is venue text one step removed, and `;$(…)` in a subject is a command
    on a shell line. The re-entry guard and the project binding of the call
    that ran THIS script are not this call's."""
    env = {key: value for key, value in os.environ.items() if key not in NOT_INHERITED}
    try:
        return subprocess.run([OPS, *argv], input=body, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"could not start {OPS}: {type(exc).__name__}: {exc}")


def _said(done):
    return " ".join((done.stderr or done.stdout or "").split())[:300]


def write_page(dest, day, keys, body):
    """`(<wiki-relative path>, None)`, or `(None, <why>)`.

    `page create` refuses a title that is already a page with exit 2 — the
    filename IS the title — and the day's ledger is regenerated whole on every
    pull of that day, so the refusal is the ordinary second run: edit the
    standing page rather than mint a second one for the same day.

    `status=` clears the `draft` create would otherwise set. A ledger is
    outside the corpus and the lifecycle by location, and a draft here would
    put every day of every channel into curate's list. An edit may not carry
    it — `status=` is `page curate`'s, `revise`'s and `retire`'s — and does
    not need to: the page it edits was created without one."""
    rel = f"{dest.rstrip('/')}/{day}.md"
    made = _ops(["page", "create", f"title={day}", f"dest={dest}", *keys, "status=", "--stdin"], body)
    if made.returncode == 0:
        return rel, None
    edited = _ops(["page", "edit", rel, *keys, "--stdin"], body)
    if edited.returncode == 0:
        return rel, None
    return None, f"`page create` said {_said(made)!r}; `page edit` said {_said(edited)!r}"


def ledger(directory, args, *, now=None):
    # FIRST, as `since` does it: the report in this directory answers the
    # harvest that filled the day, and `apply` checks neither the ticket nor
    # the step a report answers. A process run that died before its last step
    # would otherwise leave the harvest's `ok` to land as this step's.
    (directory / REPORT_NAME).unlink(missing_ok=True)
    job = load_job(directory, args)
    day = directory.name
    dest = str(args.dest or job.get("dest") or "").strip().rstrip("/")

    def finish(outcome, reason, *, written=(), counts=None):
        """The report, LAST, and a PROCESS report: `written[]` is the page
        this step wrote, `captured[]` is empty — the day was captured by the
        harvest that filled it, and naming it again would mint a second
        extraction of the ledger this step has already written."""
        _write_json(
            directory / REPORT_NAME,
            report_for(job, outcome=outcome, reason=reason, captured=[], missing=[], written=written),
        )
        print(json.dumps({"outcome": outcome, "reason": reason, "written": list(written), **(counts or {})}, indent=2))
        return 1 if outcome == "failed" else 0

    if not dest:
        print(
            "write_items: --dest is the ticket's `dest`, wiki-relative and verbatim — the job's ledger route, "
            "`research/channels/<slug>`; a process ticket carries it, and a hand run reads it off `pipeline show <slug>`",
            file=sys.stderr,
        )
        return 2
    items = read_items(directory)
    if not items:
        return finish("skipped", f"{day} holds no {ITEMS_DIRNAME}/ item to write a ledger from")
    source = source_of(directory, args.source, default=LINES_NAME)
    try:
        raw = source.read_text(encoding="utf-8") if source is not None else ("" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read())
        rows = json.loads(raw) if raw.strip() else []
    except (OSError, ValueError) as exc:
        return finish("failed", f"the lines could not be read: {type(exc).__name__}: {exc}")
    body, kept, discarded, unjudged = ledger_body(items, verdicts_in(rows))
    counts = {"items": len(items), "kept": kept, "discarded": discarded, "unjudged": unjudged}
    rel, why = write_page(dest, day, [f"type={LEDGER_TYPE}", f"channel={job.get('slug') or day}", f"date={day}", f"items={kept}", "extracted=true"], body)
    if rel is None:
        return finish("failed", f"the ledger was not written: {why}", counts=counts)
    reasons = [args.partial] if args.partial else []
    if unjudged:
        reasons.append(f"{unjudged} item(s) carried no line from this step: the sender's own subject stands in")
    return finish("partial" if args.partial else "ok", "; ".join(reasons) or None, written=[rel], counts=counts)


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
        if verb == "ledger":
            sub.add_argument("--dest", help="the ticket's `dest`, wiki-relative and verbatim: the job's ledger route")
            sub.add_argument("--from", dest="source", help=f"this step's lines as a JSON file: a bare name is looked for IN the capture directory, `-` is stdin; absent, `{LINES_NAME}` there")
            sub.add_argument("--partial", metavar="REASON", help="only some of the day was judged, and why")
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
    if args.verb == "since":
        return since(directory, args)
    return ledger(directory, args) if args.verb == "ledger" else write(directory, args)


if __name__ == "__main__":
    sys.exit(main())
