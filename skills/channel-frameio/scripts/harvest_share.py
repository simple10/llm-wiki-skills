# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Emit a Frame.io share's leaves as ONE `discovered` row for the report.

platform: frameio
scope: platform-general (no hardcoded share ids or hosts). Takes the flat
leaf manifest `enumerate_tree.py` already wrote and EMITS every leaf's view
URL for the worker to copy into its report's `discovered` array. It queues
nothing, claims nothing and completes nothing.

That is the same move `channel-substack` makes: a confined worker has no
ledger and no queue, so
discovery is reported and the HOST applies it — which is also what gets the
`parent` checked against the jobs it dispatched and the `watch_id` (the
watch's slug) against that job's own. The queued leaves still inherit
scope, filters, tags, areas and assets mode from the watch entry; intake
still does the denylist, exclude, scope-prefix and seen-ledger work. None
of that moved — only who calls it.

Three things changed shape when the queue verbs left, and each is a fact a
caller used to get here and now gets one step later:

- **The per-leaf ok/failed lines are gone.** This script no longer captures
  anything; one leaf becomes one dispatched job, and `capture_job.py` in this
  unit is what a worker runs for it. Its outcome reaches the ledger through
  the host's `apply`, not through a line printed here.
- **`drained` is gone with `drain_pending.py`.** That script existed because
  `job.py claim` is oldest-first, so a requeued job could absorb a claim meant
  for a fresh add. That is a property of CLAIMING, and this unit claims
  nothing now — the host's dispatch loop drains the queue.
- **The emission is bounded, twice.** The host refuses a report whole past
  2000 discovered URLs or 256 KiB (`harvest_apply.py`), and a refused report
  sends every job in the slice back to `pending/`. A Frame.io leaf URL is
  ~106 bytes and every share emits them at full length, so the BYTE bound
  bites first on a large share where substack's short slugs never reach it.
  This truncates and names `resume_skip` rather than handing the worker a
  payload that poisons the whole slice.

**Resuming a truncated share is `--skip <summary.resume_skip>`.** The leaf
manifest is a stable, fully enumerated list — unlike an archive walk there is
no date to move — so the next pass starts at an index. Re-emitting the same
prefix would make no progress at all: the cap is on the REPORT, not on what
intake queues, so the tail would be dropped again.

Inputs: the manifest path only. No wiki root, no `--intake`, no `--job` —
this script touches no wiki and writes nothing, which is the property that
lets it run confined.

Outputs: one JSON object on stdout, `{"discovered": {...}, "summary": {...}}`.
`discovered` is the report row VERBATIM — copy it into the report's
`discovered` array, do not rebuild it and do not merge `summary` into it.
`summary` is this script's own accounting: {"domain", "total_leaves",
"skipped_leading", "emitted", "dropped", "truncated", "bound",
"resume_skip", "row_bytes"}.

Usage:
  uv run harvest_share.py <manifest.json> --slug <slug> --parent <job-id>
      [--skip N] [--max-urls N] [--max-bytes N]

History:
  2026-07-14  created — first Frame.io share harvest.
  2026-07-29  packaged into the channel-frameio skill unit: explicit
              --intake/--job paths, per-slug capture dirs, the up-front
              by-url dump, and the foreign-claim domain guard.
  2026-08-18  moved onto the host-owned report seam. Queues nothing
              and shells nothing: emits one `discovered` row for the
              worker's report and the host applies it. `<root>`,
              `--intake`, `--job` and `--by-url-out` are gone (it touches
              no wiki and drives no queue), the per-leaf capture lines and
              the `drained` count went with them, `--parent`/`--watch-id`
              are required since the host refuses a row it cannot match to
              a dispatched job, and the emission is bounded under the
              host's own two ceilings.
  2026-08-20  `--watch-id` -> `--slug`: the watch is identified by its
              slug now, not an opaque id, and a unit reads its job's
              `slug` field. The `discovered` row's own
              `watch_id` key is UNCHANGED — the report half keeps its
              field names and now carries the slug in it.
