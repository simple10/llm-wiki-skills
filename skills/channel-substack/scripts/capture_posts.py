#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4", "markdownify"]
# ///
"""Turn each planned Substack post into a capture the generic extractor reads.

platform: substack
scope: platform-general (no hardcoded domain/slugs)

Reads the plan `enumerate_archive.py` wrote (`leaves.json` in the ticket's
capture directory) and, for each leaf, newest first:

1. skips it if its directory already holds a complete capture (`on_disk`);
2. takes `page.html` from the leaf directory — put there by the worker
   (Playwright with a stored session, for a licensed job), or fetched here
   with a plain GET when `--fetch` is given and the file is absent;
3. REFUSES a paywall preview: a page carrying the paywall block is never
   written as a capture. The leaf is recorded `paywalled` and the report
   puts it in `missing[]` as `why: auth`;
4. renders the body with this unit's `to_markdown.py` (the sibling file)
   scoped to `.available-content`, then rewrites the top of `page.md` as the
   post's title and a compact facts block — never a `---` YAML block: the
   extractor prepends its own frontmatter and a second one corrupts the page;
5. writes `capture.json` naming `page.md`, with the same facts in its
   `frontmatter` object (`type`, `published`, `author`, `newsletter`,
   `audience`, `paywalled`, and `audio` on a podcast post).

Everything is written inside the leaf directories under `_raw/<slug>/`, which
is the whole of what a harvest slice may write. Nothing here reaches `dest`:
the page under it is `pipeline extract`'s to write, from these files.

It stops cleanly at `--deadline-minutes` (default 20) — a slice is killed at
thirty with no report behind it, so an unfinished plan has to END in a report,
not in the kill. What it did lands in `results.json` beside the plan, one row
per leaf: `{"item", "dir", "state", "why", "title"}`, `state` one of
`captured`, `on_disk`, `paywalled`, `pending` (no `page.html`, and no
`--fetch`), `unreached` (the deadline came first), `error`.
`write_report.py` turns the plan, these rows and what is actually on disk
into `report.json`.

Hand-fixing one post — a title the meta got wrong, a CTA to drop:

    capture_posts.py --only <post url> --title "<the on-page headline>" \
        --drop-selector "<css of the CTA block you saw in page.html>"

`--only` re-renders that one leaf from its `page.html` even if it was already
captured, and leaves every other row of `results.json` as it was.

History:
- 2026-09-19: new with the port to the rebuilt pipeline's worker contract.
  Only harvest reaches a unit now and processing is the generic extractor's,
  so what this venue knows about a post body — its content root, its paywall
  block, where its date and author live — is applied here, at harvest.
"""

import argparse
import html as htmllib
import importlib.util
import json
import random
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
RESULTS_NAME = "results.json"
CAPTURE_NAME = "capture.json"
HTML_NAME = "page.html"
BODY_NAME = "page.md"
CAPTURE_V = 1

CONTENT_SELECTOR = ".available-content"

# The block a paid post renders after its free preview. Text, not a class:
# the sentence is what the unit has observed; no class name is verified.
PAYWALL_MARKERS = ("This post is for paid subscribers",)

# Which of the report's four `missing[]` reasons a fetch failure is — the same
# reading the plugin's own page worker makes, restated because a unit imports
# nothing from the plugin.
AUTH_CODES = (401, 403, 407)
DENIED_MARKERS = ("tunnel connection failed", "not in the allowlist")
REFUSED_MARKER = "connection refused"
BACKOFF_CODES = (429, 500, 502, 503, 504)

