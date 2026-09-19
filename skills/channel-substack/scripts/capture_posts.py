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
3. REFUSES a paywall preview: a page carrying the paywall sentence OUTSIDE
   its content root is never written as a capture (a free post that QUOTES
   the sentence in its body is not a preview). The leaf is recorded
   `paywalled` and the report puts it in `missing[]` as `why: auth`;
4. REFUSES a page with no `.available-content` at all — a login wall
   (`why: auth`), a soft block ("Just a moment…", a CAPTCHA) or a JS shell
   (`why: error`). The converter would fall back to the whole body and the
   block page would land as the article, outcome `ok`. A refused `page.html`
   (3 or 4) is moved aside to `page.refused.html`, so the next run fetches
   again instead of re-reading it for ever;
5. renders the body with this unit's `to_markdown.py` (the sibling file)
   scoped to `.available-content`, then rewrites the top of `page.md` as the
   post's title and a compact facts block — never a `---` YAML block: the
   extractor prepends its own frontmatter and a second one corrupts the page.
   The title and every fact value are folded to ONE line first: venue text
   never opens a heading or a rule in the final page;
6. writes `capture.json` naming `page.md`. Its `title` is `safe_title()` of
   the post's — the page FILE is named from it and the host refuses `: ? / "`
   and friends — while the body's H1 keeps the true title, which also rides
   `frontmatter.source_title` when the two differ. The same facts go in the
   `frontmatter` object (`type`, `published`, `author`, `newsletter`,
   `audience`, `paywalled`, and `audio` on a podcast post).

**It stops fetching a host that said no.** An auth answer (401/403/407, or a
login wall) fails every remaining unfetched leaf on that host as
`auth_expired:<host>` without touching it; so does a proxy denial (`denied`,
raised at once — a denial is not retried) and a 429 that outlasted the
backoff. Leaves whose `page.html` is already there are still rendered.

`--capture-dir` is REQUIRED and is the ticket's `capture_dir` VERBATIM —
wiki-relative, because `llm-wiki-ops run` starts a script at the WIKI ROOT. A
relative `--plan` is resolved INSIDE it.

Everything is written inside the leaf directories under `_raw/<slug>/`, which
is the whole of what a harvest slice may write. Nothing here reaches `dest`:
the page under it is `pipeline extract`'s to write, from these files.

It stops cleanly at `--deadline-minutes` (default 20) — a slice is killed at
thirty with no report behind it, so an unfinished plan has to END in a report,
not in the kill. What it did lands in `results.json` beside the plan, one row
per leaf: `{"item", "dir", "state", "why", "title"}`, `state` one of
`captured`, `on_disk`, `paywalled`, `pending` (no `page.html`, and no
`--fetch`), `unreached` (the deadline came first), `gone` (404/410 — a
refresh ticket's answer), `error`.
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
- 2026-09-19 (review): `--capture-dir` required and wiki-relative (the cwd
  under `llm-wiki-ops run` is the wiki root); `capture.json`'s title is a
  legal filename (`safe_title`) and venue text is folded to one line; a page
  with no content root is an error row, never the article; the paywall
  sentence is looked for outside the content root only; fetching stops on a
  host that answered auth, denied or a persistent 429.
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
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = "Mozilla/5.0 (compatible; llm-wiki-harvest/1.0)"

TICKET_NAME = "ticket.json"
PLAN_NAME = "leaves.json"
RESULTS_NAME = "results.json"
CAPTURE_NAME = "capture.json"
REPORT_NAME = "report.json"
HTML_NAME = "page.html"
REFUSED_NAME = "page.refused.html"
BODY_NAME = "page.md"
CAPTURE_V = 1

CONTENT_CLASS = "available-content"
CONTENT_SELECTOR = f".{CONTENT_CLASS}"

# The block a paid post renders AFTER its free preview — outside the content
# root, which is where it is looked for. Text, not a class: the sentence is
# what the unit has observed; no class name is verified.
PAYWALL_MARKERS = ("This post is for paid subscribers",)

# agent-loop, "Signals a fetch is unusable". Read only off a page that has NO
# content root: a real post may say any of these in its prose.
LOGIN_HEADING = re.compile(r"\s*(sign[ -]?in|log[ -]?in)\b", re.I)
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|auth)(/|$)", re.I)
SOFT_BLOCK_MARKERS = ("just a moment", "captcha", "unusual traffic", "verify you are human", "checking your browser")
GONE_CODES = (404, 410)

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
_HTTP_URL = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.I)
FACT_MAX = 300


