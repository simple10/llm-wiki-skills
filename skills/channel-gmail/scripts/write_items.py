#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One gmail pull, written down: the day's item files, the cursor, `report.json`.

  write_items.py since <capture_dir> [--lookback-days 7]
  write_items.py write <capture_dir> --from <capture_dir>/pull.json \\
                 [--exclude-label L]... [--exclude-sender a@b.c|@b.c]... [--cap 200] \\
                 [--partial <reason>] [--missing <host> <url> <denied|timeout|auth|error>]...
  write_items.py write <capture_dir> --failed <reason> [--missing ...]

`<capture_dir>` is the ticket's own `capture_dir` — the job's DAY directory,
`_raw/<slug>/<YYYY-MM-DD>`, wiki-relative (`llm-wiki-ops run` starts a script
at the wiki root). `ticket.json` in it is read for the ticket id, the
`capture_dir` the report names, `options.mailbox` and `min_date`; `--ticket`
and `--mailbox` stand in where no spawner wrote one.

`since` answers where this pull starts, as JSON: the cursor's
`newest_internal_date` when `_raw/<slug>/.cursor.json` is there, else
now minus the lookback; never earlier than the ticket's `min_date`.

`write` takes the messages the worker read off the connector — a JSON list on
stdin or in `--from` — and does the rest with no judgment of its own:

    [{"id": "<msg-id>", "internal_date": <epoch-ms>, "thread": "…", "from": "…",
      "to": "…", "date": "…", "subject": "<the sender's>", "labels": ["…"],
      "attachments": [{"name": "…", "mime": "…"}], "body": "<plain text>",
      "summary": "<one factual line, the WORKER's words>", "junk": null | "<rule>"}]

Oldest `--cap` first, so the cursor it leaves is a clean resume point; then
the mechanical filters (labels, senders, `min_date`, already behind the
cursor); then one file per message under `<capture_dir>/items/`:

- kept   → `items/<internal-date>--<msg-id>.json`
- junked → `items/.<internal-date>--<msg-id>.json`, holding the id and the
  rule's name and NONE of the message. The host's ledger counts a dot-file
  into its `discarded: N (junk rules)` tally and never renders one.

