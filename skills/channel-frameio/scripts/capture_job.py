# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Capture ONE dispatched Frame.io leaf job into its own capture dir.

platform: frameio
scope: platform-general (no hardcoded share ids or hosts). This is the
per-job capture path the unit never had: the host dispatches a leaf
job carrying `skill: channel-frameio`, and this wraps `capture_asset.py`
for exactly that job — one asset, one capture dir, one `capture.json`, one
report row. It runs NO queue verb: no intake, no claim, no complete, no
drain. The host applies the row.

`capture_asset.py`'s own docstring has always said the caller wraps it
per-item. Before this, the only caller that did was `harvest_share.py`'s
self-driving loop, which claimed and completed jobs itself — so a host that
dispatched one leaf had no instruction but "re-run the whole share flow".

Reads the job out of the slice's own `assignment.json` rather than taking
its fields on the command line: that file is one of the two paths a worker
is handed, it holds the full job record, and re-typing `tags`/`areas`/
`dest` off it by hand is how a capture.json quietly loses the watch's
scoping dimensions.

The capture dir is HOST-derived, inside the watch's own slice
(`core/watches.capture_dir_for`), and handed to this script as
`--capture-dir` — this unit never composes one itself. It is still checked
against the assignment's granted `slices` BEFORE anything is fetched — a
path under no granted slice is refused when the host applies the report,
and by then the bytes are already downloaded, so a caller-supplied dir is
worth re-checking rather than trusted outright.

A dispatched leaf job carries a URL and nothing else, so the asset's
original filename and its folder breadcrumb — which the share's leaf
manifest holds — do not reach it. `--name` and `--path` are there for a
caller that does have the manifest in hand, feeding the capture record's
`title`/`path` fields; without them the name comes from the URL's own
asset id and `capture_asset.py` takes the document extension off the
signed proxy URL instead.

Usage:
  uv run capture_job.py <root> --assignment <assignment.json> --job-id <id>
      --capture-dir <wiki-relative dir> [--name <original filename>]
      [--path <folder> ...] [--timeout-ms N]

Outputs one JSON object on stdout, `{"job": {...}, "summary": {...}}`.
`job` is the report row VERBATIM — copy it into the report's `jobs` array,
do not rebuild it. `title`, `verdict` and the asset summary are NOT report
fields; the host reads them out of the `capture.json` this writes.

Exit 0 when the capture completed, 1 when it failed (the `job` row is then
a `fail` row the worker still reports), 2 when the assignment or the job id
does not add up — nothing was fetched and there is no row to report.

History:
  2026-08-18  created — the per-job capture path.
  2026-08-20  `--capture-dir` is now REQUIRED: the capture dir is
              host-derived (`core/watches.capture_dir_for`) and this unit
              never composes one itself.