def one_line(text):
    """Venue text as ONE line: control characters, newlines and tabs become a
    space. `page.md` is the final page body, taken verbatim — a title carrying
    `\\n# Forged` or `\\n---\\n` must not open a heading or a rule in it."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())


# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "'", "<": "(", ">": ")"}
TITLE_MAX = 120  # characters
# UTF-8 bytes: the host checks no length, and a filename is capped in BYTES (255 on ext4/APFS) with
# `.md` appended — 100 CJK characters is 300 bytes and the REAL extractor dies
# `OSError: [Errno 36] File name too long` (measured, channel-youtube).
TITLE_MAX_BYTES = 200


def safe_title(text, fallback="Untitled"):
    text = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))  # control chars, newlines, tabs
    for bad, good in _TITLE_SWAPS.items():
        text = text.replace(bad, good)
    text = " ".join(text.split()).lstrip(". ").rstrip(" .")
    cut = text[:TITLE_MAX]
    while len(cut.encode("utf-8")) > TITLE_MAX_BYTES:
        cut = cut[:-1]
    if cut != text:
        text = cut.rstrip(" .-") + "…"
    if text.casefold() == "index":
        # `index.md` is the host's one RESERVED page name (note.py::RESERVED): its page walker skips
        # it, so the page never appears in `known[]` and the item is re-pulled forever.
        text = f"{text} (page)"
    return text or fallback


def valid_day(value):
    """`YYYY-MM-DD` exactly, or None — a date is a fact, never free text."""
    return value if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


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
    selector-scoped body never mentions it. Found here, said in the facts —
    `http(s)` and nothing a link could not hold, or not at all."""
    for attrs in _tags(html, "audio"):
        src = (attrs.get("src") or "").strip()
        if _HTTP_URL.fullmatch(src):
            return src
    return None


class _Shape(HTMLParser):
    """What a fetched page IS, before anything converts it: whether it has the
    content root, and the text that sits outside it. Stdlib on purpose — the
    check has to run (and be tested) where the converter's dependencies are
    not. Depth is counted on the ROOT's own tag only, so an unclosed `<p>` or
    `<li>` inside the post cannot stretch the root over the rest of the page."""

    _SILENT = ("script", "style", "noscript", "template")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.roots = 0
        self.title = ""
        self.h1 = []
        self.outside = []
        self._root_tag, self._depth, self._silent, self._in = None, 0, 0, None

    def handle_starttag(self, tag, attrs):
        if self._depth:
            self._depth += tag == self._root_tag
        elif CONTENT_CLASS in (dict(attrs).get("class") or "").split():
            self.roots += 1
            self._root_tag, self._depth = tag, 1
        if tag in self._SILENT:
            self._silent += 1
        elif tag == "title":
            self._in = "title"
        elif tag == "h1":
            self._in = "h1"
            self.h1.append("")

    def handle_endtag(self, tag):
        if self._depth and tag == self._root_tag:
            self._depth -= 1
        if tag in self._SILENT:
            self._silent = max(0, self._silent - 1)
        elif tag in ("title", "h1"):
            self._in = None

    def handle_data(self, data):
        if self._silent:
            return
        if self._in == "title":
            self.title += data
            return  # the tab title is not the page: a post may be NAMED after the sentence
        if self._in == "h1":
            self.h1[-1] += data
        if not self._depth:
            self.outside.append(data)


def shape_of(html):
    shape = _Shape()
    try:
        shape.feed(html)
        shape.close()
    except Exception:  # noqa: BLE001 — hostile markup: what was read so far is the answer
        pass
    return shape


def is_paywall_preview(html):
    """Is this the truncated free preview of a paid post?

    The paywall BLOCK is the signal, not JSON-LD's `isAccessibleForFree:
    false` — a licensed session reads the whole of a post that still declares
    itself paid, and that is a complete capture. And the block sits AFTER the
    content root, so the sentence is looked for outside `.available-content`
    only: a free post that quotes it in its prose is not a preview.
    """
    outside = one_line(" ".join(shape_of(html).outside))
    return any(marker in outside for marker in PAYWALL_MARKERS)


def unusable(html):
    """`(why, detail)` when this page is not a post at all, else None.

    No content root means the converter would fall back to the whole body and
    a block page would land as the article. agent-loop's "Signals a fetch is
    unusable" names the three: a login wall is `auth`; a soft block or a JS
    shell is `error` — fail the item, never evade.
    """
    shape = shape_of(html)
    if shape.roots:
        return None
    if any(LOGIN_HEADING.match(text) for text in (shape.title, *shape.h1)):
        return ("auth", f"login wall: the page is headed {one_line(shape.title or shape.h1[0])[:80]!r}")
    text = one_line(" ".join([shape.title, *shape.outside])).lower()
    for marker in SOFT_BLOCK_MARKERS:
        if marker in text:
            return ("error", f"soft block: the page says {marker!r} and has no {CONTENT_SELECTOR}")
    return ("error", f"no {CONTENT_SELECTOR} on the page — a JS shell, or not a post")