What the host's ledger reads from a kept file is `subject|title|summary|text`
(first present) and `id|source` — `pipeline/extract.py::_item_bullet`. So a
message that carries a `summary` is written with NO `subject` key (the
sender's line is kept as `venue_subject`, which the host does not read) and
the ledger's bullet is the worker's own words. One with no summary falls back
to `subject`, and the bullet is the sender's line as the host neutralizes it.
Either way `<` and `>` are swapped out of that one field here, because raw
HTML is the part the host's own folding leaves open.

Then the cursor (never moved backwards, never moved by a failed pull), and
LAST `report.json`: `captured[]` names the day directory when this run wrote
at least one file, and is empty when nothing was new — `apply` mints the
extraction from it, and the extractor regenerates the day's ledger WHOLE.

Wiki-owned, stdlib only, imports nothing from the plugin.
"""

import argparse
import json
import os
import re
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
REPORT_NAME = "report.json"
TICKET_NAME = "ticket.json"
CURSOR_NAME = ".cursor.json"
ITEMS_DIRNAME = "items"  # the host's own name for it: `pipeline/extract.py::ITEMS_DIRNAME`
WHYS = ("denied", "timeout", "auth", "error")
BODY_MAX = 50_000
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_ANGLES = str.maketrans({"<": "‹", ">": "›"})


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
    """What is kept of a message beside the line the ledger reads. Attachments
    are names and MIME types, never bytes."""
    kept = {key: _text(item.get(key)) for key in ("thread", "from", "to", "date")}
    kept["labels"] = [label for label in item.get("labels") or [] if isinstance(label, str)]
    kept["attachments"] = [
        {"name": _text(one.get("name")), "mime": _text(one.get("mime"))}
        for one in item.get("attachments") or []
        if isinstance(one, dict)
    ]
    return kept


RAW_LINE_KEY = "subject"  # the venue's own one-line name for an item, in the input
HOST_LINE_KEY = "subject"  # the key the fallback is written under, which the host reads first


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


def safe_id(value):
    """An id as one filename-safe token, or None. The id is the venue's, and a
    venue's bytes do not get to name a path."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    token = _UNSAFE.sub("_", str(value)).strip("_")[:120]
    return token or None


def host_line(value):
    """The one field the host's ledger reads, with raw HTML's two characters out."""
    return value.translate(_ANGLES)


def item_doc(item, sid):
    """One kept message as the file the host's `_item_bullet` reads."""
    doc = {"v": ITEM_V, "id": host_line(pointer_of(item, sid))}
    raw = _text(item.get(RAW_LINE_KEY))
    summary = _text(item.get("summary"))
    if summary and summary.strip():
        # No `subject`/`title` key on purpose: either would win over `summary`.
        doc["summary"] = host_line(summary.strip())
    elif raw and raw.strip():
        doc[HOST_LINE_KEY] = host_line(raw)
    doc[f"venue_{RAW_LINE_KEY}"] = raw
    doc.update(stored_of(item))
    body = _text(item.get("body")) or ""
    doc["body"] = body[:BODY_MAX]
    doc["body_truncated"] = len(body) > BODY_MAX
    return doc


def junk_doc(item, sid):
    return {"v": ITEM_V, "id": host_line(pointer_of(item, sid)), "junk": host_line(_text(item.get("junk")) or "junk")[:80]}


def held_elsewhere(slice_dir, day_dir, names):
    """Is this item already in ANOTHER day's directory? A tie with the cursor
    is pulled twice, and must not become a second day's bullet."""
    for other in slice_dir.iterdir() if slice_dir.is_dir() else []:
        if other == day_dir or not DAY_RE.fullmatch(other.name):
            continue
        if any((other / ITEMS_DIRNAME / name).is_file() for name in names):
            return True
    return False


def plan(items, args, *, cursor, min_date, slice_dir, day_dir):
    """The pure half: which messages become which files, and the counts.

    Returns `(files, newest, counts)` — `files` is `[(name, doc, safe_id)]`,
    `newest` the `(when, safe_id)` the cursor moves to, or None.
    """
    counts = {"fetched": len(items), "written": 0, "junked": 0, "invalid": 0, "held_back": 0, "unsummarised": 0}
    skipped = {}
    usable = []
    for item in items:
        sid, when = (safe_id(item.get("id")), when_of(item)) if isinstance(item, dict) else (None, None)
        if sid is None or when is None:
            counts["invalid"] += 1
            continue
        usable.append((when, sid, item))
    usable.sort(key=lambda row: (row[0], row[1]))
    if args.cap and len(usable) > args.cap:
        counts["held_back"] = len(usable) - args.cap
        usable = usable[: args.cap]
    files, newest = [], None
    floor = from_day(min_date) if min_date else None
    for when, sid, item in usable:
        newest = (when, sid)
        names = (f"{stamp_of(when)}--{sid}.json", f".{stamp_of(when)}--{sid}.json")
        why = None
        if behind_cursor(when, sid, cursor) or held_elsewhere(slice_dir, day_dir, names):
            why = "already_pulled"
        elif floor is not None and when < floor:
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
        doc = item_doc(item, sid)
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
        for old in [*items_dir.glob(f"*--{sid}.json"), *items_dir.glob(f".*--{sid}.json")]:
            if old.name != name:
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


def load_job(directory, args):
    job = _read_json(directory / TICKET_NAME)
    if args.ticket:
        job["ticket"] = args.ticket
    options = job.get("options") if isinstance(job.get("options"), dict) else {}
    answer = getattr(args, INPUT_KEY, None) or options.get(INPUT_KEY)
    job[INPUT_KEY] = answer if isinstance(answer, str) and answer.strip() else None
    job.setdefault("capture_dir", directory.as_posix())
    floor = job.get("min_date")
    job["min_date"] = floor if isinstance(floor, str) and DAY_RE.fullmatch(floor) else None
    return job


def since(directory, args, *, now=None):
    job = load_job(directory, args)
    if job[INPUT_KEY] is None:
        print(f"write_items: the job names no {INPUT_KEY} — `options.{INPUT_KEY}` is this unit's one input", file=sys.stderr)
        return 1
    cursor = _read_json(directory.parent / CURSOR_NAME)
    at = cursor_when(cursor)
    start = at if at is not None else ago(now or datetime.now(timezone.utc), args.lookback_days)
    if job["min_date"]:
        start = max(start, from_day(job["min_date"]))
    answer = {
        INPUT_KEY: job[INPUT_KEY],
        "first_pull": at is None,
        "since": show(start),
        "since_day": day_of(start),
        "min_date": job["min_date"],
        "cursor": cursor or None,
    }
    print(json.dumps(answer, indent=2))
    return 0


def write(directory, args):
    job = load_job(directory, args)
    missing = [{"host": host, "url": url, "why": why} for host, url, why in args.missing]
    for entry in missing:
        if entry["why"] not in WHYS:
            print(f"write_items: --missing why must be one of {', '.join(WHYS)}", file=sys.stderr)
            return 2

    def finish(outcome, reason, captured, counts=None):
        _write_json(directory / REPORT_NAME, report_for(job, outcome=outcome, reason=reason, captured=captured, missing=missing))
        print(json.dumps({"outcome": outcome, "reason": reason, **(counts or {})}, indent=2))
        return 1 if outcome == "failed" else 0

    if args.failed:
        return finish("failed", args.failed, [])
    try:
        raw = Path(args.source).read_text(encoding="utf-8") if args.source else sys.stdin.read()
        items = json.loads(raw)
    except (OSError, ValueError) as exc:
        return finish("failed", f"the pull could not be read: {type(exc).__name__}: {exc}", [])
    if not isinstance(items, list):
        return finish("failed", "the pull is not a JSON list of items", [])

    slice_dir = directory.parent
    cursor = _read_json(slice_dir / CURSOR_NAME)
    files, newest, counts = plan(items, args, cursor=cursor, min_date=job["min_date"], slice_dir=slice_dir, day_dir=directory)
    if items and counts["invalid"] == len(items):
        return finish("failed", f"{len(items)} item(s) and none carries a usable id and time", [], counts)
    try:
        land(directory, files)
        at = cursor_when(cursor)
        if newest is not None and (at is None or newest[0] >= at):
            _write_json(slice_dir / CURSOR_NAME, cursor_for(*newest))
            counts["cursor"] = cursor_for(*newest)
        if args.source and Path(args.source).resolve().is_relative_to(directory.resolve()):
            Path(args.source).unlink(missing_ok=True)  # consumed: the items are the record
    except OSError as exc:
        return finish("failed", f"{type(exc).__name__}: {exc}", [], counts)

    reasons = [args.partial] if args.partial else []
    if counts["held_back"]:
        reasons.append(f"capped at {args.cap}: {counts['held_back']} newer item(s) left for the next pull")
    if not files and not reasons:
        reasons.append("nothing new since the cursor")
    captured = [{"item": job.get("item") or job.get("target"), "dir": job["capture_dir"], "title": None}] if files else []
    partial = bool(args.partial or counts["held_back"])
    return finish("partial" if partial else "ok", "; ".join(reasons) or None, captured, counts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("since", "write"):
        sub = subs.add_parser(verb)
        sub.add_argument("capture_dir", help="the ticket's own `capture_dir`: the job's day directory")
        sub.add_argument("--ticket", help=f"the ticket id, when there is no {TICKET_NAME}")
        sub.add_argument(f"--{INPUT_KEY}", help=f"`options.{INPUT_KEY}`, when there is no {TICKET_NAME}")
        if verb == "since":
            sub.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS, help="the FIRST pull's window")
            continue
        sub.add_argument("--from", dest="source", help="the pull as a JSON file; stdin when absent")
        sub.add_argument("--cap", type=int, default=CAP, help="items per run, oldest first; 0 for none")
        sub.add_argument("--partial", metavar="REASON", help="the pull stopped early, and why")
        sub.add_argument("--failed", metavar="REASON", help="nothing was pulled, and why; writes the report alone")
        sub.add_argument("--missing", nargs=3, action="append", default=[], metavar=("HOST", "URL", "WHY"))
        venue_args(sub)
    args = parser.parse_args(argv)
    directory = Path(args.capture_dir)
    if not DAY_RE.fullmatch(directory.name):
        print(f"write_items: {directory.name} is not a day directory (`YYYY-MM-DD`)", file=sys.stderr)
        return 2
    return since(directory, args) if args.verb == "since" else write(directory, args)


if __name__ == "__main__":
    sys.exit(main())
