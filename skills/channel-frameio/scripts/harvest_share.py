# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Capture a Frame.io share's leaves under ONE ticket, and write its report.

platform: frameio
scope: platform-general (no hardcoded share ids or hosts).

One download ticket captures the whole share. Nothing fans a share's leaves
out into further tickets, so this script is the fan-out: it takes the leaf
manifest `enumerate_tree.py` wrote, plans which leaves this ticket still owes,
captures each one into its OWN capture dir beside the ticket's
(`_raw/<slug>/<leaf>--<hash8>/`, via `capture_job.py`), and writes
`report.json` into the ticket's capture dir listing every leaf that landed.
The foreman's `pipeline apply` mints one process ticket per `captured[].dir`;
this script touches no queue.

**The plan** is the ticket's own filters applied by this unit, because no one
else applies them:

- `known[]` — a leaf whose view URL is already a page's `resource` is
  skipped. Matched exactly, the way the page carries it. This is also the
  whole resume state: a share too large for one slice finishes on a later
  ticket, which is handed the pages the earlier one landed.
- `harvest.exclude_urls` — an entry excludes a leaf when it equals the leaf's
  view URL, is a prefix of it, or (written with a `*`) globs it. The host
  defines no matching rule for the key, so that reading is this unit's own.
- `harvest.scope` — read literally against the ticket's `target`: `page`
  keeps a leaf whose URL IS the target, `section` keeps leaves under the
  target's path, `domain` keeps leaves on the target's registered domain. A
  leaf viewer is `/share/<share-id>/view/<asset-id>`, so `domain` holds for
  every share URL, `section` holds for a share ROOT and excludes every leaf
  of a FOLDER target, and `page` excludes every leaf of any share or folder.
  A plan that scope emptied is reported `failed` with that reason — loud,
  where the old host-side filter dropped the leaves in silence.
- `harvest.access`, `min_date` and `harvest.assets` are not consulted: a
  guest share has no free/paid split, its listing carries no dates, and on
  this venue the asset IS the item rather than an attachment of a page.

**The run is bounded and resumable.** A slice is killed at thirty minutes,
and a killed slice that left no report is a failed ticket. So leaves are
taken in manifest order (stable: folder walk order), a leaf whose dir already
holds a `capture.json` is counted and not re-fetched, `report.json` is
rewritten at the end of EVERY pass, and a pass stops starting new leaves once
`--budget-seconds` (one tool call's worth) or `--slice-seconds` (measured
from the first pass, recorded in `plan.json`) runs out. Run it again while
its summary says `"stop": "budget"`; stop when it says `done` or `slice`.
A report written before every leaf landed says `partial`, with the count in
its `reason`.

A target that is itself a leaf viewer (`.../view/<asset-id>`) needs no
manifest: it is one leaf, captured into the ticket's own capture dir.

Usage:
  uv run harvest_share.py <capture_dir> [--manifest tree.json] [--plan-only]
      [--budget-seconds N] [--slice-seconds N] [--pause-seconds N]
      [--retry-failed] [--title-strip S] [--author A] [--group G]
      [--group-type T] [--timeout-ms N]
      [--target URL --slug SLUG --ticket ID]     # no ticket.json: hand run

`<capture_dir>` is the ticket's capture dir — `_raw/<slug>/<one>` from the
wiki root, which is where the front door's `run` starts a script. The manifest
defaults to `<capture_dir>/tree.json`.

Outputs one JSON summary on stdout: {"outcome", "reason", "planned",
"captured", "failed", "remaining", "skipped": {"known", "excluded", "scope",
"duplicate"}, "stop": "done|budget|slice|plan-only", "report"}.
Exit 0 when the report's outcome is `ok`, `partial` or `skipped`; 1 when it
is `failed`; 2 when the inputs do not add up and no report could be written.

