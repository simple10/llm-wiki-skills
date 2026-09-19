#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Plan one ticket's captures: walk a Substack archive and name every leaf.

platform: substack
scope: platform-general (no hardcoded domain/slugs)

ONE ticket captures the whole archive. Nothing fans a listing out into child
jobs any more, and nothing filters for this script after it: the worker's
report lists every post it captured, and `pipeline apply` mints one process
ticket per capture directory. So this script is where the job's own rules are
applied, all of them, before a single post page is fetched.

It paginates `GET /api/v1/archive?sort=new&offset=<n>&limit=<n>` newest-first
and keeps a post only if it passes, in this order:

- `min_date`       the ticket's floor (`now - harvest.max_age`). The API is
                   newest-first, so the first post below the floor ends the walk.
- `--max-date`     a hand-run ceiling, inclusive. Not how a run resumes — see below.
- `harvest.scope`  applied HERE, against the ticket's `target`:
                   `domain`  the post is on the target's host (`www.` ignored);
                   `section` same host, and under the target's path;
                   `page`    the post IS the target.
                   Posts live at `/p/<slug>`, never under `/archive`, so on an
                   archive target only `domain` keeps anything — the unit's
                   manifest defaults to it, and a walk that scope emptied says
                   so on stderr and in `summary.skipped_by_scope`.
- `harvest.exclude_urls`  an entry drops a post whose URL it equals, is a
                   prefix of, or matches as a shell glob (`*`, `?`). The host
                   defines no matching rule for this list; that one is this unit's.
- `harvest.access` `free` keeps only `audience: everyone`. Paid-tier posts are
                   counted, never fetched. `licensed` keeps every tier.
- `known[]`        a post whose URL is the `resource` of a page this job
                   already holds is skipped — matched EXACTLY, because that
                   string is the key the page carries.

Each survivor becomes a leaf: `{"item", "dir", "title", "published",
"audience", "on_disk"}`. `dir` is the leaf's capture directory,
`_raw/<slug>/<page-slug>--<hash8>`: the URL's path folded to a slug (lowercase,
non-alphanumeric runs to one hyphen, 60 chars) and the first 8 hex of
sha1(item URL). That is the host's own shape for an addressed item
(`pipeline/jobs.py::capture_dir_for`), which has no verb to ask — so it is
composed here, and `apply` accepts exactly `_raw/<slug>/<one-component>`.

**Resuming is `known[]`, not a date.** A slice is killed at thirty minutes and
a killed slice leaves no report, so the plan is bounded (`--max-leaves`) and
`capture_posts.py` stops at a deadline; the report says `partial`. The next
ticket's `known[]` carries what was extracted since, this walk skips it, and
the cap is spent only on posts still to capture — so a bounded walk always
advances. A leaf whose directory already holds a complete capture (a slice
that died after capturing it but before reporting) is planned with
`on_disk: true`: nothing re-fetches it, the report lists it again, and it
does not count against `--max-leaves`.

Two tickets are not an archive walk, and both plan ONE leaf into the ticket's
own `capture_dir`, with no API call:

- a **refresh** ticket (`refresh: true`) — the leaf is its `resource`, and
  `known[]` is not consulted, since re-fetching a known page is the job;
- a ticket whose `target` is itself a post (`/p/<slug>`).

Inputs come from `ticket.json` in `--capture-dir` (default: the directory you
stand in, which is where a worker is started). Every flag is an override for
a hand run; with no `ticket.json` give the domain or archive URL positionally.

Output: one JSON object on stdout, `{"v", "ticket", "slug", "newsletter",
"capture_dir", "leaves": [...], "summary": {...}}`, and — when there is a
`ticket.json`, or `--out` names a file — the same object written as
`leaves.json`, which `capture_posts.py` and `write_report.py` read. `summary`:
{"total_posts", "by_audience", "skipped_paywalled", "skipped_known",
"skipped_excluded", "skipped_by_scope", "skipped_newer",
"stopped_at_min_date", "planned", "on_disk", "truncated", "fetch_failed"}.