class LoginWall(Exception):
    """The fetch was redirected to a sign-in page: the session is dead."""


def facts_block(facts):
    labels = (
        ("published", "Published"),
        ("author", "Author"),
        ("newsletter", "Newsletter"),
        ("audience", "Audience"),
        ("audio", "Audio"),
        ("source", "Source"),
    )
    # One `- **Label:** value` line per fact, whatever the venue put in the value.
    lines = [(label, one_line(facts[key])[:FACT_MAX]) for key, label in labels if facts.get(key)]
    return "\n".join(f"- **{label}:** {value}" for label, value in lines if value) + "\n"


def compose_body(markdown, title, facts):
    """Title, facts, then the post — with the converter's own leading H1
    dropped, since it is the `<title>` tag or a duplicate of this one."""
    body = re.sub(r"\A\s*#\s+[^\n]*\n+", "", markdown)
    if body.lstrip().startswith("---"):
        # A leading rule would read as the opening of a YAML block once the
        # extractor's own frontmatter sits above it.
        body = re.sub(r"\A\s*---+\s*\n", "", body)
    return f"# {one_line(title) or 'Untitled'}\n\n{facts_block(facts)}\n{body.strip()}\n"


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
    if isinstance(exc, LoginWall):
        return "auth"
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
    after a denial). A DENIAL itself is raised at once — the proxy will say
    the same thing every time — and so is a redirect to a sign-in page.
    Never anything cleverer: a soft block is a failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    refused_once = False
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if LOGIN_PATH.search(urlsplit(response.geturl()).path) and not LOGIN_PATH.search(urlsplit(url).path):
                    raise LoginWall(f"redirected to {response.geturl()[:200]}")
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in BACKOFF_CODES or attempt == tries - 1:
                raise
            sleep(60 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError) as exc:
            if any(marker in str(exc).lower() for marker in DENIED_MARKERS):
                raise
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
    refused = ("paywalled", "auth", None) if is_paywall_preview(html) else None
    if refused is None:
        not_a_post = unusable(html)
        refused = ("error", *not_a_post) if not_a_post else None
    if refused is not None:
        # Aside, not deleted: it is the evidence, and out of `page.html`'s way
        # the next run fetches again instead of re-reading this for ever.
        (directory / HTML_NAME).replace(directory / REFUSED_NAME)
        for name in (BODY_NAME, CAPTURE_NAME):
            (directory / name).unlink(missing_ok=True)
        row.update(state=refused[0], why=refused[1])
        if refused[2]:
            row["detail"] = refused[2]
        return row

    body_path = directory / BODY_NAME
    render(directory / HTML_NAME, body_path, item, drop_selectors)
    markdown = body_path.read_text(encoding="utf-8")

    first_h1 = re.match(r"\A\s*#\s+([^\n]+)", markdown)
    candidates = (
        overrides.get("title"),
        leaf.get("title"),
        meta(html, "og:title"),
        first_h1.group(1) if first_h1 else None,
    )
    url_slug = urlsplit(item).path.rstrip("/").rsplit("/", 1)[-1]
    # The post's TRUE title, on one line: the body's H1. The page's FILE is
    # named from `capture.json`'s, which has to be one the host will take.
    venue_title = next((one_line(text) for text in candidates if isinstance(text, str) and one_line(text)), "")
    title = safe_title(venue_title, fallback=safe_title(url_slug))
    venue_title = venue_title or title
    audience = one_line(leaf.get("audience"))[:FACT_MAX] or None
    frontmatter = {
        "type": "article",
        "published": valid_day(overrides.get("published")) or valid_day(leaf.get("published")) or published_of(html),
        "author": one_line(overrides.get("author") or author_of(html))[:FACT_MAX] or None,
        "newsletter": one_line(newsletter)[:FACT_MAX] or None,
        "audience": audience,
        # A fact about the POST, not about this capture: a preview is refused
        # above, so a page that lands is complete whichever tier it is.
        "paywalled": (audience != "everyone") if audience else None,
        "audio": audio_of(html),
        "source_title": venue_title if venue_title != title else None,
    }
    frontmatter = {key: value for key, value in frontmatter.items() if value is not None}

    source = item if _HTTP_URL.fullmatch(item) else None
    body_path.write_text(compose_body(markdown, venue_title, {**frontmatter, "source": source}), encoding="utf-8")
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