"""

import argparse
import json
import sys

# `harvest_apply.py`'s MAX_DISCOVERED_URLS and REPORT_MAX_BYTES, restated
# rather than imported: this script is deliberately self-contained (stdlib
# only, no sibling imports) so it runs inside a slice with nothing but its
# own directory.
# keep-in-sync: llm-wiki-ops/v1/plugin/scripts/harvest_apply.py
MAX_DISCOVERED_URLS = 2000
REPORT_MAX_BYTES = 256 * 1024

#: What ONE discovered row may claim of that report-wide byte budget. Not
#: the whole of it: the same report carries a `jobs` row per capture in the
#: slice, and it is written with indentation this measurement does not see.
#: At Frame.io's ~106-byte leaf URLs 2000 of them are ~220 KiB on their own,
#: which would leave a two-domain slice's job rows nothing at all — so the
#: row stops at three quarters and the tail comes back with `--skip`.
MAX_ROW_BYTES = 192 * 1024


def row_bytes(parent, slug, urls) -> int:
    """Serialized size of the row this emits, compact.

    Compact because that is the only size this script can know — the worker
    writes the enclosing report and chooses its own indentation, which only
    ever adds. Hence a bound that leaves headroom rather than one that
    pretends to be exact.
    """
    return len(json.dumps({"parent": parent, "watch_id": slug, "urls": urls}))


def leaves_of(manifest):
    """The manifest's leaf list, or a refusal naming what was handed over."""
    if not isinstance(manifest, dict):
        raise SystemExit("error: manifest is not a JSON object — pass the file enumerate_tree.py --out wrote")
    leaves = manifest.get("leaves")
    if not isinstance(leaves, list):
        raise SystemExit("error: manifest has no `leaves` list — pass the file enumerate_tree.py --out wrote")
    for i, leaf in enumerate(leaves):
        if not isinstance(leaf, dict) or not leaf.get("view_url"):
            raise SystemExit(f"error: leaves[{i}] carries no view_url")
    return leaves


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # No wiki root and no pipeline script paths: this emits and the host
    # applies, so it reaches nothing outside its own process.
    ap.add_argument("manifest", help="tree.json from enumerate_tree.py")
    ap.add_argument(
        "--slug",
        required=True,
        help="the watch's slug — queued jobs inherit "
        "scope/filters/tags from that entry. The host "
        "refuses a row naming any watch but this job's "
        "own",
    )
    ap.add_argument(
        "--parent",
        required=True,
        help="job id of the share job that ran this. REQUIRED: "
        "the host refuses a discovered row "
        "whose parent is not a job it dispatched, so a row "
        "without one can never apply",
    )
    ap.add_argument(
        "--skip",
        type=int,
        default=0,
        help="start at this leaf index — how a truncated share "
        "resumes (pass the previous run's "
        "summary.resume_skip). The manifest is a stable "
        "list, so an index is the whole resume state",
    )
    ap.add_argument(
        "--max-urls",
        type=int,
        default=MAX_DISCOVERED_URLS,
        help=f"stop at this many URLs (default "
        f"{MAX_DISCOVERED_URLS}, the host's own ceiling "
        f"across EVERY discovered row in one report). Past "
        f"it the host applies none of the report and every "
        f"job in the slice returns to pending/",
    )
    ap.add_argument(
        "--max-bytes",
        type=int,
        default=MAX_ROW_BYTES,
        help=f"stop once the row's compact serialization would "
        f"pass this many bytes (default {MAX_ROW_BYTES}, "
        f"under the host's {REPORT_MAX_BYTES}-byte whole-"
        f"report refusal). Frame.io leaf URLs are long and "
        f"uniform, so this is the bound that usually bites",
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    leaves = leaves_of(manifest)
    skip = max(0, args.skip)

    urls = []
    bound = None
    size = row_bytes(args.parent, args.slug, [])
    for leaf in leaves[skip:]:
        if len(urls) >= args.max_urls:
            bound = "urls"
            break
        # +2 for the `, ` json.dumps puts between elements by default; the
        # first URL replaces the empty list's `[]` and costs only itself.
        grown = size + len(json.dumps(leaf["view_url"])) + (2 if urls else 0)
        if grown > args.max_bytes:
            bound = "bytes"
            break
        urls.append(leaf["view_url"])
        size = grown

    truncated = bound is not None
    dropped = max(0, len(leaves) - skip - len(urls))

    if truncated:
        print(
            f"truncated at the {bound} bound: emitted {len(urls)} of "
            f"{len(leaves)} leaves, {dropped} left. Take the rest on a "
            f"later pass with --skip {skip + len(urls)} once this batch "
            f"has been applied — the cap is on the REPORT, so re-emitting "
            f"from the start drops the same tail again.",
            file=sys.stderr,
        )

    # `discovered` is the report row verbatim; `summary` is this script's
    # own accounting. Two keys rather than one flat object so a worker
    # copies the row across without having to know which fields the host
    # reads — a summary field leaking into the row is refused whole.
    print(
        json.dumps(
            {
                "discovered": {
                    "parent": args.parent,
                    "watch_id": args.slug,
                    "urls": urls,
                },
                "summary": {
                    "domain": manifest.get("domain"),
                    "total_leaves": len(leaves),
                    "skipped_leading": skip,
                    "emitted": len(urls),
                    "dropped": dropped,
                    "truncated": truncated,
                    "bound": bound,
                    "resume_skip": skip + len(urls) if truncated else None,
                    "row_bytes": size,
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