History:
- 2026-07-09: initial version; same-day pagination fix — `offset=0`
  silently caps the response at 23 items even when `limit` asks for more,
  so advance the offset by the actual page length returned and stop only on
  a truly empty page.
- 2026-07-28: ported into the channel-substack skill unit.
- 2026-08-18: stopped queueing; emitted one row for a host script to apply.
- 2026-09-19: ported to the rebuilt pipeline's worker contract. The host
  half this script used to hand off to is gone — no child jobs, and no host
  pass filtering what a worker reports — so it no longer emits a `discovered` row:
  it reads `ticket.json`, applies scope, access, `exclude_urls`, `min_date`
  and `known[]` itself, and plans the leaf capture directories. `--parent`
  is gone (nothing matches a row to a job); `--max-urls` became
  `--max-leaves` and is optional (the ceiling it restated was the host's);
  `resume_max_date` and `stalled` are gone with the date-resume they served,
  because `known[]` is what resumes a walk now and it cannot stall.
"""

import argparse
import fnmatch
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = "Mozilla/5.0 (compatible; llm-wiki-harvest/1.0)"

TICKET_NAME = "ticket.json"
PLAN_NAME = "leaves.json"
CAPTURE_NAME = "capture.json"
RAW_DIRNAME = "_raw"
SCOPES = ("page", "section", "domain")

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_POST_PATH = re.compile(r"^/p/[^/]+/?$")


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


def leaf_name(url):
    """`<page-slug>--<hash8>` — the host's own shape for an addressed item.

    Mirrors `pipeline/jobs.py::capture_dir_for` + `slugify`, which no verb
    exposes: the path's segments joined, folded, capped at 60; the host when
    the path is empty; then the first 8 hex of sha1 over the URL as given.
    """
    parts = urlsplit(url)
    bits = [bit for bit in parts.path.split("/") if bit] or [parts.netloc.lower()]
    folded = _NON_SLUG.sub("-", "-".join(bits).lower()).strip("-")[:60] or "item"
    return f"{folded}--{hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]}"


def leaf_dir(slug, url):
    return f"{RAW_DIRNAME}/{slug}/{leaf_name(url)}"


def _host(url):
    host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def in_scope(url, target, scope):
    """Does `harvest.scope`, read against the job's `target`, keep this post?"""
    if not target:
        return True  # a hand run with no job behind it has nothing to scope against
    if scope == "page":
        return url.rstrip("/") == target.rstrip("/")
    if _host(url) != _host(target):
        return False
    if scope == "domain":
        return True
    prefix = urlsplit(target if "://" in target else f"https://{target}").path.rstrip("/")
    path = urlsplit(url).path
    return not prefix or path == prefix or path.startswith(prefix + "/")


def excluded(url, patterns):
    for pattern in patterns or []:
        if not isinstance(pattern, str) or not pattern:
            continue
        if url.rstrip("/") == pattern.rstrip("/") or url.startswith(pattern):
            return True
        if any(ch in pattern for ch in "*?") and fnmatch.fnmatchcase(url, pattern):
            return True
    return False


def known_resources(ticket):
    entries = ticket.get("known")
    if not isinstance(entries, list):
        return set()
    return {e["resource"] for e in entries if isinstance(e, dict) and isinstance(e.get("resource"), str)}


def load_ticket(directory):
    """`ticket.json` beside the worker, or {} on a hand run. Never raises:
    a ticket this cannot read is a hand run that must name its own inputs."""
    try:
        doc = json.loads((Path(directory) / TICKET_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def holds_capture(directory):
    """Is there a COMPLETE capture here — a record, and the body it names?"""
    try:
        record = json.loads((Path(directory) / CAPTURE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    body = record.get("body") if isinstance(record, dict) else None
    return isinstance(body, str) and bool(body) and (Path(directory) / body).is_file()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "domain",
        nargs="?",
        default=None,
        help="Substack domain (e.g. example.substack.com) or full archive URL. Default: ticket.json's `target`",
    )
    ap.add_argument(
        "--capture-dir",
        default=".",
        help="the ticket's capture directory — where ticket.json is read and leaves.json is written (default: .)",
    )
    ap.add_argument("--slug", default=None, help="the job's slug, which names the leaf dirs. Default: ticket.json's `slug`")
    ap.add_argument(
        "--min-date",
        default=None,
        help="ISO date floor (YYYY-MM-DD); stop paginating once posts fall below it. Default: ticket.json's `min_date`",
    )
    ap.add_argument(
        "--max-date",
        default=None,
        help="ISO date ceiling (YYYY-MM-DD), INCLUSIVE: skip every post newer than it. A hand-run "
        "window only — a run resumes through the ticket's known[], never through a date",
    )
    ap.add_argument(
        "--access",
        choices=["licensed", "free"],
        default=None,
        help="licensed: every post reachable; free: only audience=everyone. Default: ticket.json's harvest.access, else free",
    )
    ap.add_argument(
        "--scope",
        choices=SCOPES,
        default=None,
        help="Default: ticket.json's harvest.scope, else domain. On an archive target only `domain` keeps any post",
    )
    ap.add_argument(
        "--exclude-url",
        action="append",
        default=None,
        metavar="URL|PREFIX|GLOB",
        help="repeatable; REPLACES ticket.json's harvest.exclude_urls when given",
    )
    ap.add_argument("--limit", type=int, default=50, help="page size for the archive API (default 50)")
    ap.add_argument(
        "--max-leaves",
        type=int,
        default=200,
        help="stop once this many posts are planned for fetching (default 200). A slice is killed at thirty "
        "minutes with no report behind it, so a plan is one slice's worth; the rest comes on the next "
        "run, which skips what known[] holds. Leaves already on disk do not count",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="write the plan here as well as stdout. Default: <capture-dir>/leaves.json when a ticket.json is there",
    )
    args = ap.parse_args()

    capture_dir = Path(args.capture_dir)
    ticket = load_ticket(capture_dir)
    harvest = ticket.get("harvest") if isinstance(ticket.get("harvest"), dict) else {}

    target = args.domain or ticket.get("target") or ticket.get("item")
    if not target:
        ap.error(f"no domain given and no {TICKET_NAME} with a `target` in {capture_dir}")
    slug = args.slug or ticket.get("slug")
    if not slug:
        ap.error(f"no --slug given and no {TICKET_NAME} with a `slug` in {capture_dir}")
    min_date = args.min_date or ticket.get("min_date")
    access = args.access or harvest.get("access") or "free"
    scope = args.scope or harvest.get("scope") or "domain"
    patterns = args.exclude_url if args.exclude_url is not None else harvest.get("exclude_urls") or []
    known = known_resources(ticket)
    # A scope is read against the JOB's target; a bare domain on a hand run is one too.
    scope_target = ticket.get("target") or (target if "://" in target else f"https://{target}")

    domain = domain_from_arg(target)
    own_dir = ticket.get("capture_dir")  # wiki-relative, host-derived: never recomposed

    by_audience = {}
    leaves = []
    counts = dict.fromkeys(
        ("skipped_paywalled", "skipped_known", "skipped_excluded", "skipped_by_scope", "skipped_newer"), 0
    )
    total_posts = 0
    planned = 0
    stopped_at_min_date = False
    truncated = False
    fetch_failed = None

    def on_disk(rel):
        # Leaves are siblings of the ticket's own directory: both are
        # `_raw/<slug>/<one>`, so no wiki root has to be found to look.
        return holds_capture(capture_dir.resolve().parent / rel.rsplit("/", 1)[-1])

    single = None
    if ticket.get("refresh") and isinstance(ticket.get("resource"), str):
        single, known = ticket["resource"], set()
    elif "://" in target and _POST_PATH.match(urlsplit(target).path):
        single = target

    if single is not None:
        total_posts = 1
        if single in known:
            counts["skipped_known"] = 1
        elif excluded(single, patterns):
            counts["skipped_excluded"] = 1
        else:
            leaves.append(
                {
                    "item": single,
                    "dir": own_dir or leaf_dir(slug, single),
                    "title": None,
                    "published": None,
                    "audience": None,
                    "on_disk": False,  # its own ticket's directory: a refresh or a re-pull re-fetches
                }
            )
            planned = 1

    offset = 0
    stop = single is not None
    while not stop:
        try:
            page = fetch_page(domain, offset, args.limit)
        except (urllib.error.URLError, TimeoutError) as e:
            # A mid-read socket timeout escapes as TimeoutError, not URLError.
            fetch_failed = f"offset {offset}: {e}"
            print(f"fetch failed at {fetch_failed}", file=sys.stderr)
            break

        if not page:
            break

        for post in page:
            total_posts += 1
            audience = post.get("audience", "unknown")
            by_audience[audience] = by_audience.get(audience, 0) + 1

            post_date = None
            if post.get("post_date"):
                post_date = parse_date(post["post_date"])

            if min_date and post_date and post_date < min_date:
                # Newest-first pagination: once we're below the floor, every
                # subsequent (older) post is too — stop entirely.
                stop = True
                stopped_at_min_date = True
                break

            if args.max_date and post_date and post_date > args.max_date:
                counts["skipped_newer"] += 1
                continue

            url = post_url(domain, post)

            if not in_scope(url, scope_target, scope):
                counts["skipped_by_scope"] += 1
                continue

            if excluded(url, patterns):
                counts["skipped_excluded"] += 1
                continue

            if access == "free" and audience != "everyone":
                counts["skipped_paywalled"] += 1
                continue

            if url in known:
                counts["skipped_known"] += 1
                continue

            rel = leaf_dir(slug, url)
            held = on_disk(rel)
            if not held and planned >= args.max_leaves:
                # Full. Stop here rather than walk an archive whose tail this
                # slice cannot capture anyway; everything left is older, and
                # the next run reaches it once known[] holds this batch.
                truncated = True
                stop = True
                break
            if not held:
                planned += 1
            leaves.append(
                {
                    "item": url,
                    "dir": rel,
                    "title": post.get("title") if isinstance(post.get("title"), str) else None,
                    "published": post_date,
                    "audience": audience,
                    "on_disk": held,
                }
            )

        if stop:
            break

        # Advance by the actual number of items returned, not the requested
        # limit: offset=0 on this API can silently truncate a page short of
        # `limit` even when more posts remain (see History), so "short page"
        # is not a reliable end-of-archive signal. Only a truly empty page
        # (checked at the top of the loop) means the archive is exhausted.
        offset += len(page)
        time.sleep(0.7)

    if counts["skipped_by_scope"] and not leaves:
        print(
            f"harvest.scope={scope} dropped {counts['skipped_by_scope']} posts and nothing is planned, against target "
            f"{scope_target}. Posts live at /p/<slug> on the newsletter's host: an archive job needs "
            f"harvest.scope=domain, declared on the host its posts are served from.",
            file=sys.stderr,
        )
    if truncated:
        print(
            f"truncated at --max-leaves {args.max_leaves}: the archive goes on past what one slice "
            f"captures. Report `partial`; the next run's known[] skips this batch and the walk continues.",
            file=sys.stderr,
        )

    plan = {
        "v": 1,
        "ticket": ticket.get("ticket"),
        "slug": slug,
        "newsletter": domain,
        "capture_dir": own_dir,
        "access": access,
        "leaves": leaves,
        "summary": {
            "total_posts": total_posts,
            "by_audience": by_audience,
            **counts,
            "stopped_at_min_date": stopped_at_min_date,
            "planned": planned,
            "on_disk": sum(1 for leaf in leaves if leaf["on_disk"]),
            # No count of what was left behind: the walk STOPS at the cap, so
            # the tail is never fetched and any number here would be a guess.
            "truncated": truncated,
            "fetch_failed": fetch_failed,
        },
    }
    text = json.dumps(plan, indent=2)
    out = Path(args.out) if args.out else (capture_dir / PLAN_NAME if ticket else None)
    if out is not None:
        out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
