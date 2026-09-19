#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Capture each planned Substack post's BYTES — the page as the venue served it.

platform: substack
scope: platform-general (no hardcoded domain/slugs)

Harvest is bytes. Reads the plan `enumerate_archive.py` wrote (`leaves.json`
in the ticket's capture directory) and, for each leaf, newest first:

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
   (`why: error`). The converter would fall back to the whole body, so a block
   page would become the article. A refused `page.html` (3 or 4) is moved
   aside to `page.refused.html`, so the next run fetches again instead of
   re-reading it for ever;
5. writes `leaf.json` — what the archive row said about this post: its title,
   the day it was published, its audience, the newsletter it is on — beside
   the bytes, because the archive is the only place those are stated and a
   process ticket only ever sees this one directory;
6. writes `capture.json`, naming `page.html` as the body. It is FLAT —
   `slug`, `item`, `title`, `body`, `content_type`, `fetched_at` — and its
   `title` is `safe_title()` of the post's, because the page FILE is named
   from it and the host refuses `: ? / "` and friends.

The post HTML becomes a page in the PROCESS step, one process ticket per
captured leaf. Nothing here renders markdown, writes a summary or a `page.md`,
or puts a `frontmatter` object on `capture.json`: a page's keys belong to the
step that writes the page.

**It stops fetching a host that said no.** An auth answer (401/403/407, or a
login wall) fails every remaining unfetched leaf on that host as
`auth_expired:<host>` without touching it; so does a proxy denial (`denied`,
raised at once — a denial is not retried) and a 429 that outlasted the
backoff. Leaves whose `page.html` is already there are still captured.

`--capture-dir` is REQUIRED and is the ticket's `capture_dir` VERBATIM —
wiki-relative, because `llm-wiki-ops run` starts a script at the WIKI ROOT. A
relative `--plan` is resolved INSIDE it.

Everything is written inside the leaf directories under `_raw/<slug>/`, which
is the whole of what a harvest slice may write. Nothing here reaches `dest`.

It stops cleanly at `--deadline-minutes` (default 20) — a slice is killed at
thirty with no report behind it, so an unfinished plan has to END in a report,
not in the kill. What it did lands in `results.json` beside the plan, one row
per leaf: `{"item", "dir", "state", "why", "title"}`, `state` one of
`captured`, `on_disk`, `paywalled`, `pending` (no `page.html`, and no
`--fetch`), `unreached` (the deadline came first), `gone` (404/410 — a
refresh ticket's answer), `error`.
`write_report.py` turns the plan, these rows and what is actually on disk
into `report.json`.

`--only <post url>` re-captures that one leaf — with `--fetch`, re-fetching it
— even if it was already captured, and leaves every other row of
`results.json` as it was.
"""

import argparse
import html as htmllib
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
LEAF_NAME = "leaf.json"
BODY_NAME = HTML_NAME  # what `capture.json` names as the body: the bytes, as they arrived
CONTENT_TYPE = "text/html"
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

_TAG = re.compile(r"<(meta)\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
FACT_MAX = 300


def one_line(text):
    """Venue text as ONE line: control characters, newlines and tabs become a
    space. The page body is taken verbatim from these — a title carrying
    `\\n# Forged` or `\\n---\\n` must not open a heading or a rule in it."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())


# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "\u2019", "'": "\u2019", "<": "(", ">": ")"}
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


def capture_leaf(directory, leaf, *, slug, newsletter):
    """One leaf: `page.html` -> `leaf.json` + `capture.json`. Returns the row."""
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
        for name in (LEAF_NAME, CAPTURE_NAME):
            (directory / name).unlink(missing_ok=True)
        row.update(state=refused[0], why=refused[1])
        if refused[2]:
            row["detail"] = refused[2]
        return row

    url_slug = urlsplit(item).path.rstrip("/").rsplit("/", 1)[-1]
    # The post's TRUE title: the archive's, else the page's own. The page FILE
    # is named from `capture.json`'s, which has to be one the host will take.
    candidates = (leaf.get("title"), meta(html, "og:title"))
    venue_title = next((one_line(text) for text in candidates if isinstance(text, str) and one_line(text)), "")
    title = safe_title(venue_title, fallback=safe_title(url_slug))
    audience = one_line(leaf.get("audience"))[:FACT_MAX] or None
    said = {
        "v": CAPTURE_V,
        "item": item,
        # What the ARCHIVE said, kept because a process ticket sees this one
        # directory and the archive row is nowhere in it.
        "title": venue_title or title,
        "published": valid_day(leaf.get("published")),
        "audience": audience,
        "newsletter": one_line(newsletter)[:FACT_MAX] or None,
    }
    (directory / LEAF_NAME).write_text(json.dumps(said, indent=2) + "\n", encoding="utf-8")
    record = {
        "v": CAPTURE_V,
        "slug": slug,
        "item": item,
        "title": title,
        "body": BODY_NAME,
        "content_type": CONTENT_TYPE,
        "fetched_at": now(),
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
    ap.add_argument("--only", default=None, metavar="URL", help="(re)capture this one leaf, even if it is already captured")
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
                row = capture_leaf(directory, leaf, slug=plan.get("slug"), newsletter=plan.get("newsletter"))
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