History:
  2026-07-14  created — first Frame.io share harvest.
  2026-07-29  packaged into the channel-frameio skill unit.
  2026-08-18  stopped driving the queue: emitted one bounded `discovered`
              row for a host-side apply to queue as per-leaf jobs.
  2026-09-19  ported to the ticket contract. That host half is gone — nothing
              queues discovered pages — so the emitter became the driver: it
              plans the leaves from the ticket's own filters, captures each
              into its own `_raw/<slug>/<leaf>/`, and writes `report.json`.
              The URL/byte bounds and `--skip` went with the row they bounded;
              `known[]` is the resume state now.
"""

import argparse
import fnmatch
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from capture_record import (
    CAPTURE_NAME,
    REPORT_NAME,
    TICKET_NAME,
    host_of,
    leaf_dir_name,
    leaf_ids,
    now_utc,
    read_json,
    run,
    write_json,
)

PLAN_NAME = "plan.json"
ERROR_NAME = "error.json"
RAW_DIRNAME = "_raw"

SCOPES = ("page", "section", "domain")

#: One pass stays under a ten-minute tool call; the slice is killed at 1800 s,
#: so no new leaf starts past 1500 — a video still has to finish downloading.
BUDGET_SECONDS = 480
SLICE_SECONDS = 1500
PAUSE_SECONDS = 2.0


class Unusable(Exception):
    """The inputs do not add up — exit 2, nothing fetched, no report."""


# ------------------------------------------------------------------ the plan


def leaves_of(manifest):
    """The manifest's leaf list, or a refusal naming what was handed over."""
    if not isinstance(manifest, dict):
        raise Unusable("manifest is not a JSON object — pass the file enumerate_tree.py --out wrote")
    leaves = manifest.get("leaves")
    if not isinstance(leaves, list):
        raise Unusable("manifest has no `leaves` list — pass the file enumerate_tree.py --out wrote")
    for i, leaf in enumerate(leaves):
        if not isinstance(leaf, dict) or not isinstance(leaf.get("view_url"), str) or not leaf["view_url"]:
            raise Unusable(f"leaves[{i}] carries no view_url")
    return leaves


