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
`--report --ticket <id>` turns the plan, these rows and what is actually on
disk into a `tickets update` post — see `build_update` and its call in
`main`, the arm SKILL.md's step 3 runs, last.

`--only <post url>` re-captures that one leaf — with `--fetch`, re-fetching it
— even if it was already captured, and leaves every other row of
`results.json` as it was.
"""

import argparse
import hashlib
import html as htmllib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = "Mozilla/5.0 (compatible; llm-wiki-harvest/1.0)"

PLAN_NAME = "leaves.json"
RESULTS_NAME = "results.json"
CAPTURE_NAME = "capture.json"
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


# --------------------------------------------------------------- the front door


OPS = "llm-wiki-ops"


def front_door() -> list:
    """The front door, as an argv prefix.

    A hosted run exports `LLM_WIKI_OPS`, naming the CLI it was itself reached
    by — a command LINE, not a path — and that is the one spelling a jail is
    sure to carry. Otherwise the bare name on PATH. Empty when there is
    neither."""
    named = os.environ.get("LLM_WIKI_OPS")
    if named:
        return shlex.split(named)
    found = shutil.which(OPS)
    return [found] if found else []


def open_ticket(ticket: str, stage: str | None = None) -> dict:
    """This worker's own ticket (A-1), through the front door. Exits naming
    the refusal."""
    me = Path(__file__).stem
    door = front_door()
    if not door:
        sys.exit(f"{me}: `{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — the front door is how this unit reaches the plugin")
    argv = [*door, "--json", "pipeline", "tickets", "open", ticket]
    if stage:
        argv.append(f"stage={stage}")
    cp = subprocess.run(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        sys.exit(f"{me}: `tickets open {ticket}` refused — {(cp.stdout + cp.stderr).strip()}")
    try:
        return json.loads(cp.stdout)["ticket"]
    except (ValueError, KeyError) as exc:
        sys.exit(f"{me}: `tickets open {ticket}` did not answer a ticket ({exc}) — {cp.stdout}")


def post_update(
    ticket: str,
    stage: str,
    status: str,
    *,
    reason: str | None = None,
    captured=(),
    missing=(),
    written_from: str | None = None,
    produced: int | None = None,
    note: str | None = None,
) -> int:
    """This worker's progress (A-2), through the front door. `missing` is an
    iterable of `(host, url, why)`; a `,` inside `url` is typed as `%2C`,
    the side note every unit's `missing=` build follows the same way."""
    me = Path(__file__).stem
    door = front_door()
    if not door:
        sys.exit(f"{me}: `{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — the front door is how this unit posts progress")
    argv = [*door, "--json", "pipeline", "tickets", "update", ticket, f"stage={stage}", f"status={status}"]
    if reason:
        argv.append(f"reason={reason}")
    for directory in captured:
        argv.append(f"captured={directory}")
    for host, url, why in missing:
        argv.append(f"missing={host},{url.replace(',', '%2C')},{why}")
    if written_from:
        argv.append(f"written_from={written_from}")
    if produced is not None:
        argv.append(f"produced={produced}")
    if note:
        argv.append(f"note={note}")
    cp = subprocess.run(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        print(f"{me}: `tickets update` refused — {(cp.stdout + cp.stderr).strip()}", file=sys.stderr)
    return cp.returncode


# ------------------------------------------------------ the report (post-capture)
#
# What `write_report.py` did, ported whole: `captured[]` is not what a worker
# remembers doing, it is every planned leaf whose directory holds a COMPLETE
# capture (`capture.json` naming a body that is there), read at the moment
# this runs — so a leaf a previous, killed slice captured is counted too.


TITLE_ILLEGAL = '/\\:*?"<>|'  # `page/note.py::ILLEGAL`
QUALIFIER_MAX = 60
WHYS = ("denied", "timeout", "auth", "error")


def captured_record(directory):
    """The capture record here, if the capture is COMPLETE; else None."""
    record = _load(Path(directory) / CAPTURE_NAME)
    body = record.get("body") if record else None
    if not isinstance(body, str) or not body or not (Path(directory) / body).is_file():
        return None
    return record


def page_key(title: str) -> str:
    """What two titles share when they make one page file: the host strips, a
    case-insensitive filesystem folds case, and APFS folds Unicode form too —
    `é` composed and `e` + combining accent are one name there."""
    return unicodedata.normalize("NFC", title.strip()).casefold()


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


def leaf_qualifiers(leaf):
    """What tells this post from a namesake: the day it was published — the one
    thing a newsletter re-using a title always changes — then its URL's hash."""
    published = leaf.get("published")
    day = _DAY.match(published) if isinstance(published, str) else None
    return [day.group(0) if day else None, hashlib.sha1(leaf["item"].encode("utf-8")).hexdigest()[:8]]


def settle_titles(leaves, leaf_root):
    """One page per post: in plan order the first leaf to make a filename keeps
    its title, and a later one is retitled in its own `capture.json`.

    Here and not earlier, because a post's final title is only settled once it
    is captured (the plan's, else `og:title`, else a hand `--only --title`),
    one `--only` pass sees one leaf, and this runs last, over all of them,
    before anything is extracted. A planned post that did not land still holds
    the title the archive gave it, so what landed is titled the same whether
    or not its namesake did.
    """
    taken = {}
    for leaf in leaves:
        directory = Path(leaf_root) / leaf["dir"].rsplit("/", 1)[-1]
        record = captured_record(directory)
        if record is None:
            if isinstance(leaf.get("title"), str) and leaf["title"].strip():
                unique_title(leaf["title"], leaf_qualifiers(leaf), taken)
            continue
        title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
        held = title or Path(record["body"]).stem
        final = unique_title(held, leaf_qualifiers(leaf), taken)
        if final != held:
            record["title"] = final
            (directory / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def build_update(plan, rows, leaf_root, *, extra_missing=()):
    """The `tickets update` arguments, as a dict of `post_update` kwargs.
    Pure but for reading the leaf directories — and for settling the titles in
    them, which is what makes `captured[]` true."""
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
        if row.get("state") in ("paywalled", "error", "gone"):
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
    halted = sorted({str(row["detail"]).split(" ", 1)[0] for row in by_item.values()
                     if str(row.get("detail") or "").startswith("auth_expired:")})
    if halted:
        why_partial.append(f"{', '.join(halted)} — fetching stopped there")
    if summary.get("truncated"):
        why_partial.append("the archive goes on past this plan's cap; the job's next pull continues through known[]")
    if summary.get("fetch_failed"):
        why_partial.append(f"the archive walk stopped early ({summary['fetch_failed']})")

    refresh = plan.get("refresh")
    if captured:
        # P-5: `partial` means a re-run of THIS stage in THIS directory gets
        # more — the deadline or an archive-page cap, both re-runnable. A
        # paywall or a fetch error is a lasting fact about that one post:
        # `ok`, with the shortfall named in `reason` and the post in `missing[]`.
        status = "partial" if (unreached or summary.get("truncated")) else "ok"
        reason = "; ".join(why_partial) if why_partial else None
    elif refresh and leaves and all((by_item.get(leaf["item"]) or {}).get("state") == "gone" for leaf in leaves):
        # `gone` is a refresh ticket's alone — the source answered 404 or 410.
        status = "gone"
        reason = "; ".join(str((by_item[leaf["item"]]).get("detail") or "404/410") for leaf in leaves)
    elif leaves:
        status = "failed"
        if missing and all(entry["why"] == "auth" for entry in missing):
            reason = f"auth_expired:{plan.get('newsletter')}"
        else:
            reason = "; ".join(why_partial) or "nothing captured"
    elif summary.get("fetch_failed"):
        status, reason = "failed", f"the archive would not load ({summary['fetch_failed']})"
    elif summary.get("skipped_by_scope") and not summary.get("skipped_known"):
        status = "failed"
        reason = (
            f"harvest.scope kept none of {summary['skipped_by_scope']} posts — an archive job needs "
            f"harvest.scope=domain, on the host its posts are served from"
        )
    else:
        # Nothing was owed — and WHY is the truth, not always `known:`.
        status = "ok"
        held, paid, dropped = (summary.get(key) or 0 for key in ("skipped_known", "skipped_paywalled", "skipped_excluded"))
        where = plan.get("newsletter")
        if held:
            reason = f"known: nothing new on {where} ({held} already held" + (f", {paid} paid-tier not fetched)" if paid else ")")
        elif paid:
            reason = (
                f"paywalled: every post in range on {where} is paid-tier ({paid}) and harvest.access is "
                f"{plan.get('access') or 'free'} — nothing to capture"
            )
        elif dropped:
            reason = f"excluded: harvest.exclude_urls dropped every post in range on {where} ({dropped})"
        else:
            reason = f"nothing in range on {where}"

    return {"status": status, "reason": reason, "captured": captured, "missing": missing}


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
        help="REQUIRED: the ticket's `capture_dir`, verbatim. It is WIKI-RELATIVE — `llm-wiki-ops run` starts "
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
    ap.add_argument(
        "--report", action="store_true",
        help="post progress instead of capturing: read the plan, the leaf directories and results.json on disk, "
        "and post `tickets update` — the arm SKILL.md's step 3 runs, last. With --written-from, it is the "
        "PROCESS ticket's report instead: the pages a build wrote, and no capture",
    )
    ap.add_argument("--ticket", default=None, help="REQUIRED with --report: the ticket id (`tickets open`'s own)")
    ap.add_argument(
        "--missing", action="append", default=[], metavar="URL=WHY",
        help=f"--report, harvest only: a url you could not get yourself (repeatable); WHY is one of {', '.join(WHYS)}",
    )
    ap.add_argument(
        "--written-from", default=None, metavar="FILE",
        help="--report, process only: a JSON list of wiki-relative pages, relative to --capture-dir — the PROCESS "
        "ticket's report: `written_from=` is posted and no capture is claimed",
    )
    ap.add_argument(
        "--process", action="store_true",
        help="--report, with no --written-from: a capture that earned no page — post --outcome/--reason instead",
    )
    ap.add_argument("--outcome", choices=("ok", "failed"), default="ok", help="--report --process only")
    ap.add_argument("--reason", default=None, help="--report --process only, paired with --outcome")
    args = ap.parse_args(argv)

    if not Path(args.capture_dir).is_dir():
        ap.error(
            f"--capture-dir {args.capture_dir!r} is no directory under {Path.cwd()} — give the ticket's "
            f"`capture_dir` verbatim: it is wiki-relative, and `llm-wiki-ops run` starts a script at the wiki root"
        )
    capture_dir = Path(args.capture_dir).resolve()

    if args.report and args.written_from:
        if not args.ticket:
            ap.error("--report needs --ticket")
        return post_update(args.ticket, "process", "ok", written_from=args.written_from)

    if args.report and args.process:
        if not args.ticket:
            ap.error("--report needs --ticket")
        return post_update(args.ticket, "process", args.outcome, reason=args.reason)

    if args.report:
        if not args.ticket:
            ap.error("--report needs --ticket")
        extra = []
        for spec in args.missing:
            url, _, why = spec.rpartition("=")
            if not url or why not in WHYS:
                ap.error(f"--missing {spec!r}: want URL=WHY, WHY one of {', '.join(WHYS)}")
            extra.append((url, why))
        plan_path = capture_dir / (args.plan or PLAN_NAME)
        plan = _load(plan_path) or {"leaves": [], "summary": {"fetch_failed": f"no {PLAN_NAME}"}}
        rows = (_load(capture_dir / RESULTS_NAME) or {}).get("rows") or []
        update = build_update(plan, rows, capture_dir.parent, extra_missing=extra)
        return post_update(
            args.ticket, "harvest", update["status"], reason=update["reason"],
            captured=[entry["dir"] for entry in update["captured"]],
            missing=[(m["host"], m["url"], m["why"]) for m in update["missing"]],
        )

    plan_path = capture_dir / (args.plan or PLAN_NAME)  # an absolute --plan stays what it is
    plan = _load(plan_path)
    if plan is None or not isinstance(plan.get("leaves"), list):
        print(f"no readable plan at {plan_path} — run enumerate_archive.py --capture-dir {args.capture_dir} first", file=sys.stderr)
        return 2

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
