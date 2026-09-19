#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Write this ticket's `report.json` — LAST, and deterministically.

  write_report.py <wiki> --capture-dir <dir> --outcome ok
  write_report.py <wiki> --capture-dir <dir> --outcome partial --reason no_captions
  write_report.py <wiki> --capture-dir <dir> --outcome failed --reason "yt-dlp: Video unavailable" \\
                  [--missing <host> <url> <denied|timeout|auth|error>]... [--ticket <id>]

`report.json` is the only thing that travels back out of a harvest slice: the
foreman's `pipeline apply` reads it, mints one process ticket per `captured[]`
entry, and makes the widen decision on `missing[]`. Its shape is the worker
loop's (`llm-wiki-ops reference agent-loop`); this writes exactly that and
nothing else:

    {"v": 1, "ticket": …, "outcome": …, "reason": …,
     "captured": [{"item": …, "dir": …, "title": …}],
     "written": [], "missing": […], "discovered": []}

What it reads, all in the capture dir: `ticket.json` for the ticket id and the
host-derived `capture_dir` (`--ticket` stands in for the id where no spawner
wrote one), and `capture.json` for the item and title of what landed.

`captured[]` is derived, never claimed: an outcome that says something landed
(`ok`, `partial`, `unchanged`) is REFUSED unless `capture.json` is there and
names a body file that is there — so a build that aborted cannot be reported
as a capture. `skipped`, `gone` and `failed` carry an empty `captured[]`, and
every outcome but `ok` must say why.

Wiki-owned, stdlib only, imports nothing from the plugin.
"""

import argparse
import json
import sys
from pathlib import Path

REPORT_V = 1
REPORT_NAME = "report.json"
TICKET_NAME = "ticket.json"
CAPTURE_NAME = "capture.json"

OUTCOMES = ("ok", "partial", "skipped", "unchanged", "gone", "failed")
# The outcomes that say a capture is on disk.
LANDED = ("ok", "partial", "unchanged")
WHYS = ("denied", "timeout", "auth", "error")


def _read_json(path):
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def captured_of(cap_dir, capture_rel):
    """The one `captured[]` entry this capture dir supports, or None."""
    record = _read_json(Path(cap_dir) / CAPTURE_NAME)
    body = record.get("body")
    if not isinstance(body, str) or not body or not (Path(cap_dir) / body).is_file():
        return None
    return {"item": record.get("item"), "dir": capture_rel, "title": record.get("title")}


def build_report(cap_dir, capture_rel, *, ticket, outcome, reason=None, missing=()):
    """The report as a dict. Raises ValueError on a report that would lie."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if outcome != "ok" and not reason:
        raise ValueError(f"outcome {outcome!r} must say why — pass --reason")
    rows = []
    for host, url, why in missing:
        if why not in WHYS:
            raise ValueError(f"missing: why must be one of {', '.join(WHYS)}, not {why!r}")
        rows.append({"host": host, "url": url, "why": why})
    captured = []
    if outcome in LANDED:
        entry = captured_of(cap_dir, capture_rel)
        if entry is None:
            raise ValueError(
                f"outcome {outcome!r} says a capture landed, and {capture_rel}/{CAPTURE_NAME} "
                "names no body file that is there — report `failed` with the reason instead"
            )
        captured.append(entry)
    return {
        "v": REPORT_V,
        "ticket": ticket,
        "outcome": outcome,
        "reason": reason or None,
        "captured": captured,
        "written": [],
        "missing": rows,
        "discovered": [],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wiki", type=Path, help="the wiki root")
    ap.add_argument("--capture-dir", required=True, help="wiki-relative capture dir: `capture_dir` off ticket.json")
    ap.add_argument("--outcome", required=True, choices=OUTCOMES)
    ap.add_argument("--reason", default=None, help="required for every outcome but `ok`")
    ap.add_argument(
        "--missing", nargs=3, action="append", default=[], metavar=("HOST", "URL", "WHY"),
        help=f"one url that could not be reached (repeatable); WHY is one of {', '.join(WHYS)}",
    )
    ap.add_argument("--ticket", default=None, help="the ticket id. Defaults to ticket.json's `ticket`")
    args = ap.parse_args()

    cap_dir = args.wiki / args.capture_dir
    if not cap_dir.is_dir():
        sys.exit(f"write_report: {cap_dir} is not a directory")
    spawned = _read_json(cap_dir / TICKET_NAME)
    ticket = args.ticket or spawned.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        sys.exit(f"write_report: no {TICKET_NAME} in {cap_dir} and no --ticket — a report names the ticket it answers")
    # The host-derived spelling where there is one: `apply` reads `dir` back
    # as a wiki-relative path of the shape `_raw/<slug>/<leaf>`.
    capture_rel = spawned.get("capture_dir") if isinstance(spawned.get("capture_dir"), str) else None
    capture_rel = capture_rel or args.capture_dir.strip("/")
    try:
        report = build_report(
            cap_dir, capture_rel, ticket=ticket, outcome=args.outcome, reason=args.reason, missing=args.missing
        )
    except ValueError as exc:
        sys.exit(f"write_report: {exc}")
    (cap_dir / REPORT_NAME).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": f"{capture_rel}/{REPORT_NAME}", "outcome": args.outcome, "captured": len(report["captured"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