def halt_for(exc, why, host):
    """Why nothing more is fetched from `host` after this failure, or None.

    agent-loop: on auth expiry fail every remaining item on that domain with
    `auth_expired:<domain>` and STOP fetching it; a host the proxy denied is
    reported and left alone (widening is the foreman's); a soft block that
    outlasted the backoff fails the item — never evade. Anything else (a 5xx,
    a timeout, one post's 404) is that post's own failure.
    """
    if why == "auth":
        return f"auth_expired:{host}"
    if why == "denied":
        return f"denied:{host} — the slice's egress does not include it"
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return f"soft block: {host} still answered 429 after the backoff"
    return None


def main(argv=None, *, sleep=time.sleep, clock=time.monotonic, fetch=None):
    fetch = fetch or fetch_html
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--capture-dir",
        required=True,
        help="REQUIRED: ticket.json's `capture_dir`, verbatim. It is WIKI-RELATIVE — `llm-wiki-ops run` starts "
        "this script at the wiki root — and holds leaves.json",
    )
    ap.add_argument(
        "--plan",
        default=None,
        help="the plan to read (default: <capture-dir>/leaves.json); a relative path is resolved INSIDE --capture-dir",
    )
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

    if not Path(args.capture_dir).is_dir():
        ap.error(
            f"--capture-dir {args.capture_dir!r} is no directory under {Path.cwd()} — give ticket.json's "
            f"`capture_dir` verbatim: it is wiki-relative, and `llm-wiki-ops run` starts a script at the wiki root"
        )
    capture_dir = Path(args.capture_dir).resolve()
    plan_path = capture_dir / (args.plan or PLAN_NAME)  # an absolute --plan stays what it is
    plan = _load(plan_path)
    if plan is None or not isinstance(plan.get("leaves"), list):
        print(f"no readable plan at {plan_path} — run enumerate_archive.py --capture-dir {args.capture_dir} first", file=sys.stderr)
        return 2
    # Whatever is captured from here on, a report already standing is not its report.
    (capture_dir / REPORT_NAME).unlink(missing_ok=True)
    if (args.title or args.author or args.published) and not args.only:
        ap.error("--title/--author/--published describe ONE post: name it with --only")
    if args.published and not valid_day(args.published):
        ap.error(f"--published {args.published!r}: want YYYY-MM-DD")
    overrides = {"title": args.title, "author": args.author, "published": args.published}

    results_path = capture_dir / RESULTS_NAME
    previous = _load(results_path) or {}
    rows = {row["item"]: row for row in previous.get("rows", []) if isinstance(row, dict) and "item" in row}

    started = clock()
    fetched_any = False
    halted = {}  # host -> (why, detail): nothing more is fetched from it this run
    for leaf in plan["leaves"]:
        if not isinstance(leaf, dict) or not isinstance(leaf.get("item"), str):
            continue
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
                    host = urlsplit(item).netloc.lower()
                    if host in halted:
                        why, detail = halted[host]
                        row.update(state="error", why=why, detail=f"{detail} — not fetched")
                        rows[item] = row
                        continue
                    if fetched_any:
                        sleep(random.uniform(2, 5))  # one person reading quickly
                    fetched_any = True
                    try:
                        html = fetch(item, sleep=sleep)
                    except Exception as exc:  # noqa: BLE001 — classified below, never raised past the row
                        why = why_for(exc)
                        gone = isinstance(exc, urllib.error.HTTPError) and exc.code in GONE_CODES
                        row.update(state="gone" if gone else "error", why=why, detail=str(exc)[:200])
                        detail = halt_for(exc, why, host)
                        if detail:
                            halted[host] = (why, detail)
                            row["detail"] = f"{detail} ({str(exc)[:120]})"
                        rows[item] = row
                        continue
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
            if row["state"] == "error" and row["why"] == "auth":
                # A login wall where a post should be: the session is dead for
                # the whole host, not for this post. (A paywall is a skip.)
                host = urlsplit(item).netloc.lower()
                halted.setdefault(host, ("auth", f"auth_expired:{host}"))
        rows[item] = row

    if args.only and args.only not in rows:
        print(f"{args.only} is not a leaf of this plan", file=sys.stderr)
        return 2

    ordered = [rows[leaf["item"]] for leaf in plan["leaves"] if isinstance(leaf, dict) and leaf.get("item") in rows]
    results_path.write_text(json.dumps({"v": 1, "rows": ordered}, indent=2) + "\n", encoding="utf-8")
    tally = {}
    for row in ordered:
        tally[row["state"]] = tally.get(row["state"], 0) + 1
    print(json.dumps({"results": str(results_path), "states": tally}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
