# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Capture a Frame.io share's leaves under ONE ticket, and post its update.

platform: frameio
scope: platform-general (no hardcoded share ids or hosts).

One harvest ticket captures the whole share. Nothing fans a share's leaves
out into further tickets, so this script is the fan-out: it takes the leaf
manifest `enumerate_tree.py` wrote, plans which leaves this ticket still owes,
captures each one into its OWN capture dir beside the ticket's
(`_raw/<slug>/<leaf>--<hash8>/`, via `capture_job.py`), and posts `tickets
update` naming every leaf that landed. The host's own `close` mints one
process ticket per `captured=` directory; this script touches no queue.

This is the HARVEST step, and harvest is bytes: no page is rendered, no
summary is written and no `capture.json` carries a `frontmatter` object. The
page is `frameio_doc_note.py`'s, in the process step that `apply`'s ticket
starts.

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
- `harvest.assets` — `download` (the unit's shipped default) captures every
  leaf. `reference` plans no MEDIA leaf: the only reference this venue offers
  is a signed HLS URL that expires within hours, and nothing transcribes a
  video that was never fetched, so a referenced video is a page pointing at
  nothing. A leaf is media by the extension of its card name; a nameless
  leaf is captured whatever the value says, since its kind is known only
  once fetched. Documents are captured under every value — they ARE the page.
- `harvest.access` and `min_date` are not consulted: a guest share has no
  free/paid split and its listing carries no dates.

**The run is bounded and resumable.** A slice is killed at thirty minutes,
and a killed slice that posted no update is a failed ticket. So leaves are
taken in manifest order (stable: folder walk order), a leaf whose dir already
holds a `capture.json` is counted and not re-fetched, `tickets update` is
posted when a pass opens and again after EVERY leaf, and a pass stops
starting new leaves once `--budget-seconds` (one tool call's worth) or
`--slice-seconds` runs out. Every child gets what is left of `--kill-seconds`
as its deadline and is killed, with everything it started, when that passes
— the leaf is then recorded as not captured, `why: timeout`. Run it again
while its summary says `"stop": "budget"`; stop when it says `done` or
`slice`. A status posted before every leaf landed is `partial`, with the
count in its `reason`.

**All of that state is the SPAWN's, never the ticket id's.** A ticket's id is
a hash of the job's slug and target: the same on every pull, every retry and
every respawn after a widen, into the same capture dir, which the spawner
only ever `mkdir -p`s. There is no per-dispatch timestamp on disk any more
(P-8: `open` answers no mtime), so the ticket id itself is the spawn's name —
a failure an `error.json` recorded, and `plan.json`, count only when they
carry the same one, and `--retry-failed` is what reopens a failure recorded
under THIS ticket id (an operator's own call, since nothing here can tell a
genuine respawn apart from a continuation of the same one any more). The
first capturing pass of a spawn is the FIRST one to see no earlier `plan.json`
for this ticket id.

A target that is itself a leaf viewer (`.../view/<asset-id>`) needs no
manifest: it is one leaf, captured into the ticket's own capture dir.

**A refresh ticket** (`refresh: true`) names one page's `resource` — a leaf
viewer — and gets exactly that leaf, RE-captured: the first pass of the spawn
drops the `capture.json` the last one left in that (stable) dir, so the bytes
a later read hashes against the page's stamp are bytes this run read. Whether
a Frame.io asset can change under one view URL is unverified (a version stack
may), which is the reason to re-read rather than to answer
`refresh_unsupported`: re-reading is right either way, and the host — not
this unit — says `unchanged`. Never `gone`: the viewer of a removed asset and
a viewer this unit no longer understands look the same from here, so that is
reported `failed`.

Usage:
  uv run harvest_share.py <capture_dir> [--manifest tree.json] [--plan-only]
      [--budget-seconds N] [--slice-seconds N] [--kill-seconds N] [--pause-seconds N]
      [--retry-failed] [--title-strip S] [--author A] [--group G]
      [--group-type T] [--timeout-ms N] --ticket ID
      [--target URL --slug SLUG]     # no --ticket: hand run

`<capture_dir>` is the ticket's capture dir — `_raw/<slug>/<one>` from the
wiki root, which is where the front door's `run` starts a script. The manifest
defaults to `<capture_dir>/tree.json`. Outside a slice — a hand run continuing
an old plan — pass `--slice-seconds 0`: there is no kill to stay ahead of,
and the old spawn's clock is long spent.

Outputs one JSON summary on stdout: {"outcome", "reason", "planned",
"captured", "failed", "remaining", "skipped": {"known", "excluded", "scope",
"duplicate", "reference"}, "stop": "done|budget|slice|plan-only"}.
Exit 0 when the posted status is `ok` or `partial`; 1 when it is `failed`;
2 when the inputs do not add up and nothing was posted. A refusal posts
nothing (the host's own start unlinks a stale earlier report, A-4).

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
  2026-09-19  the unit's two steps restored: nothing here renders a page, and
              a leaf's `body` is the file the venue served.
  2026-09-22  `harvest.assets` honored: `reference` plans no media leaf, by
              the card name's extension, and names each under `unplanned`;
              the manifest's default is `download`.
  2026-09-25  moved to the CLI-verb worker contract: `load_ticket` opens the
              ticket through `tickets open`, given --ticket, instead of a
              file beside the capture dir; the spawn marker and the slice
              clock are re-anchored on the ticket id and this run's own first
              write (P-8), since there is no per-dispatch timestamp left to
              read; every status is posted via `tickets update` instead of
              written to `report.json`. `skipped` becomes `ok` (P-4).
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
    TIMED_OUT,
    host_of,
    inner_deadline,
    leaf_dir_name,
    leaf_ids,
    now_utc,
    open_ticket,
    post_update,
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
#: What `harvest.assets: reference` leaves unplanned, by the card name's
#: extension: a video or audio asset, the one kind of leaf `reference` would
#: reduce to a signed URL that is dead within hours. No `ts`: a false positive
#: drops a document, a false negative downloads one video.
MEDIA_EXTS = frozenset(
    "mp4 m4v mov mkv webm avi wmv mpg mpeg mxf mts m2ts 3gp mp3 m4a aac wav aif aiff flac ogg opus wma".split()
)
SKIPPED = ("known", "excluded", "scope", "duplicate", "reference")

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


def is_media_name(name) -> bool:
    """Whether a card name says the leaf is a video or audio asset."""
    return isinstance(name, str) and "." in name and name.rsplit(".", 1)[1].lower() in MEDIA_EXTS


def unplanned_by_reference(leaf: dict, harvest: dict) -> bool:
    """`harvest.assets: reference` and a leaf whose name says media."""
    return harvest.get("assets") == "reference" and is_media_name(leaf.get("name"))


def known_resources(ticket: dict) -> set:
    entries = ticket.get("known")
    if not isinstance(entries, list):
        return set()
    return {e["resource"] for e in entries if isinstance(e, dict) and isinstance(e.get("resource"), str)}


def plan_leaves(leaves, ticket: dict) -> dict:
    """Which leaves this ticket owes, in manifest order, each with its dir.

    Pure: a manifest's leaves and a ticket in, `{"leaves": [...], "skipped":
    {...}, "unplanned": [...]}` out — `unplanned` names each media leaf that
    `harvest.assets: reference` left out, so an operator can see what a
    `reference` cost and a misjudged extension has a trace. A leaf is `{item, dir, name, path, crumb, asset_id}`; `dir`
    is wiki-relative and exactly `_raw/<slug>/<one component>`, the only shape
    `apply` mints a process ticket for. `crumb` is `path` below any top folder
    EVERY leaf of the manifest carries (`shared_top` — judged over the whole
    manifest, not the plan, so a leaf's dir does not move as `known[]` grows).
    """
    slug, target = ticket["slug"], ticket["target"]
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    scope = harvest.get("scope") if harvest.get("scope") in SCOPES else "domain"
    known = known_resources(ticket)
    skipped = dict.fromkeys(SKIPPED, 0)
    planned, unplanned, seen = [], [], set()
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
        elif unplanned_by_reference(leaf, harvest):
            skipped["reference"] += 1
            unplanned.append({"item": url, "name": leaf.get("name"), "why": "reference"})
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
    return {"scope": scope, "leaves": planned, "skipped": skipped, "unplanned": unplanned}


def refresh_resource(ticket: dict):
    """What a refresh ticket re-fetches: its `resource`, which the host also
    makes the ticket's `target`. None when this is no refresh ticket."""
    if not ticket.get("refresh"):
        return None
    resource = ticket.get("resource")
    return resource if isinstance(resource, str) and resource else ticket["target"]


def single_leaf_plan(ticket: dict) -> dict:
    """A target that IS a leaf viewer: one leaf, in the ticket's own dir. A
    refresh ticket is always this — exactly the refreshed resource. No card
    name reaches a single-leaf ticket, so `harvest.assets: reference` leaves
    it planned: its kind is known only once fetched."""
    url = refresh_resource(ticket) or ticket["target"]
    skipped = dict.fromkeys(SKIPPED, 0)
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}
    leaves = []
    if url in known_resources(ticket) and not ticket.get("refresh"):
        skipped["known"] = 1
    elif excluded(url, harvest.get("exclude_urls")):
        skipped["excluded"] = 1
    else:
        leaves = [{"item": url, "dir": ticket["capture_dir"], "name": None, "path": [], "crumb": [], "asset_id": leaf_ids(url)[1]}]
    return {"scope": harvest.get("scope") or "domain", "leaves": leaves, "skipped": skipped, "unplanned": []}


# ---------------------------------------------------------------- the report


def spawn_of(ticket_id):
    """This run's own spawn marker (P-8): there is no per-dispatch timestamp
    on disk any more (`open` answers no mtime, no `spawned_at`), and the
    ticket's own id is stable across every pull, retry and respawn after a
    widen — so it is what `leaf_state()` keys a recorded failure on: a
    failure stays put until `--retry-failed` names it, rather than being
    silently retried by the next pull of the same ticket. None is a hand run
    with no `--ticket`: one open-ended "spawn", whose recorded failures
    `--retry-failed` reopens the same way.
    """
    return ticket_id


def slice_epoch(spawn, earlier: dict, same_spawn: bool, now: float, cap: float = SLICE_CAP_SECONDS) -> float:
    """When this slice's thirty minutes began, as a wall-clock epoch (P-8):
    THIS run's own first write for this ticket id. The first capturing pass
    of a spawn stamps `now`; every later pass — this driver is run again
    while its summary says `"stop": "budget"` — reads the SAME epoch back off
    `plan.json`, so the clock does not reset each time. A clock older than the
    cap belongs to a slice that was killed long ago and is not inherited.
    """
    inherited = earlier.get("slice_epoch") if same_spawn else None
    if isinstance(inherited, (int, float)) and 0 <= now - inherited <= cap:
        return float(inherited)
    return now


def leaf_state(root: Path, leaf: dict, spawn=None):
    """`("captured", title)`, `("failed", why)` or `("pending", None)`.

    Read off the leaf's own dir, so a second pass — or a second script — sees
    what the first one left. `captured` means a `capture.json` naming a body
    file that is there: exactly what the process step will ask of it. A recorded
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


def update_of(ticket: dict, plan: dict, states: dict) -> dict:
    """The ticket's `tickets update`. Pure: `states` maps a leaf's `item` to
    `leaf_state()`'s answer, and a leaf it does not name is pending.

    P-4: "nothing new" (known/excluded/reference) is `ok`, named in the
    reason — `skipped` is no unit's word any more."""
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
            status, reason = "ok", f"known: all {skipped['known']} leaves in the plan are already pages"
        elif skipped["scope"]:
            status = "failed"
            reason = (
                f"harvest.scope={plan['scope']} excludes all {skipped['scope']} leaves of {ticket['target']} — "
                f"a leaf is /share/<share-id>/view/<asset-id>; set harvest.scope=domain"
            )
        elif skipped["excluded"]:
            status, reason = "ok", f"harvest.exclude_urls excludes all {skipped['excluded']} leaves"
        elif skipped["reference"]:
            status, reason = "ok", f"harvest.assets=reference plans no media leaf, and all {skipped['reference']} leaves are media"
        else:
            status, reason = "failed", "the share enumerated no leaves"
    elif len(captured) == planned:
        status, reason = "ok", None
    elif captured:
        status = "partial"
        reason = f"{len(captured)} of {planned} leaves captured; {len(missing)} failed, {pending} not reached"
    else:
        status = "failed"
        reason = f"0 of {planned} leaves captured; {len(missing)} failed, {pending} not reached"
    return {"status": status, "reason": reason, "captured": captured, "missing": missing}


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
    """The ticket (A-1): `tickets open`, given `--ticket` — the one command
    that opens it (every later pass over the same spawn reads it back off
    `plan.json`, per P-8). Explicit flags override either way, for a hand
    run."""
    ticket = open_ticket(args.ticket, "harvest") if args.ticket else {}
    for key, value in (("target", args.target), ("slug", args.slug), ("ticket", args.ticket)):
        if value:
            ticket[key] = value
    for key in ("target", "slug"):
        if not isinstance(ticket.get(key), str) or not ticket[key]:
            raise Unusable(f"no `{key}`: no --ticket and none on the command line")
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


def post_report(root: Path, ticket: dict, plan: dict, spawn) -> tuple:
    """Settle the titles, read every leaf's state, post `tickets update`.

    After EVERY leaf, not once at the end: whatever kills the pass — the
    slice's cap, a tool call's own timeout — the status posted already names
    every leaf that had landed. Titles first: two leaves with one title are
    ONE page, the second overwriting the first.
    """
    settle_titles(root, plan["leaves"])
    states = {leaf["item"]: leaf_state(root, leaf, spawn) for leaf in plan["leaves"]}
    update = update_of(ticket, plan, states)
    post_update(
        ticket["ticket"], "harvest", update["status"], reason=update["reason"],
        captured=[row["dir"] for row in update["captured"]],
        missing=[(row["host"], row["url"], row["why"]) for row in update["missing"]],
    )
    return states, update


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture_dir", type=Path, help="the ticket's capture dir")
    ap.add_argument("--manifest", type=Path, default=None, help="tree.json from enumerate_tree.py (default: <capture_dir>/tree.json)")
    ap.add_argument("--plan-only", action="store_true", help="write plan.json and print the summary; fetch nothing, post nothing")
    ap.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS, help=f"start no new leaf after this long in THIS pass (default {BUDGET_SECONDS})")
    ap.add_argument("--slice-seconds", type=float, default=SLICE_SECONDS, help=f"start no new leaf this long after the slice was spawned — this run's own first write, P-8 (default {SLICE_SECONDS}; the slice is killed at {SLICE_CAP_SECONDS}). 0: no slice clock, a hand run outside a slice")
    ap.add_argument("--kill-seconds", type=float, default=KILL_SECONDS, help=f"kill a child still running this long after the spawn and record its leaf as `timeout` (default {KILL_SECONDS})")
    ap.add_argument("--pause-seconds", type=float, default=PAUSE_SECONDS, help="pause between leaves — one reader, not a crawler")
    ap.add_argument("--retry-failed", action="store_true", help="re-fetch leaves an earlier pass recorded as failed")
    ap.add_argument("--title-strip", default=None, help="share-wide suffix to trim off every captured title")
    ap.add_argument("--author", default=None)
    ap.add_argument("--group", default=None)
    ap.add_argument("--group-type", dest="group_type", default=None)
    ap.add_argument("--timeout-ms", type=int, default=None, help="passed through to capture_asset.py")
    ap.add_argument("--target", default=None, help="the share URL, for a hand run with no --ticket")
    ap.add_argument("--slug", default=None, help="the job's slug, for a hand run with no --ticket")
    ap.add_argument("--ticket", default=None, help="the ticket id, opened for the rest of these defaults; REQUIRED unless every other flag names a hand run's inputs")
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
        # A refusal posts nothing (A-4 covers a stale earlier run's report).
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Whose state is on disk? The SPAWN's — see the module docstring. The id
    # is kept in the files for a reader; nothing is decided by it alone.
    ticket_id = ticket.get("ticket")
    spawn = spawn_of(ticket_id)
    now = time.time()
    earlier = read_json(directory / PLAN_NAME) or {}
    same_spawn = spawn is not None and earlier.get("spawn") == spawn
    epoch = slice_epoch(spawn, earlier, same_spawn, now)
    opened = bool(same_spawn and earlier.get("opened"))

    if plan is None:
        reason = f"refresh_unsupported: {refreshing} is not a Frame.io leaf viewer (.../share/<share-id>/view/<asset-id>)"
        if not args.plan_only:
            post_update(ticket_id, "harvest", "failed", reason=reason)
        print(json.dumps({"outcome": "failed", "reason": reason, "planned": 0, "stop": "done"}, indent=2))
        return 1

    if not args.plan_only and not opened:
        # The first capturing pass of this spawn.
        if refreshing is not None:
            # A refresh RE-reads. The capture the last one left would otherwise
            # be counted as landed and a later read would hash bytes nobody fetched.
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
        update = update_of(ticket, plan, states)
    else:
        # Before the first leaf, so a pass killed inside it still leaves a
        # posted status — and one that says what is true so far.
        states, update = post_report(root, ticket, plan, spawn)
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
            states, update = post_report(root, ticket, plan, spawn)
        if stop == "slice" and not fetched:
            print(
                f"note: the slice clock (this run's own first write) is {time.time() - epoch:.0f}s old, so no leaf was "
                f"started; outside a slice (a hand run) pass --slice-seconds 0",
                file=sys.stderr,
            )
    counts = [s for s, _ in states.values()]
    print(
        json.dumps(
            {
                # A plan with leaves in it has no outcome until they are fetched.
                "outcome": update["status"] if not (args.plan_only and plan["leaves"]) else None,
                "reason": update["reason"] if not (args.plan_only and plan["leaves"]) else None,
                "planned": len(plan["leaves"]),
                "captured": counts.count("captured"),
                "failed": counts.count("failed"),
                "remaining": counts.count("pending"),
                "skipped": plan["skipped"],
                "stop": stop,
            },
            indent=2,
        )
    )
    if args.plan_only and plan["leaves"]:
        return 0
    return 1 if update["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
