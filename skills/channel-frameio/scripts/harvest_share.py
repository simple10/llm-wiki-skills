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
  this venue the asset IS the item rather than an attachment of a page (a
  ticket carries the RESOLVED `assets` value, so an operator's explicit
  `reference` cannot be told from the default — SKILL.md says what to do).

**The run is bounded and resumable.** A slice is killed at thirty minutes,
and a killed slice that left no report is a failed ticket. So leaves are
taken in manifest order (stable: folder walk order), a leaf whose dir already
holds a `capture.json` is counted and not re-fetched, `report.json` is
rewritten (atomically) when a pass opens and again after EVERY leaf, and a
pass stops starting new leaves once `--budget-seconds` (one tool call's worth)
or `--slice-seconds` runs out. Every child gets what is left of
`--kill-seconds` as its deadline and is killed, with everything it started,
when that passes — the leaf is then recorded as not captured, `why: timeout`.
Run it again while its summary says `"stop": "budget"`; stop when it says
`done` or `slice`. A report written before every leaf landed says `partial`,
with the count in its `reason`.

**All of that state is the SPAWN's, never the ticket id's.** A ticket's id is
a hash of the job's slug and target: the same on every pull, every
`queue retry` and every respawn after a widen, into the same capture dir,
which the spawner only ever `mkdir -p`s. What IS new on every spawn is
`ticket.json` — the spawner rewrites it — so its mtime is both the slice's
clock (the kill is measured from the spawn, not from this script's first
pass, which the enumeration precedes) and the spawn's name: a failure an
`error.json` recorded, and a `plan.json`, count only when they carry the same
one. Keyed on the ticket id, a second pull inherited the first one's clock,
found the slice "spent" before it began, started no leaf, and never retried
a leaf that had failed once. The first capturing pass of a spawn also removes
the `report.json` an earlier spawn left: `apply` does not check whose it is.

A target that is itself a leaf viewer (`.../view/<asset-id>`) needs no
manifest: it is one leaf, captured into the ticket's own capture dir.

**A refresh ticket** (`refresh: true`) names one page's `resource` — a leaf
viewer — and gets exactly that leaf, RE-captured: the first pass of the spawn
drops the `capture.json` the last one left in that (stable) dir, so the bytes
`apply` hashes against the page's stamp are bytes this run read. Whether a
Frame.io asset can change under one view URL is unverified (a version stack
may), which is the reason to re-read rather than to answer
`refresh_unsupported`: re-reading is right either way, and `apply` — not this
unit — says `unchanged`. Never `gone`: the viewer of a removed asset and a
viewer this unit no longer understands look the same from here, so that is
reported `failed`.

Usage:
  uv run harvest_share.py <capture_dir> [--manifest tree.json] [--plan-only]
      [--budget-seconds N] [--slice-seconds N] [--kill-seconds N] [--pause-seconds N]
      [--retry-failed] [--title-strip S] [--author A] [--group G]
      [--group-type T] [--timeout-ms N]
      [--target URL --slug SLUG --ticket ID]     # no ticket.json: hand run

`<capture_dir>` is the ticket's capture dir — `_raw/<slug>/<one>` from the
wiki root, which is where the front door's `run` starts a script. The manifest
defaults to `<capture_dir>/tree.json`. Outside a slice — a hand run in a
directory whose `ticket.json` is hours old — pass `--slice-seconds 0`: there
is no kill to stay ahead of, and the old spawn's clock is long spent.

Outputs one JSON summary on stdout: {"outcome", "reason", "planned",
"captured", "failed", "remaining", "skipped": {"known", "excluded", "scope",
"duplicate"}, "stop": "done|budget|slice|plan-only", "report"}.
Exit 0 when the report's outcome is `ok`, `partial` or `skipped`; 1 when it
is `failed`; 2 when the inputs do not add up and no report could be written.
A refusal also REMOVES any `report.json` an earlier run left in the capture
dir: `apply` would read that one as this run's.

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
  2026-09-19  titles are settled before every report: two assets of one share
              with one name landed as one page, the second overwriting the
              first (`capture_record.py::settle_titles`).
  2026-09-19  review fixes. Resume state is keyed on the SPAWN (`ticket.json`'s
              mtime), not the ticket id, which never changes: a second pull
              started no leaf. `report.json` after every leaf, atomically, and
              every child has a deadline: one long video could outrun the kill
              and leave no report. A refresh ticket re-captures its leaf. Venue
              text crosses argv as `--name=<v>`: a file named `-rf.pdf` exited
              2 on every pass. Breadcrumbs drop the top folder only when every
              leaf carries it. A leaf URL must be http(s).
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
    TIMED_OUT,
    host_of,
    inner_deadline,
    leaf_dir_name,
    leaf_ids,
    now_utc,
    read_json,
    run,
    settle_titles,
    shared_top,
    write_json,
)

