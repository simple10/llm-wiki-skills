#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Enumerate a Substack newsletter's post archive via its JSON API.

platform: substack
scope: platform-general (no hardcoded domain/slugs)

Paginates `GET /api/v1/archive?sort=new&offset=<n>&limit=<n>` newest-first
for a given Substack domain, applies `--min-date` (stop paginating once a
page's posts fall below the floor — the API is newest-first, so once we're
below the floor everything older is too) and `--access` (free → only
`audience: everyone` posts are queued; paid-tier posts are counted in the
summary, never fetched), then EMITS the surviving URLs for the
worker to put in its report's `discovered` array. It queues nothing itself.

A confined worker has no ledger and no
queue, so discovery is reported and the HOST applies it — which is also
what gets the `parent` checked against the jobs it dispatched and the
`watch_id` (the watch's slug) against that job's own. The queued jobs still
inherit scope, filters, tags, areas and assets mode from the watch entry;
intake still does the denylist, exclude, scope-prefix and seen-ledger work.
None of that moved — only who calls it.

Three things changed shape when the intake call left, and each is a fact a
caller used to get here and now gets one step later:

- **The intake-side counts are gone.** `queued`, `intake_skipped` and
  `intake_reasons` were intake's own answers, summed over one call per
  archive page. `harvest_apply.py apply` takes ONE discovered row per parent
  job, so the per-page batching collapses into a single emission and those
  answers do not exist until the host applies it. They come back in
  `apply`'s output, per row.
- **A `scope: page` watch is no longer visible here.** It used to show up as
  `queued: 0` with `intake_reasons` full of `out_of_scope`. That signal now
  appears in `apply`'s `reasons` — check it there, and see the SKILL.md.
- **The emission is bounded.** The report refuses whole past the host's
  `max_discovered_urls` ceiling (across every row — the assignment's
  `limits` carries it, and `--max-urls` is how it reaches this script), and a
  refused report sends every job in the slice back to `pending/`. So this
  stops paginating at `--max-urls` and names `resume_max_date`, the ceiling
  to resume AT, rather than handing the worker a payload that poisons the
  whole slice.
  The default leaves no headroom for a sibling row on purpose: one archive
  walk is the only discoverer in its own slice, and a caller that knows
  otherwise lowers it.

**Resuming is `--max-date`, not `--min-date`.** The walk always starts at
`offset=0` and runs newest-first, so `--min-date` only decides where it
STOPS — lowering it re-emits the same first `--max-urls` posts and makes no
progress at all (measured). `--max-date` is what moves the start: it skips
everything newer, so a truncated walk continues with
`--max-date <summary.resume_max_date>`. It is INCLUSIVE, deliberately —
several posts can share one date, and re-emitting a boundary post costs a
slot that intake's seen-ledger then dedupes, while excluding it would drop
a post silently.

**It stalls if `--max-urls` or more posts share the boundary date**: the
whole emission is then one date, the ceiling cannot move below itself, and
the next pass returns the same set. `summary.stalled` says so and stderr
names the fix — raise `--max-urls` above the number of posts sharing that
date. Unreachable at the default 2000 for a human-written newsletter, and
measured: 1/day and 10/day walk 300 of 300 at cap 50, 60/day at cap 50
stalls on pass 2.

Inputs: either a domain (example.substack.com) or a full archive URL —
either is accepted, the domain is extracted automatically. No wiki root:
this script no longer touches the wiki at all, which is the property that
lets it run confined.

Outputs: one JSON object on stdout, `{"discovered": {...}, "summary": {...}}`.
`discovered` is the report row VERBATIM — copy it into the report's
`discovered` array, do not rebuild it. `summary` is this script's own
accounting: {"total_posts", "by_audience", "skipped_paywalled",
"skipped_newer", "stopped_at_min_date", "emitted", "truncated",
"stalled", "resume_max_date"}.
Paywalled posts are summary counts, not URLs — to pick them up after
subscribing, re-run with --access licensed.

History:
- 2026-07-09: initial version; same-day pagination fix — `offset=0`
  silently caps the response at 23 items even when `limit` asks for more,
  so advance the offset by the actual page length returned and stop only on
  a truly empty page.
- 2026-07-28: ported into the channel-substack skill unit. Intake handoff
  rewritten to the watch-inherited contract (per-URL, with `--watch-id`/
  `--parent`); the enumerator no longer passes scope/filters/tags itself.
- 2026-07-31: intake handoff batched per archive page on the
  `--discovered` contract (`--discovered-from -`, URLs on stdin); the
  summary's `queued` comes from intake's parsed response (it previously
  counted attempts without reading intake's stdout, even on failure), and
  `--dry-run` passes intake's own `--dry-run` through so the counts stay
  real while nothing is written.
- 2026-08-18: moved onto the host-owned report seam. Queues nothing
  and shells nothing: emits one `discovered` row for the worker's report
  and the host applies it. `<root>`, `--intake` and `--dry-run` are gone
  (it touches no wiki and writes nothing, so there is no run to dry); the
  three intake-side summary counts went with them; `--parent` is now
  required, since the host refuses a row it cannot match to a dispatched
  job; `--max-urls` bounds the emission under the host's own ceiling.
- 2026-08-20: `--watch-id` -> `--slug`: the watch is identified by its
  slug now, not an opaque id, and a unit reads its job's `slug` field.
  The `discovered` row's own `watch_id` key is UNCHANGED —
  the report half keeps its field names and now carries the slug in it.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

USER_AGENT = "Mozilla/5.0 (compatible; llm-wiki-harvest/1.0)"


def domain_from_arg(arg):
    """Accept either a bare domain or a full URL and return the domain."""
    if "://" in arg:
        return urlsplit(arg).netloc
    return arg


def fetch_page(domain, offset, limit):
    url = f"https://{domain}/api/v1/archive?sort=new&offset={offset}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_url(domain, post):
    # Archive API posts carry a canonical "canonical_url"; fall back to
    # constructing /p/<slug> if that field is ever missing.
    if post.get("canonical_url"):
        return post["canonical_url"]
    return f"https://{domain}/p/{post.get('slug')}"



def parse_date(s):
    # post_date is ISO 8601, e.g. "2026-07-08T12:00:00.000Z"
    return s[:10]  # YYYY-MM-DD prefix sorts/compares fine as strings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # No wiki root and no intake path: this script writes nothing and
    # reaches nothing outside its own process.
    ap.add_argument("domain", help="Substack domain (e.g. example.substack.com) or full archive URL")
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
        help="job id of the enumeration job that ran this. "
        "REQUIRED: the host refuses a discovered "
        "row whose parent is not a job it dispatched, so a "
        "row without one can never apply",
    )
    ap.add_argument(
        "--min-date",
        default=None,
        help="ISO date floor (YYYY-MM-DD); stop paginating once "
        "posts fall below it (read it off the claimed "
        "job's min_date)",
    )
    ap.add_argument(
        "--max-date",
        default=None,
        help="ISO date ceiling (YYYY-MM-DD), INCLUSIVE: skip "
        "every post newer than it. This is how a truncated "
        "walk resumes — pass the previous run's "
        "summary.resume_max_date. `--min-date` cannot do "
        "it: the walk always starts at offset=0, so "
        "lowering the floor re-emits the same first "
        "--max-urls posts",
    )
    ap.add_argument(
        "--access",
        choices=["licensed", "free"],
        default="free",
        help="licensed: queue every post reachable; free: only audience=everyone (read it off the job's access)",
    )
    ap.add_argument("--limit", type=int, default=50, help="page size for the archive API (default 50)")
    ap.add_argument(
        "--max-urls",
        type=int,
        required=True,
        help="stop paginating once this many URLs are emitted — the host's "
        "ceiling across EVERY discovered row in one report, handed to you as "
        "assignment.limits.max_discovered_urls; this script restates no number. "
        "Past it the host applies none of the report and every job in the "
        "slice returns to pending/, so this truncates and says so instead",
    )
    args = ap.parse_args()

    domain = domain_from_arg(args.domain)

    by_audience = {}
    urls = []
    skipped_newer = 0
    truncated = False
    oldest_emitted = None
    newest_emitted = None
    skipped_paywalled = 0
    total_posts = 0
    stopped_at_min_date = False

    offset = 0
    stop = False
    while not stop:
        try:
            page = fetch_page(domain, offset, args.limit)
        except urllib.error.URLError as e:
            print(f"fetch failed at offset {offset}: {e}", file=sys.stderr)
            break

        if not page:
            break

        page_urls = []
        for post in page:
            total_posts += 1
            audience = post.get("audience", "unknown")
            by_audience[audience] = by_audience.get(audience, 0) + 1

            post_date = None
            if post.get("post_date"):
                post_date = parse_date(post["post_date"])

            if args.min_date and post_date and post_date < args.min_date:
                # Newest-first pagination: once we're below the floor, every
                # subsequent (older) post is too — stop entirely.
                stop = True
                stopped_at_min_date = True
                break

            if args.max_date and post_date and post_date > args.max_date:
                # Newer than the resume ceiling: already taken by the pass
                # that named it. Skipped, not stopped — the archive is
                # newest-first, so what we want is further down.
                skipped_newer += 1
                continue

            if args.access == "free" and audience != "everyone":
                skipped_paywalled += 1
                continue

            if len(urls) + len(page_urls) >= args.max_urls:
                # Full. Stop here rather than walk an archive whose tail
                # cannot be emitted anyway — and record the floor to resume
                # from, since the API is newest-first and everything left is
                # older than what we kept.
                truncated = True
                stop = True
                break
            page_urls.append(post_url(domain, post))
            if post_date:
                oldest_emitted = post_date
                if newest_emitted is None:
                    newest_emitted = post_date

        urls.extend(page_urls)
        if stop:
            break

        # Advance by the actual number of items returned, not the requested
        # limit: offset=0 on this API can silently truncate a page short of
        # `limit` even when more posts remain (see History), so "short page"
        # is not a reliable end-of-archive signal. Only a truly empty page
        # (checked at the top of the loop) means the archive is exhausted.
        offset += len(page)
        time.sleep(0.7)

    # The ceiling can only move if the emission spans more than one date.
    # When it does not, the next pass re-emits this same set — the exact
    # shape the `--min-date` instruction had, so it says so rather than
    # looking like progress.
    stalled = bool(truncated and newest_emitted and newest_emitted == oldest_emitted)

    if stalled:
        print(
            f"STALLED: all {len(urls)} emitted posts share post_date "
            f"{oldest_emitted}, so --max-date {oldest_emitted} returns "
            f"this same set and the walk cannot advance. Raise --max-urls "
            f"above the number of posts sharing that date.",
            file=sys.stderr,
        )
    elif truncated:
        print(
            f"truncated at --max-urls {args.max_urls}: emitted "
            f"{len(urls)} of an archive still going at post_date "
            f"{oldest_emitted}. Take the rest with "
            f"--max-date {oldest_emitted} once this batch has been "
            f"applied (NOT --min-date: the walk restarts at offset=0, so "
            f"a lower floor re-emits these same posts).",
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
                    "total_posts": total_posts,
                    "by_audience": by_audience,
                    "skipped_paywalled": skipped_paywalled,
                    "skipped_newer": skipped_newer,
                    "stopped_at_min_date": stopped_at_min_date,
                    "emitted": len(urls),
                    # No count of what was left behind: the walk STOPS at the cap,
                    # so the tail is never fetched and any number here would be the
                    # one post that did not fit, not the remainder.
                    # `resume_max_date` is the useful answer.
                    "truncated": truncated,
                    "stalled": stalled,
                    "resume_max_date": oldest_emitted if truncated else None,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