def _registered(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def in_scope(view_url: str, target: str, scope: str) -> bool:
    """`harvest.scope`, read literally against the ticket's target."""
    if scope == "page":
        return view_url.rstrip("/") == target.rstrip("/")
    leaf, root = urlsplit(view_url), urlsplit(target)
    if scope == "section":
        prefix = root.path.rstrip("/") + "/"
        return host_of(view_url) == host_of(target) and (leaf.path.rstrip("/") + "/").startswith(prefix)
    return _registered(host_of(view_url)) == _registered(host_of(target))


def excluded(view_url: str, patterns) -> bool:
    for pattern in patterns or []:
        if not isinstance(pattern, str) or not pattern:
            continue
        if "*" in pattern:  # `?` opens a query string in a URL, so only `*` marks a glob
            if fnmatch.fnmatchcase(view_url, pattern):
                return True
        elif view_url == pattern or view_url.startswith(pattern):
            return True
    return False


def known_resources(ticket: dict) -> set:
    entries = ticket.get("known")
    if not isinstance(entries, list):
        return set()
    return {e["resource"] for e in entries if isinstance(e, dict) and isinstance(e.get("resource"), str)}


def plan_leaves(leaves, ticket: dict) -> dict:
    """Which leaves this ticket owes, in manifest order, each with its dir.

    Pure: a manifest's leaves and a ticket in, `{"leaves": [...], "skipped":
    {...}}` out. A leaf is `{item, dir, name, path, asset_id}`; `dir` is
    wiki-relative and exactly `_raw/<slug>/<one component>`, the only shape
    `apply` mints a process ticket for.
    """
    slug, target = ticket["slug"], ticket["target"]
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    scope = harvest.get("scope") if harvest.get("scope") in SCOPES else "domain"
    known = known_resources(ticket)
    skipped = {"known": 0, "excluded": 0, "scope": 0, "duplicate": 0}
    planned, seen = [], set()
    for leaf in leaves:
        url = leaf["view_url"]
        if url in seen:
            skipped["duplicate"] += 1
        elif not in_scope(url, target, scope):
            skipped["scope"] += 1
        elif excluded(url, harvest.get("exclude_urls")):
            skipped["excluded"] += 1
        elif url in known:
            skipped["known"] += 1
        else:
            path_bits = [p for p in (leaf.get("path") or []) if isinstance(p, str)]
            planned.append(
                {
                    "item": url,
                    "dir": f"{RAW_DIRNAME}/{slug}/{leaf_dir_name(url, leaf.get('name'), path_bits)}",
                    "name": leaf.get("name") or None,
                    "path": path_bits,
                    "asset_id": leaf.get("asset_id") or leaf_ids(url)[1],
                }
            )
        seen.add(url)
    return {"scope": scope, "leaves": planned, "skipped": skipped}


def single_leaf_plan(ticket: dict) -> dict:
    """A target that IS a leaf viewer: one leaf, in the ticket's own dir."""
    url = ticket["target"]
    skipped = {"known": 0, "excluded": 0, "scope": 0, "duplicate": 0}
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    leaves = []
    if url in known_resources(ticket) and not ticket.get("refresh"):
        skipped["known"] = 1
    elif excluded(url, harvest.get("exclude_urls")):
        skipped["excluded"] = 1
    else:
        leaves = [{"item": url, "dir": ticket["capture_dir"], "name": None, "path": [], "asset_id": leaf_ids(url)[1]}]
    return {"scope": harvest.get("scope") or "domain", "leaves": leaves, "skipped": skipped}


# ---------------------------------------------------------------- the report


def leaf_state(root: Path, leaf: dict, ticket_id=None):
    """`("captured", title)`, `("failed", why)` or `("pending", None)`.

    Read off the leaf's own dir, so a second pass — or a second script — sees
    what the first one left. `captured` means a `capture.json` naming a body
    file that is there: exactly what the extractor will ask of it. A recorded
    failure counts only for the ticket that recorded it — a share's capture
    dirs outlive a ticket, and a later ticket owes the leaf a fresh attempt.
    """
    directory = root / leaf["dir"]
    record = read_json(directory / CAPTURE_NAME)
    if record and isinstance(record.get("body"), str) and (directory / record["body"]).is_file():
        title = record.get("title")
        return "captured", title if isinstance(title, str) else None
    error = read_json(directory / ERROR_NAME)
    if error and error.get("ticket") == ticket_id:
        return "failed", error.get("why") if error.get("why") in ("denied", "timeout", "auth", "error") else "error"
    return "pending", None


def report_of(ticket: dict, plan: dict, states: dict) -> dict:
    """The ticket's `report.json`. Pure: `states` maps a leaf's `item` to
    `leaf_state()`'s answer, and a leaf it does not name is pending."""
    captured, missing, pending = [], [], 0
    for leaf in plan["leaves"]:
        state, detail = states.get(leaf["item"], ("pending", None))
        if state == "captured":
            captured.append({"item": leaf["item"], "dir": leaf["dir"], "title": detail})
        elif state == "failed":
            missing.append({"host": host_of(leaf["item"]), "url": leaf["item"], "why": detail or "error"})
        else:
            pending += 1
    planned, skipped = len(plan["leaves"]), plan["skipped"]
    if planned == 0:
        if skipped["known"]:
            outcome, reason = "skipped", f"known: all {skipped['known']} leaves in the plan are already pages"
        elif skipped["scope"]:
            outcome = "failed"
            reason = (
                f"harvest.scope={plan['scope']} excludes all {skipped['scope']} leaves of {ticket['target']} — "
                f"a leaf is /share/<share-id>/view/<asset-id>; set harvest.scope=domain"
            )
        elif skipped["excluded"]:
            outcome, reason = "skipped", f"harvest.exclude_urls excludes all {skipped['excluded']} leaves"
        else:
            outcome, reason = "failed", "the share enumerated no leaves"
    elif len(captured) == planned:
        outcome, reason = "ok", None
    elif captured:
        outcome = "partial"
        reason = f"{len(captured)} of {planned} leaves captured; {len(missing)} failed, {pending} not reached"
    else:
        outcome = "failed"
        reason = f"0 of {planned} leaves captured; {len(missing)} failed, {pending} not reached"
    return {
        "v": 1,
        "ticket": ticket.get("ticket"),
        "outcome": outcome,
        "reason": reason,
        "captured": captured,
        "written": [],
        "missing": missing,
        "discovered": [],
    }


# ------------------------------------------------------------------- the run


def wiki_root_of(directory: Path, capture_dir: str) -> Path:
    """The wiki root, read off where the ticket's capture dir actually is.

    `capture_dir` is wiki-relative and exactly three components, so the root
    is three levels up — and the two must agree, or every leaf dir composed
    from the slug would land somewhere the slice was never granted.
    """
    resolved = directory.resolve()
    parts = Path(capture_dir).parts
    if len(parts) != 3 or parts[0] != RAW_DIRNAME or ".." in parts:
        raise Unusable(f"capture_dir {capture_dir!r} is not of the form {RAW_DIRNAME}/<slug>/<one>")
    root = resolved.parents[2]
    if resolved != (root / capture_dir).resolve():
        raise Unusable(f"{directory} is not the ticket's capture_dir {capture_dir!r}")
    return root


def load_ticket(directory: Path, args) -> dict:
    ticket = read_json(directory / TICKET_NAME) or {}
    for key, value in (("target", args.target), ("slug", args.slug), ("ticket", args.ticket)):
        if value:
            ticket[key] = value
    for key in ("target", "slug"):
        if not isinstance(ticket.get(key), str) or not ticket[key]:
            raise Unusable(f"no `{key}`: none in {directory / TICKET_NAME} and none on the command line")
    if not isinstance(ticket.get("capture_dir"), str):
        resolved = directory.resolve()
        ticket["capture_dir"] = f"{RAW_DIRNAME}/{ticket['slug']}/{resolved.name}"
    if Path(ticket["capture_dir"]).parts[1:2] != (ticket["slug"],):
        raise Unusable(f"capture_dir {ticket['capture_dir']!r} is not under {RAW_DIRNAME}/{ticket['slug']}")
    return ticket


def capture_cmd(root: Path, ticket: dict, leaf: dict, args) -> list:
    cmd = ["uv", "run", str(Path(__file__).resolve().parent / "capture_job.py"), str(root / leaf["dir"])]
    cmd += ["--root", str(root), "--url", leaf["item"], "--slug", ticket["slug"]]
    if leaf.get("name"):
        cmd += ["--name", leaf["name"]]
    for bit in leaf.get("path") or []:
        cmd += ["--path", bit]
    for flag, value in (
        ("--title-strip", args.title_strip),
        ("--author", args.author),
        ("--group", args.group),
        ("--group-type", args.group_type),
        ("--timeout-ms", args.timeout_ms),
    ):
        if value is not None:
            cmd += [flag, str(value)]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture_dir", type=Path, help="the ticket's capture dir — where ticket.json is")
    ap.add_argument("--manifest", type=Path, default=None, help="tree.json from enumerate_tree.py (default: <capture_dir>/tree.json)")
    ap.add_argument("--plan-only", action="store_true", help="write plan.json and print the summary; fetch nothing, report nothing")
    ap.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS, help=f"start no new leaf after this long in THIS pass (default {BUDGET_SECONDS})")
    ap.add_argument("--slice-seconds", type=float, default=SLICE_SECONDS, help=f"start no new leaf this long after the FIRST pass began (default {SLICE_SECONDS}; the slice is killed at 1800)")
    ap.add_argument("--pause-seconds", type=float, default=PAUSE_SECONDS, help="pause between leaves — one reader, not a crawler")
    ap.add_argument("--retry-failed", action="store_true", help="re-fetch leaves an earlier pass recorded as failed")
    ap.add_argument("--title-strip", default=None, help="share-wide suffix to trim off every captured title")
    ap.add_argument("--author", default=None)
    ap.add_argument("--group", default=None)
    ap.add_argument("--group-type", dest="group_type", default=None)
    ap.add_argument("--timeout-ms", type=int, default=None, help="passed through to capture_asset.py")
    ap.add_argument("--target", default=None, help="the share URL, when there is no ticket.json")
    ap.add_argument("--slug", default=None, help="the job's slug, when there is no ticket.json")
    ap.add_argument("--ticket", default=None, help="the ticket id, when there is no ticket.json")
    args = ap.parse_args()

    started = time.monotonic()
    directory = args.capture_dir
    try:
        if not directory.is_dir():
            raise Unusable(f"{directory} is not a directory — pass the ticket's capture dir")
        ticket = load_ticket(directory, args)
        root = wiki_root_of(directory, ticket["capture_dir"])
        if leaf_ids(ticket["target"])[1]:
            plan = single_leaf_plan(ticket)
        else:
            manifest_path = args.manifest or directory / "tree.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Unusable(f"{manifest_path} is not readable as JSON — run enumerate_tree.py first ({exc})") from None
            plan = plan_leaves(leaves_of(manifest), ticket)
    except Unusable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # `first_pass_epoch` survives across passes OF ONE TICKET: the slice clock
    # started with the first, and a later pass that reset it would run into
    # the kill. A plan.json another ticket left in this dir (a share's capture
    # dir is the same on every pull) says nothing about this slice's clock.
    ticket_id = ticket.get("ticket")
    earlier = read_json(directory / PLAN_NAME) or {}
    same_ticket = ticket_id is not None and earlier.get("ticket") == ticket_id
    first_pass_at = earlier.get("first_pass_epoch") if same_ticket else None
    if not isinstance(first_pass_at, (int, float)):
        first_pass_at = time.time()
    write_json(
        directory / PLAN_NAME,
        {"ticket": ticket_id, "planned_at": now_utc(), "first_pass_epoch": first_pass_at, **plan},
    )

    stop = "plan-only" if args.plan_only else "done"
    fetched = 0
    if not args.plan_only:
        for leaf in plan["leaves"]:
            state, _ = leaf_state(root, leaf, ticket_id)
            if state == "captured" or (state == "failed" and not args.retry_failed):
                continue
            if time.monotonic() - started > args.budget_seconds:
                stop = "budget"
                break
            if time.time() - first_pass_at > args.slice_seconds:
                stop = "slice"
                break
            if fetched and args.pause_seconds > 0:
                time.sleep(args.pause_seconds)
            fetched += 1
            leaf_dir = root / leaf["dir"]
            leaf_dir.mkdir(parents=True, exist_ok=True)
            (leaf_dir / ERROR_NAME).unlink(missing_ok=True)
            rc, out, err = run(capture_cmd(root, ticket, leaf, args))
            if rc != 0 or leaf_state(root, leaf, ticket_id)[0] != "captured":
                text = (err or out)[-600:]
                why = "timeout" if "timeout" in text.lower() else "error"
                write_json(leaf_dir / ERROR_NAME, {"ticket": ticket_id, "item": leaf["item"], "why": why, "exit": rc, "detail": text})
            print(f"[{fetched}] {leaf['dir']} exit {rc}", file=sys.stderr)

    states = {leaf["item"]: leaf_state(root, leaf, ticket_id) for leaf in plan["leaves"]}
    report = report_of(ticket, plan, states)
    if not args.plan_only:
        # Last, and on every pass: a slice killed between passes still leaves
        # a report that tells the truth about what landed.
        write_json(directory / REPORT_NAME, report)
    counts = [s for s, _ in states.values()]
    print(
        json.dumps(
            {
                # A plan with leaves in it has no outcome until they are fetched.
                "outcome": report["outcome"] if not (args.plan_only and plan["leaves"]) else None,
                "reason": report["reason"] if not (args.plan_only and plan["leaves"]) else None,
                "planned": len(plan["leaves"]),
                "captured": counts.count("captured"),
                "failed": counts.count("failed"),
                "remaining": counts.count("pending"),
                "skipped": plan["skipped"],
                "stop": stop,
                "report": None if args.plan_only else f"{ticket['capture_dir']}/{REPORT_NAME}",
            },
            indent=2,
        )
    )
    if args.plan_only and plan["leaves"]:
        return 0
    return 1 if report["outcome"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