PLAN_NAME = "plan.json"
ERROR_NAME = "error.json"
RAW_DIRNAME = "_raw"

SCOPES = ("page", "section", "domain")

#: One pass stays under a ten-minute tool call; the slice is killed at 1800 s,
#: so no new leaf starts past 1500 — a video still has to finish downloading —
#: and a child still running at 1740 is killed, which leaves a minute to say so.
BUDGET_SECONDS = 480
SLICE_SECONDS = 1500
KILL_SECONDS = 1740
SLICE_CAP_SECONDS = 1800  # the host's `schedule/runner/slice.py::SLICE_CAP_SECONDS`
PAUSE_SECONDS = 2.0


class Unusable(Exception):
    """The inputs do not add up — exit 2, nothing fetched, no report."""


class NotOurs(Unusable):
    """The directory named is not the ticket's capture dir: touch nothing in it."""


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
        # It becomes a page's `resource`, a link in a body and an argv item.
        if urlsplit(leaf["view_url"]).scheme not in ("http", "https") or any(c.isspace() for c in leaf["view_url"]):
            raise Unusable(f"leaves[{i}].view_url is not an http(s) URL: {leaf['view_url']!r}")
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
    {...}}` out. A leaf is `{item, dir, name, path, crumb, asset_id}`; `dir`
    is wiki-relative and exactly `_raw/<slug>/<one component>`, the only shape
    `apply` mints a process ticket for. `crumb` is `path` below any top folder
    EVERY leaf of the manifest carries (`shared_top` — judged over the whole
    manifest, not the plan, so a leaf's dir does not move as `known[]` grows).
    """
    slug, target = ticket["slug"], ticket["target"]
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    scope = harvest.get("scope") if harvest.get("scope") in SCOPES else "domain"
    known = known_resources(ticket)
    skipped = {"known": 0, "excluded": 0, "scope": 0, "duplicate": 0}
    planned, seen = [], set()
    paths = [[p for p in (leaf.get("path") or []) if isinstance(p, str)] for leaf in leaves]
    skip = shared_top(paths)
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
                    "dir": f"{RAW_DIRNAME}/{slug}/{leaf_dir_name(url, leaf.get('name'), path_bits[skip:])}",
                    "name": leaf.get("name") or None,
                    "path": path_bits,
                    "crumb": path_bits[skip:],
                    "asset_id": leaf.get("asset_id") or leaf_ids(url)[1],
                }
            )
        seen.add(url)
    return {"scope": scope, "leaves": planned, "skipped": skipped}


def refresh_resource(ticket: dict):
    """What a refresh ticket re-fetches: its `resource`, which the host also
    makes the ticket's `target`. None when this is no refresh ticket."""
    if not ticket.get("refresh"):
        return None
    resource = ticket.get("resource")
    return resource if isinstance(resource, str) and resource else ticket["target"]


def single_leaf_plan(ticket: dict) -> dict:
    """A target that IS a leaf viewer: one leaf, in the ticket's own dir. A
    refresh ticket is always this — exactly the refreshed resource."""
    url = refresh_resource(ticket) or ticket["target"]
    skipped = {"known": 0, "excluded": 0, "scope": 0, "duplicate": 0}
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    leaves = []
    if url in known_resources(ticket) and not ticket.get("refresh"):
        skipped["known"] = 1
    elif excluded(url, harvest.get("exclude_urls")):
        skipped["excluded"] = 1
    else:
        leaves = [{"item": url, "dir": ticket["capture_dir"], "name": None, "path": [], "crumb": [], "asset_id": leaf_ids(url)[1]}]
    return {"scope": harvest.get("scope") or "domain", "leaves": leaves, "skipped": skipped}


# ---------------------------------------------------------------- the report


def spawn_of(directory: Path):
    """This spawn's name: `ticket.json`'s mtime in ns, or None with no ticket.

    The spawner rewrites `ticket.json` on every dispatch — a pull, a retry, a
    respawn after a widen (`pipeline/dispatch.py::start_slice`, the one spawn
    path, calls `write_ticket`) — while the ticket's ID is a hash of the job's
    slug and target and never changes. None is a hand run: one open-ended
    "spawn", whose recorded failures `--retry-failed` reopens.
    """
    try:
        return (Path(directory) / TICKET_NAME).stat().st_mtime_ns
    except OSError:
        return None


def slice_epoch(spawn, earlier: dict, same_spawn: bool, now: float, cap: float = SLICE_CAP_SECONDS) -> float:
    """When this slice's thirty minutes began, as a wall-clock epoch.

    The spawn itself where there is a `ticket.json`: the kill is measured from
    there, and this script's first pass comes after an enumeration that can
    take minutes. With none (a hand run), the first pass — inherited from
    `plan.json` only while it could still be the same run: a clock older than
    the cap belongs to a slice that was killed long ago.
    """
    if spawn is not None:
        return min(spawn / 1e9, now)
    inherited = earlier.get("slice_epoch") if same_spawn else None
    if isinstance(inherited, (int, float)) and 0 <= now - inherited <= cap:
        return float(inherited)
    return now


def leaf_state(root: Path, leaf: dict, spawn=None):
    """`("captured", title)`, `("failed", why)` or `("pending", None)`.

    Read off the leaf's own dir, so a second pass — or a second script — sees
    what the first one left. `captured` means a `capture.json` naming a body
    file that is there: exactly what the extractor will ask of it. A recorded
    failure counts only for the SPAWN that recorded it (`spawn_of`) — a
    share's capture dirs outlive a spawn, the ticket id does not change
    between them, and a later spawn owes the leaf a fresh attempt.
    """
    directory = root / leaf["dir"]
    record = read_json(directory / CAPTURE_NAME)
    if record and isinstance(record.get("body"), str) and (directory / record["body"]).is_file():
        title = record.get("title")
        return "captured", title if isinstance(title, str) else None
    error = read_json(directory / ERROR_NAME)
    if error and error.get("spawn", "unrecorded") == spawn:
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
        raise NotOurs(f"{directory} is not the ticket's capture_dir {capture_dir!r}")
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


def capture_cmd(root: Path, ticket: dict, leaf: dict, args, *, deadline=None, fresh=False) -> list:
    """The per-leaf child's argv. Every VALUE rides as `--flag=<value>`: a
    card's name is venue text, and `--name -rf.pdf` (or a folder called
    `--help`) as a separate item is an option to argparse, which then exits 2
    on every pass. The `=` form is taken literally whatever it starts with."""
    cmd = ["uv", "run", str(Path(__file__).resolve().parent / "capture_job.py"), str(root / leaf["dir"])]
    cmd += [f"--root={root}", f"--url={leaf['item']}", f"--slug={ticket['slug']}"]
    if leaf.get("name"):
        cmd.append(f"--name={leaf['name']}")
    cmd += [f"--path={bit}" for bit in leaf.get("path") or []]
    crumb = leaf.get("crumb")
    if isinstance(crumb, list):
        cmd.append(f"--crumb-skip={len(leaf.get('path') or []) - len(crumb)}")
    for flag, value in (
        ("--title-strip", args.title_strip),
        ("--author", args.author),
        ("--group", args.group),
        ("--group-type", args.group_type),
        ("--timeout-ms", args.timeout_ms),
        ("--deadline-seconds", None if deadline is None else f"{inner_deadline(deadline):.0f}"),
    ):
        if value is not None:
            cmd.append(f"{flag}={value}")
    if fresh:
        cmd.append("--fresh")
    return cmd


def write_report(root: Path, directory: Path, ticket: dict, plan: dict, spawn) -> tuple:
    """Settle the titles, read every leaf's state, write `report.json`.

    After EVERY leaf, not once at the end: whatever kills the pass — the
    slice's cap, a tool call's own timeout — the report on disk already names
    every leaf that had landed. Titles first: two leaves with one title are
    ONE page to the extractor, the second overwriting the first.
    """
    settle_titles(root, plan["leaves"])
    states = {leaf["item"]: leaf_state(root, leaf, spawn) for leaf in plan["leaves"]}
    report = report_of(ticket, plan, states)
    write_json(directory / REPORT_NAME, report)
    return states, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture_dir", type=Path, help="the ticket's capture dir — where ticket.json is")
    ap.add_argument("--manifest", type=Path, default=None, help="tree.json from enumerate_tree.py (default: <capture_dir>/tree.json)")
    ap.add_argument("--plan-only", action="store_true", help="write plan.json and print the summary; fetch nothing, report nothing")
    ap.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS, help=f"start no new leaf after this long in THIS pass (default {BUDGET_SECONDS})")
    ap.add_argument("--slice-seconds", type=float, default=SLICE_SECONDS, help=f"start no new leaf this long after the slice was spawned — ticket.json's mtime (default {SLICE_SECONDS}; the slice is killed at {SLICE_CAP_SECONDS}). 0: no slice clock, a hand run outside a slice")
    ap.add_argument("--kill-seconds", type=float, default=KILL_SECONDS, help=f"kill a child still running this long after the spawn and record its leaf as `timeout` (default {KILL_SECONDS})")
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
        refreshing = refresh_resource(ticket)
        if refreshing is not None and not leaf_ids(refreshing)[1]:
            # Every page this unit lands has a leaf viewer as its `resource`;
            # anything else is a page it did not write and cannot re-read.
            plan = None
        elif refreshing is not None or leaf_ids(ticket["target"])[1]:
            plan = single_leaf_plan(ticket)
        else:
            manifest_path = args.manifest or directory / "tree.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Unusable(f"{manifest_path} is not readable as JSON — run enumerate_tree.py first ({exc})") from None
            plan = plan_leaves(leaves_of(manifest), ticket)
    except Unusable as exc:
        # A refusal writes no report — and must not leave an EARLIER run's
        # `ok` standing where `apply` will read it as this one's.
        if not args.plan_only and not isinstance(exc, NotOurs) and directory.is_dir():
            (directory / REPORT_NAME).unlink(missing_ok=True)
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Whose state is on disk? The SPAWN's — see the module docstring. The id
    # is kept in the files for a reader; nothing is decided by it alone.
    ticket_id = ticket.get("ticket")
    spawn = spawn_of(directory)
    now = time.time()
    earlier = read_json(directory / PLAN_NAME) or {}
    same_spawn = earlier.get("spawn", "unrecorded") == spawn and earlier.get("ticket") == ticket_id
    epoch = slice_epoch(spawn, earlier, same_spawn, now)
    opened = bool(same_spawn and earlier.get("opened"))

    if plan is None:
        reason = f"refresh_unsupported: {refreshing} is not a Frame.io leaf viewer (.../share/<share-id>/view/<asset-id>)"
        report = report_of(ticket, {"scope": None, "leaves": [], "skipped": dict.fromkeys(("known", "excluded", "scope", "duplicate"), 0)}, {})
        report["reason"] = reason
        if not args.plan_only:
            write_json(directory / REPORT_NAME, report)
        print(json.dumps({"outcome": "failed", "reason": reason, "planned": 0, "stop": "done"}, indent=2))
        return 1

    if not args.plan_only and not opened:
        # The first capturing pass of this spawn. What an earlier spawn left
        # in this (stable) dir is not this one's word: `apply` does not check
        # whose `report.json` it reads, and on a single-leaf ticket the
        # extractor's own report lands here too.
        (directory / REPORT_NAME).unlink(missing_ok=True)
        if refreshing is not None:
            # A refresh RE-reads. The capture the last one left would otherwise
            # be counted as landed and `apply` would hash bytes nobody fetched.
            for leaf in plan["leaves"]:
                (root / leaf["dir"] / CAPTURE_NAME).unlink(missing_ok=True)
        opened = True
    write_json(
        directory / PLAN_NAME,
        {"ticket": ticket_id, "spawn": spawn, "opened": opened, "planned_at": now_utc(), "slice_epoch": epoch, **plan},
    )

    sliced = args.slice_seconds > 0
    stop = "plan-only" if args.plan_only else "done"
    fetched = 0
    if args.plan_only:
        states = {leaf["item"]: leaf_state(root, leaf, spawn) for leaf in plan["leaves"]}
        report = report_of(ticket, plan, states)
    else:
        # Before the first leaf, so a pass killed inside it still leaves a
        # report — and one that says what is true so far.
        states, report = write_report(root, directory, ticket, plan, spawn)
        for leaf in plan["leaves"]:
            state, _ = states.get(leaf["item"], ("pending", None))
            if state == "captured" or (state == "failed" and not args.retry_failed):
                continue
            if time.monotonic() - started > args.budget_seconds:
                stop = "budget"
                break
            deadline = None
            if sliced:
                if time.time() - epoch > args.slice_seconds:
                    stop = "slice"
                    break
                deadline = epoch + args.kill_seconds - time.time()
                if deadline <= 1:
                    stop = "slice"
                    break
            if fetched and args.pause_seconds > 0:
                time.sleep(args.pause_seconds)
            fetched += 1
            leaf_dir = root / leaf["dir"]
            leaf_dir.mkdir(parents=True, exist_ok=True)
            (leaf_dir / ERROR_NAME).unlink(missing_ok=True)
            rc, out, err = run(capture_cmd(root, ticket, leaf, args, deadline=deadline, fresh=refreshing is not None), timeout=deadline)
            if rc == TIMED_OUT:
                # Killed mid-write: whatever it left is not a capture.
                (leaf_dir / CAPTURE_NAME).unlink(missing_ok=True)
            if rc != 0 or leaf_state(root, leaf, spawn)[0] != "captured":
                text = (err or out)[-600:]
                why = "timeout" if rc == TIMED_OUT or "timeout" in text.lower() else "error"
                write_json(
                    leaf_dir / ERROR_NAME,
                    {"ticket": ticket_id, "spawn": spawn, "item": leaf["item"], "why": why, "exit": rc, "detail": text},
                )
            print(f"[{fetched}] {leaf['dir']} exit {rc}", file=sys.stderr)
            states, report = write_report(root, directory, ticket, plan, spawn)
        if stop == "slice" and not fetched:
            print(
                f"note: the slice clock ({TICKET_NAME}'s mtime) is {time.time() - epoch:.0f}s old, so no leaf was started; "
                f"outside a slice (a hand run) pass --slice-seconds 0",
                file=sys.stderr,
            )
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