_TAG = re.compile(r"<(meta|audio)\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_JSONLD = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tags(html, name):
    """Each `<name …>` tag's attributes, as a dict. Order-independent on
    purpose: Substack writes `<meta data-rh="true" property="og:title" …>`,
    and a regex anchored on `property` coming first missed 38 of 38 titles."""
    for match in _TAG.finditer(html):
        if match.group(1).lower() != name:
            continue
        yield {k.lower(): htmllib.unescape(a if a else b) for k, a, b in _ATTR.findall(match.group(0))}


def meta(html, *names):
    """The first `<meta>` whose `property` or `name` is one of `names`."""
    for attrs in _tags(html, "meta"):
        if (attrs.get("property") or attrs.get("name")) in names and attrs.get("content"):
            return attrs["content"].strip()
    return None


def jsonld(html):
    """Every JSON-LD object on the page, flattened. Hostile input: a block
    that does not parse is skipped, never raised."""
    found = []
    for block in _JSONLD.findall(html):
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        for node in doc if isinstance(doc, list) else [doc]:
            if isinstance(node, dict):
                found.append(node)
    return found


def published_of(html):
    """`YYYY-MM-DD` where the page DECLARES one, else None — never a guess."""
    candidates = [meta(html, "article:published_time")]
    candidates += [node.get("datePublished") for node in jsonld(html)]
    for value in candidates:
        if isinstance(value, str) and _DATE.match(value):
            return value[:10]
    return None


def author_of(html):
    named = meta(html, "author")
    if named:
        return named
    for node in jsonld(html):
        author = node.get("author")
        for one in author if isinstance(author, list) else [author]:
            if isinstance(one, dict) and isinstance(one.get("name"), str) and one["name"].strip():
                return one["name"].strip()
            if isinstance(one, str) and one.strip():
                return one.strip()
    return None


def audio_of(html):
    """A podcast post's audio sits OUTSIDE `.available-content`, so a
    selector-scoped body never mentions it. Found here, said in the facts."""
    for attrs in _tags(html, "audio"):
        if attrs.get("src"):
            return attrs["src"]
    return None


def is_paywall_preview(html):
    """Is this the truncated free preview of a paid post?

    The paywall BLOCK is the signal, not JSON-LD's `isAccessibleForFree:
    false` — a licensed session reads the whole of a post that still declares
    itself paid, and that is a complete capture.
    """
    return any(marker in html for marker in PAYWALL_MARKERS)


def facts_block(facts):
    labels = (
        ("published", "Published"),
        ("author", "Author"),
        ("newsletter", "Newsletter"),
        ("audience", "Audience"),
        ("audio", "Audio"),
        ("source", "Source"),
    )
    return "\n".join(f"- **{label}:** {facts[key]}" for key, label in labels if facts.get(key)) + "\n"


def compose_body(markdown, title, facts):
    """Title, facts, then the post — with the converter's own leading H1
    dropped, since it is the `<title>` tag or a duplicate of this one."""
    body = re.sub(r"\A\s*#\s+[^\n]*\n+", "", markdown)
    if body.lstrip().startswith("---"):
        # A leading rule would read as the opening of a YAML block once the
        # extractor's own frontmatter sits above it.
        body = re.sub(r"\A\s*---+\s*\n", "", body)
    return f"# {title}\n\n{facts_block(facts)}\n{body.strip()}\n"


def _converter():
    path = Path(__file__).resolve().with_name("to_markdown.py")
    spec = importlib.util.spec_from_file_location("substack_to_markdown", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(html_path, out_path, base_url, drop_selectors):
    """This unit's `to_markdown.py`, run as itself — its argv, its rules."""
    module = _converter()
    argv = [str(html_path), "--out", str(out_path), "--selector", CONTENT_SELECTOR, "--base-url", base_url]
    for selector in drop_selectors:
        argv += ["--drop-selector", selector]
    saved = sys.argv
    sys.argv = ["to_markdown.py", *argv]
    try:
        module.main()
    finally:
        sys.argv = saved


def why_for(exc):
    text = str(exc).lower()
    if any(marker in text for marker in DENIED_MARKERS):
        return "denied"
    if isinstance(exc, urllib.error.HTTPError):
        return "auth" if exc.code in AUTH_CODES else "error"
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return "timeout"
    return "error"


def fetch_html(url, *, sleep=time.sleep, tries=3):
    """One post page, as one reader would get it. Backs off on 429/5xx, and
    retries ONCE the measured proxy quirk (a refused socket in the second
    after a denial). Never anything cleverer: a soft block is a failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    refused_once = False
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in BACKOFF_CODES or attempt == tries - 1:
                raise
            sleep(60 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as exc:
            refused = isinstance(getattr(exc, "reason", exc), ConnectionRefusedError) or REFUSED_MARKER in str(exc).lower()
            if refused and not refused_once:
                refused_once = True
                sleep(1.0)
                continue
            if attempt == tries - 1:
                raise
            sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def capture_leaf(directory, leaf, *, slug, newsletter, overrides=None, drop_selectors=()):
    """One leaf: `page.html` -> `page.md` + `capture.json`. Returns the row."""
    overrides = overrides or {}
    item = leaf["item"]
    row = {"item": item, "dir": leaf["dir"], "state": "captured", "why": None, "title": None}
    html = (directory / HTML_NAME).read_text(encoding="utf-8", errors="replace")
    if is_paywall_preview(html):
        row.update(state="paywalled", why="auth")
        return row

    body_path = directory / BODY_NAME
    render(directory / HTML_NAME, body_path, item, drop_selectors)
    markdown = body_path.read_text(encoding="utf-8")

    first_h1 = re.match(r"\A\s*#\s+([^\n]+)", markdown)
    title = (
        overrides.get("title")
        or leaf.get("title")
        or meta(html, "og:title")
        or (first_h1.group(1).strip() if first_h1 else None)
        or urlsplit(item).path.rstrip("/").rsplit("/", 1)[-1]
    )
    audience = leaf.get("audience")
    frontmatter = {
        "type": "article",
        "published": overrides.get("published") or leaf.get("published") or published_of(html),
        "author": overrides.get("author") or author_of(html),
        "newsletter": newsletter,
        "audience": audience,
        # A fact about the POST, not about this capture: a preview is refused
        # above, so a page that lands is complete whichever tier it is.
        "paywalled": (audience != "everyone") if audience else None,
        "audio": audio_of(html),
    }
    frontmatter = {key: value for key, value in frontmatter.items() if value is not None}

    body_path.write_text(compose_body(markdown, title, {**frontmatter, "source": item}), encoding="utf-8")
    record = {
        "v": CAPTURE_V,
        "slug": slug,
        "item": item,
        "title": title,
        "body": BODY_NAME,
        "content_type": "text/markdown",
        "fetched_at": now(),
        "frontmatter": frontmatter,
    }
    (directory / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    row["title"] = title
    return row


def holds_capture(directory):
    try:
        record = json.loads((directory / CAPTURE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    body = record.get("body") if isinstance(record, dict) else None
    return isinstance(body, str) and bool(body) and (directory / body).is_file()


def _load(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def main(argv=None, *, sleep=time.sleep, clock=time.monotonic):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture-dir", default=".", help="the ticket's capture directory, holding leaves.json (default: .)")
    ap.add_argument("--plan", default=None, help="the plan to read (default: <capture-dir>/leaves.json)")
    ap.add_argument(
        "--fetch",
        action="store_true",
        help="GET a leaf's page.html when it is absent. Without it a leaf with no page.html is `pending`: "
        "put the rendered DOM there yourself (a licensed job's Playwright session) and run this again",
    )
    ap.add_argument(
        "--deadline-minutes",
        type=float,
        default=20,
        help="stop starting new leaves after this long (default 20): a slice is killed at 30 with no report behind it",
    )
    ap.add_argument("--only", default=None, metavar="URL", help="(re)render this one leaf, even if already captured")
    ap.add_argument("--title", default=None, help="with --only: the post's true title, when the plan's or the meta's is wrong")
    ap.add_argument("--author", default=None, help="with --only")
    ap.add_argument("--published", default=None, help="with --only: YYYY-MM-DD")
    ap.add_argument(
        "--drop-selector",
        action="append",
        default=[],
        metavar="CSS",
        help="passed to to_markdown.py (repeatable): CTA chrome to remove before conversion",
    )
    args = ap.parse_args(argv)

    capture_dir = Path(args.capture_dir).resolve()
    plan = _load(args.plan or capture_dir / PLAN_NAME)
    if plan is None or not isinstance(plan.get("leaves"), list):
        print(f"no readable plan at {args.plan or capture_dir / PLAN_NAME} — run enumerate_archive.py first", file=sys.stderr)
        return 2
    if (args.title or args.author or args.published) and not args.only:
        ap.error("--title/--author/--published describe ONE post: name it with --only")
    overrides = {"title": args.title, "author": args.author, "published": args.published}

    results_path = capture_dir / RESULTS_NAME
    previous = _load(results_path) or {}
    rows = {row["item"]: row for row in previous.get("rows", []) if isinstance(row, dict) and "item" in row}

    started = clock()
    fetched_any = False
    for leaf in plan["leaves"]:
        item = leaf["item"]
        if args.only and item != args.only:
            continue
        # Every leaf is a sibling of the ticket's directory (`_raw/<slug>/<one>`),
        # so the plan's wiki-relative `dir` is resolved without a wiki root.
        name = str(leaf.get("dir") or "").rsplit("/", 1)[-1]
        if name in ("", ".", ".."):
            rows[item] = {"item": item, "dir": leaf.get("dir"), "state": "error", "why": "error", "title": None, "detail": "bad leaf dir"}
            continue
        directory = capture_dir.parent / name
        row = {"item": item, "dir": leaf["dir"], "state": "captured", "why": None, "title": leaf.get("title")}
        if not args.only and holds_capture(directory):
            row["state"] = "on_disk"
        elif (clock() - started) > args.deadline_minutes * 60:
            row.update(state="unreached", why="deadline")
        else:
            try:
                if not (directory / HTML_NAME).is_file():
                    if not args.fetch:
                        row.update(state="pending", why=f"no {HTML_NAME}")
                        rows[item] = row
                        continue
                    if fetched_any:
                        sleep(random.uniform(2, 5))  # one person reading quickly
                    fetched_any = True
                    html = fetch_html(item, sleep=sleep)
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / HTML_NAME).write_text(html, encoding="utf-8")
                row = capture_leaf(
                    directory,
                    leaf,
                    slug=plan.get("slug"),
                    newsletter=plan.get("newsletter"),
                    overrides=overrides if args.only else None,
                    drop_selectors=args.drop_selector,
                )
            except Exception as exc:  # noqa: BLE001 — one post's failure is one row, never the batch
                row.update(state="error", why=why_for(exc), detail=str(exc)[:200])
        rows[item] = row

    if args.only and args.only not in rows:
        print(f"{args.only} is not a leaf of this plan", file=sys.stderr)
        return 2

    ordered = [rows[leaf["item"]] for leaf in plan["leaves"] if leaf["item"] in rows]
    results_path.write_text(json.dumps({"v": 1, "rows": ordered}, indent=2) + "\n", encoding="utf-8")
    tally = {}
    for row in ordered:
        tally[row["state"]] = tally.get(row["state"], 0) + 1
    print(json.dumps({"results": str(results_path), "states": tally}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
