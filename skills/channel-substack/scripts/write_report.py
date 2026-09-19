#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Write the ticket's `report.json` — last, and from what is ON DISK.

platform: substack
scope: platform-general (no hardcoded domain/slugs)

`report.json` is the only thing that travels back out of a harvest slice, and
`pipeline apply` mints one process ticket per `captured[].dir`. So `captured[]`
is not what a worker remembers doing: it is every leaf of the plan
(`leaves.json`) whose directory holds a `capture.json` AND the body that record
names, read at the moment this runs. A leaf a previous, killed slice captured
is listed too — nothing else ever reported it.

`missing[]` is every leaf `capture_posts.py` recorded as `paywalled`
(`why: auth` — a preview is never captured as the article) or `error` (its own
`why`: `denied`, `timeout`, `auth`, `error`), plus any `--missing URL=WHY` the
worker saw itself.

The outcome, unless `--outcome` overrides it (a refresh ticket's `gone`, say):

- `ok`       every planned leaf is captured, and the plan was the whole walk;
- `partial`  something landed, and something did not: a paywall, a failure,
             the deadline, the `--max-leaves` cap, or an archive page that
             would not load. `reason` says which;
- `skipped`  the plan is empty because the job already holds everything
             (`known[]`) or nothing is new — `reason` opens `known:`;
- `failed`   nothing landed and something should have. When every leaf was
             refused for auth the reason is `auth_expired:<domain>`; when
             `harvest.scope` kept no post, the reason names the scope.

**Titles are settled first.** The extractor files a page under its TITLE and
overwrites what is there, so two posts of one run sharing a title ("Open
thread", "Links") would be ONE page. In plan order the first post to make a
filename keeps its title; a later one is retitled `<title> (<published
date>)` — the 8-hex hash of its URL where there is no date, or the date is the
namesake's too — in its own `capture.json`, and `captured[].title` says the
same. See "page names" below for the rule and its limit.

It writes `<capture-dir>/report.json` and prints the same object. Exit 0 for
`ok`, `partial`, `skipped`, `unchanged` and `gone`; exit 1 for `failed`.

History:
- 2026-09-19: new with the port to the rebuilt pipeline's worker contract.
- 2026-09-19: titles are settled before the report is built — two same-titled
  posts of one run landed as one page, the second overwriting the first.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

TICKET_NAME = "ticket.json"
PLAN_NAME = "leaves.json"
RESULTS_NAME = "results.json"
CAPTURE_NAME = "capture.json"
REPORT_NAME = "report.json"
REPORT_V = 1

OUTCOMES = ("ok", "partial", "skipped", "unchanged", "gone", "failed")
WHYS = ("denied", "timeout", "auth", "error")


def _load(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def captured_record(directory):
    """The capture record here, if the capture is COMPLETE; else None."""
    record = _load(Path(directory) / CAPTURE_NAME)
    body = record.get("body") if record else None
    if not isinstance(body, str) or not body or not (Path(directory) / body).is_file():
        return None
    return record


# --- page names
#
# KEEP IN SYNC with the host. `pipeline extract` names a page FILE from the
# capture's title and writes it with no existence check (llm-wiki-ops
# `commands/pipeline/extract.py::_capture_to_page` -> `pipeline/pages.py::
# name_for` -> `page/note.py::filename_for`): the filename is
# `title.strip() + ".md"` — nothing folded, nothing dropped; a title carrying
# one of `ILLEGAL` or a control character is REFUSED, not altered — and a
# capture with no title is filed under its body's stem (`page`). So two leaves
# of one run whose titles differ only in outer whitespace are ONE page, the
# second overwriting the first; on a filesystem that folds case (macOS,
# Windows) so are two that differ only in that. `page_key` folds both: a
# needless qualifier costs nothing, an overwritten page is lost. NOT folded:
# Unicode form (NFC/NFD), which the same filesystems also fold — stdlib has it
# only in `unicodedata`, and one venue spelling one title two ways is rare.
# Duplicated per unit on purpose — units install one by one, nothing is shared.

TITLE_ILLEGAL = '/\\:*?"<>|'  # `page/note.py::ILLEGAL`
QUALIFIER_MAX = 60


def page_key(title: str) -> str:
    """What two titles share when they make one page file."""
    return title.strip().casefold()


def qualifier(text) -> str:
    """Venue text made safe inside a title: one line, capped, and none of the
    characters the host refuses a title for."""
    if not isinstance(text, str):
        return ""
    safe = "".join("-" if (char in TITLE_ILLEGAL or ord(char) < 32) else char for char in text)
    return " ".join(safe.split())[:QUALIFIER_MAX].strip(" -.")


def unique_title(title: str, qualifiers, taken: dict) -> str:
    """`title`, untouched, when no leaf before this one makes its filename;
    else `title (<qualifier>)` with the first qualifier that tells it apart.

    `taken` maps a `page_key` to the qualifiers of the leaf holding it, and the
    answer is claimed in it. A qualifier the holder shares distinguishes
    nothing and is passed over; callers end the list with the leaf's hash8,
    which no other leaf has, and a counter closes it, so the answer is always
    free. A title this already qualified is free on the next pass and comes
    back as it is — re-running never renames a leaf a second time.
    """
    given = list(dict.fromkeys(q for q in map(qualifier, qualifiers) if q))
    chosen = title
    holder = taken.get(page_key(title))
    if holder is not None:
        shared = {page_key(q) for q in holder}
        options = [q for q in given if page_key(q) not in shared]
        base = title.strip()
        chosen = next((f"{base} ({q})" for q in options if page_key(f"{base} ({q})") not in taken), None)
        stem, n = (f"{base} ({options[-1]})" if options else base), 2
        while chosen is None:
            if page_key(f"{stem} ({n})") not in taken:
                chosen = f"{stem} ({n})"
            n += 1
    taken[page_key(chosen)] = given
    return chosen


_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def leaf_qualifiers(leaf, record):
    """What tells this post from a namesake: the day it was published — the one
    thing a newsletter re-using a title always changes — then its URL's hash."""
    front = (record or {}).get("frontmatter")
    published = (front.get("published") if isinstance(front, dict) else None) or leaf.get("published")
    day = _DAY.match(published) if isinstance(published, str) else None
    return [day.group(0) if day else None, hashlib.sha1(leaf["item"].encode("utf-8")).hexdigest()[:8]]


def settle_titles(leaves, leaf_root):
    """One page per post: in plan order the first leaf to make a filename keeps
    its title, and a later one is retitled in its own `capture.json`.

    Here and not in `capture_posts.py`, because a post's final title is only
    settled once it is captured (the plan's, else `og:title`, else a hand
    `--only --title`), one `--only` pass sees one leaf, and this runs last,
    over all of them, before anything is extracted. A planned post that did
    not land still holds the title the archive gave it, so what landed is
    titled the same whether or not its namesake did.
    """
    taken = {}
    for leaf in leaves:
        directory = Path(leaf_root) / leaf["dir"].rsplit("/", 1)[-1]
        record = captured_record(directory)
        if record is None:
            if isinstance(leaf.get("title"), str) and leaf["title"].strip():
                unique_title(leaf["title"], leaf_qualifiers(leaf, None), taken)
            continue
        title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
        held = title or Path(record["body"]).stem
        final = unique_title(held, leaf_qualifiers(leaf, record), taken)
        if final != held:
            record["title"] = final
            (directory / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def build(plan, rows, ticket, leaf_root, *, extra_missing=(), outcome=None, reason=None):
    """The report, as a dict. Pure but for reading the leaf directories — and
    for settling the titles in them, which is what makes `captured[]` true."""
    leaves = [leaf for leaf in plan.get("leaves") or [] if isinstance(leaf, dict) and leaf.get("item") and leaf.get("dir")]
    settle_titles(leaves, leaf_root)
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    by_item = {row.get("item"): row for row in rows if isinstance(row, dict)}

    captured, missing, not_landed = [], [], 0
    for leaf in leaves:
        record = captured_record(Path(leaf_root) / leaf["dir"].rsplit("/", 1)[-1])
        if record is not None:
            title = record.get("title")
            captured.append({"item": leaf["item"], "dir": leaf["dir"], "title": title if isinstance(title, str) else None})
            continue
        not_landed += 1
        row = by_item.get(leaf["item"]) or {}
        if row.get("state") in ("paywalled", "error"):
            why = row.get("why") if row.get("why") in WHYS else "error"
            missing.append({"host": urlsplit(leaf["item"]).netloc, "url": leaf["item"], "why": why})
    for url, why in extra_missing:
        if not any(entry["url"] == url for entry in missing):
            missing.append({"host": urlsplit(url).netloc, "url": url, "why": why})

    why_partial = []
    paywalled = sum(1 for leaf in leaves if (by_item.get(leaf["item"]) or {}).get("state") == "paywalled")
    unreached = sum(1 for leaf in leaves if (by_item.get(leaf["item"]) or {}).get("state") == "unreached")
    if not_landed:
        why_partial.append(f"{len(captured)} of {len(leaves)} planned posts captured")
    if paywalled:
        why_partial.append(f"{paywalled} paywalled")
    if unreached:
        why_partial.append(f"{unreached} not reached before the deadline")
    if summary.get("truncated"):
        why_partial.append("the archive goes on past this plan's cap; the next run resumes through known[]")
    if summary.get("fetch_failed"):
        why_partial.append(f"the archive walk stopped early ({summary['fetch_failed']})")

    if outcome is None:
        if captured:
            outcome = "partial" if (why_partial or missing) else "ok"
            reason = reason or ("; ".join(why_partial) if why_partial else None)
            if outcome == "partial" and not reason:
                reason = f"{len(missing)} url(s) missing"
        elif leaves:
            outcome = "failed"
            if missing and all(entry["why"] == "auth" for entry in missing):
                reason = reason or f"auth_expired:{plan.get('newsletter')}"
            else:
                reason = reason or "; ".join(why_partial) or "nothing captured"
        elif summary.get("fetch_failed"):
            outcome, reason = "failed", reason or f"the archive would not load ({summary['fetch_failed']})"
        elif summary.get("skipped_by_scope") and not summary.get("skipped_known"):
            outcome = "failed"
            reason = reason or (
                f"harvest.scope kept none of {summary['skipped_by_scope']} posts — an archive job needs "
                f"harvest.scope=domain, on the host its posts are served from"
            )
        else:
            outcome = "skipped"
            reason = reason or f"known: nothing new on {plan.get('newsletter')}"

    return {
        "v": REPORT_V,
        "ticket": ticket.get("ticket") or plan.get("ticket"),
        "outcome": outcome,
        "reason": reason,
        "captured": captured,
        "written": [],
        "missing": missing,
        "discovered": [],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture-dir", default=".", help="the ticket's capture directory (default: .)")
    ap.add_argument("--ticket", default=None, help="the ticket id, when there is no ticket.json to read it from")
    ap.add_argument(
        "--missing",
        action="append",
        default=[],
        metavar="URL=WHY",
        help=f"a url you could not get yourself (repeatable); WHY is one of {', '.join(WHYS)}",
    )
    ap.add_argument("--outcome", choices=OUTCOMES, default=None, help="override the computed outcome (a refresh's `gone`)")
    ap.add_argument("--reason", default=None, help="override the computed reason")
    args = ap.parse_args(argv)

    capture_dir = Path(args.capture_dir).resolve()
    ticket = _load(capture_dir / TICKET_NAME) or {}
    if args.ticket:
        ticket["ticket"] = args.ticket
    plan = _load(capture_dir / PLAN_NAME)
    if plan is None:
        # No plan is a walk that never ran. Still a report: a foreman reads
        # `no_report` as a broken jail, and this is a broken run.
        plan = {"leaves": [], "summary": {"fetch_failed": f"no {PLAN_NAME}"}}
    rows = (_load(capture_dir / RESULTS_NAME) or {}).get("rows") or []

    extra = []
    for spec in args.missing:
        url, _, why = spec.rpartition("=")
        if not url or why not in WHYS:
            ap.error(f"--missing {spec!r}: want URL=WHY, WHY one of {', '.join(WHYS)}")
        extra.append((url, why))

    report = build(plan, rows, ticket, capture_dir.parent, extra_missing=extra, outcome=args.outcome, reason=args.reason)
    text = json.dumps(report, indent=2)
    (capture_dir / REPORT_NAME).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if report["outcome"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