"""

import argparse
import json
import sys
from pathlib import Path

from capture_record import capture_record, domain_of_url, run


def job_from_assignment(path: Path, job_id: str):
    """`(job, slices)` for one dispatched id, or a refusal on stderr.

    A job id this worker was not given is an operator error, not a capture
    failure: there is no row to report for a job nobody dispatched, and the
    host would refuse one anyway.

    An unreadable or non-object assignment is the SAME class — the docstring
    promises exit 2 for "the assignment does not add up", and an uncaught
    `JSONDecodeError` exits 1 instead, which is the exit a worker reads as
    "there is a fail row on stdout".
    """
    try:
        assignment = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: {path} is not readable as JSON — {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(assignment, dict):
        print(
            f"error: {path} does not hold a JSON object — the host writes {{v, session, slices, jobs}}", file=sys.stderr
        )
        raise SystemExit(2)
    jobs = [j for j in assignment.get("jobs", []) if isinstance(j, dict)]
    for job in jobs:
        if job.get("id") == job_id:
            return job, assignment.get("slices", [])
    print(f"error: {job_id} is not in {path} — dispatched ids are {[j.get('id') for j in jobs]}", file=sys.stderr)
    raise SystemExit(2)


def in_granted_slice(root: Path, capture_dir: Path, slices) -> bool:
    """Whether the capture dir lands PHYSICALLY inside a granted slice.

    The host re-checks this when it applies the report; checking here is
    what keeps a wrong-slice capture from costing the fetch as well as the
    row.

    Resolved absolute paths compared by path COMPONENT, never by string
    prefix. `--capture-dir` is caller-supplied — it is in this skill's own
    `argument-hint` — and the prefix test on `Path.as_posix()` this
    replaces let three shapes through:
    `<granted>/../../.git/hooks` and `<granted>/../other.example/x` both
    start with the granted string, and a component of an otherwise-granted
    path may be a symlink pointing out of the wiki or sideways into a slice
    this worker was not given.

    `resolve()` rather than `normpath()` because a slice grant is about
    where the BYTES land: `normpath` collapses `..` lexically and follows
    no symlink, so it closes the two traversals and leaves the symlink
    open. The component comparison then also closes the prefix-sibling
    shape — `<granted>x` sitting beside a granted `<granted>` — structurally
    rather than by the trailing `/` the old test appended, which is what
    happened to cover it.

    A slice carrying `..` is skipped rather than honoured, and an absolute
    one is read root-relative (`strip("/")`, so `/etc` grants `<root>/etc`
    and still refuses `/etc/passwd`). Neither can widen the grant past the
    wiki root, which is the property that matters: the host writes one
    slice per slug and validates that shape, so anything else here is a
    broken assignment rather than a bigger permission.

    An EMPTY `slices` needs no branch of its own — the loop grants nothing,
    so an assignment with no grant is refused here like any other
    out-of-slice path.
    """
    target = (root / capture_dir).resolve()
    for s in slices:
        s = s.strip("/")
        if not s or ".." in Path(s).parts:
            continue
        base = (root / s).resolve()
        if target == base or base in target.parents:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="wiki root path")
    ap.add_argument(
        "--assignment",
        required=True,
        type=Path,
        help="the slice's assignment.json — one of the two paths the host handed this worker",
    )
    ap.add_argument("--job-id", required=True, help="which job in that assignment to capture")
    ap.add_argument(
        "--capture-dir",
        required=True,
        help="wiki-relative capture dir, host-derived and "
        "handed over by the caller — this unit never "
        "composes one itself",
    )
    ap.add_argument(
        "--name",
        default=None,
        help="the asset's original filename, when the caller has the share's leaf manifest in hand",
    )
    ap.add_argument(
        "--path", action="append", default=None, help="folder breadcrumb bit, repeatable — same source as --name"
    )
    ap.add_argument("--timeout-ms", type=int, default=None, help="passed through to capture_asset.py")
    args = ap.parse_args()

    job, slices = job_from_assignment(args.assignment, args.job_id)
    url = job.get("url")
    if not isinstance(url, str) or not url:
        print(
            f"error: job {args.job_id} in {args.assignment} carries no url "
            f"— there is nothing to capture and no row to report",
            file=sys.stderr,
        )
        raise SystemExit(2)
    domain = domain_of_url(url)
    path_bits = list(args.path or [])
    # A leaf viewer is `.../share/<share-id>/view/<asset-id>`, so the last
    # path segment is the asset id — stable, unique, and the only name this
    # path can have without the manifest.
    name = args.name or url.rstrip("/").rsplit("/", 1)[-1]

    capture_dir = Path(args.capture_dir)
    # Unconditional, not `if slices and …`: guarding it means an assignment
    # carrying no slices skips the check entirely, which is a fetch into
    # anywhere on the strength of a field being absent. An empty grant
    # needs no branch of its own — the check refuses it like any other
    # path under no slice.
    if not in_granted_slice(args.root, capture_dir, slices):
        print(
            f"error: {capture_dir} does not land inside any granted slice "
            f"{slices} — the host would refuse the row and the fetch "
            f"would be wasted",
            file=sys.stderr,
        )
        raise SystemExit(2)

    abs_dir = args.root / capture_dir
    cmd = ["uv", "run", str(Path(__file__).resolve().parent / "capture_asset.py"), url, "--out", str(abs_dir)]
    if args.name:
        cmd += ["--name", args.name]
    if args.timeout_ms is not None:
        cmd += ["--timeout-ms", str(args.timeout_ms)]

    rc, out, err = run(cmd)
    if rc != 0:
        print(
            json.dumps(
                {
                    "job": {
                        "id": job["id"],
                        "outcome": "fail",
                        "error": f"capture_asset exit {rc}: {(err or out)[:300]}",
                    },
                    "summary": {"url": url, "capture_dir": str(capture_dir)},
                },
                indent=2,
            )
        )
        return 1

    # Everything past the fetch is inside the promise "exit 1 is a fail row
    # the worker still reports": truncated JSON, a missing `kind`/`bytes`, a
    # result that is not an object, or a capture dir with no `document.*` in
    # it all used to die on a traceback with the bytes already on disk and
    # NO row on stdout, which is the one outcome the host cannot account
    # for. Broad on purpose — the row is what makes the
    # failure visible, so anything that stops it is worth catching.
    try:
        cap_result = json.loads(out)
        record = capture_record(job, cap_result, name=name, path_bits=path_bits, domain=domain, abs_capture_dir=abs_dir)
        summary = {
            "url": url,
            "domain": domain,
            "kind": cap_result["kind"],
            "bytes": cap_result["bytes"],
            "title": cap_result.get("title"),
        }
        (abs_dir / "capture.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — see the comment above
        print(
            json.dumps(
                {
                    "job": {
                        "id": job["id"],
                        "outcome": "fail",
                        "error": (f"capture_asset exited 0 but its result did not add up: {type(exc).__name__}: {exc}")[
                            :300
                        ],
                    },
                    "summary": {"url": url, "capture_dir": str(capture_dir)},
                },
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "job": {"id": job["id"], "outcome": "complete", "capture_dir": str(capture_dir)},
                "summary": summary,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
