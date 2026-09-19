#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["beautifulsoup4", "markdownify"]
# ///
"""Convert captured page.html to page.md.

This unit's own copy. It shipped in the ops plugin's `scripts/` until the
2026-09-11 cut-over, which dropped it: the rebuilt extractor converts HTML
itself, with no selectors. A unit that knows its venue's content root still
needs the selector-aware converter, so it carries one — stdlib plus PEP 723
dependencies, importing nothing from the plugin. Restored unchanged from
llm-wiki-plugins `parked/ops-v1-2026-09-11`.

Use whenever the fetch tool didn't produce markdown itself (Playwright, raw
HTTP). Firecrawl captures already have markdown — don't re-convert those.

  to_markdown.py page.html [--out page.md|-] [--selector CSS] [--base-url URL]
                 [--drop-selector CSS ...] [--title-selector CSS]

This script WRITES THE FILE ITSELF — it is not a filter. Without --out it
writes `page.md` beside the html, and its stdout carries nothing (the status
line goes to stderr). `--out -` is the filter mode: markdown on stdout, no
file written. Do not redirect stdout over the same path you let it write;
pick one of the two modes.

--selector picks the content root (use the venue skill's
content_selector, e.g. ".available-content"). Without it, common candidates
are tried and the one with the most text wins. Chrome (script/style/nav/
header/footer/forms) is stripped before conversion. Uses markdownify when
available; falls back to a built-in converter otherwise, so `uv run` and
bare python3 both work.

Exit status: 0 on success, INCLUDING the suspiciously-short case (the file is
written either way — the warning goes to stderr, so a caller that treats
nonzero as failure does not throw away a good capture of a genuinely short
page). Nonzero means the conversion did not happen.
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    sys.exit("beautifulsoup4 required — run via `uv run to_markdown.py ...`")

CANDIDATES = ["article", '[class*="available-content"]', ".post-content", '[class*="lesson-content"]', "main", "body"]
STRIP_TAGS = ["script", "style", "noscript", "template", "nav", "header", "footer", "form", "button", "svg", "aside"]

PLAYER_IFRAME_RE = re.compile(
    r"(youtube\.com/embed/|youtube-nocookie\.com/embed/|youtu\.be/|"
    r"player\.vimeo\.com/video/|(fast\.)?wistia\.(net|com)/embed/|"
    r"loom\.com/embed/|stream\.mux\.com/|player\.mux\.com/|"
    r"play\.hubspotvideo\.com/|cdn-media\.circle\.so/)"
)

# Custom video/audio web components (Circle's <hls-video>, <mux-player>,
# <media-player>, …). Matched by tag-name suffix so new players are covered
# without an allowlist.
CUSTOM_MEDIA_RE = re.compile(r"^[a-z][a-z0-9]*-(video|audio|player)$")


# Alphanumeric sentinel survives markdownify escaping; rewritten to an HTML
# comment after conversion. ZZZ delimits the ordinal.
def _sentinel(typ, n):
    return f"\n\nMEDIAPLACEHOLDER{typ}ZZZ{n}ZZZ\n\n"


SENTINEL_RE = re.compile(r"MEDIAPLACEHOLDER(video|audio|embed)ZZZ(\d+)ZZZ")


def drop_selectors(root, selectors):
    """Remove every node matching `selectors` from the content root.

    At the DOM level, before markdown conversion, because that is the only
    point where the structure still exists: once a course-module nav or a CTA
    block is markdown, it is indistinguishable from prose and can only be
    matched by fragile text patterns. One real corpus was ~90% boilerplate by
    volume — a repeated module table of contents, a "Free Download" heading, a
    workshop CTA and a legal disclaimer on all 76 lesson pages — and all of it
    collapses to three or four selectors.

    Returns the number of nodes removed, so a caller can report a selector
    that matched nothing rather than leaving it to be discovered in the note.
    """
    removed = 0
    for sel in selectors or ():
        matched = root.select(sel)
        if not matched:
            print(f"warning: --drop-selector {sel!r} matched nothing", file=sys.stderr)
        for node in matched:
            node.decompose()
            removed += 1
    return removed


def pick_title(soup, root, selector):
    """The venue's own title rule, when the generic one is wrong.

    `<title>` then `h1` is a reasonable default and was wrong on 49 of 77
    captures at one venue: the pages have no `h1` and the CMS reuses a single
    `<title>` per course, so 27 lessons came out named "Start Here". A title
    is a venue property, and it is indexed — this is metadata correctness, not
    cosmetics.
    """
    if not selector:
        return None
    node = (root.select_one(selector) if root is not None else None) or soup.select_one(selector)
    if node is None:
        print(f"warning: --title-selector {selector!r} matched nothing", file=sys.stderr)
        return None
    return node.get_text(strip=True) or None


def pick_root(soup, selector):
    if selector:
        node = soup.select_one(selector)
        if node:
            return node
        print(f"warning: selector {selector!r} matched nothing; falling back", file=sys.stderr)
    best, best_len = None, -1
    for sel in CANDIDATES:
        node = soup.select_one(sel)
        if node:
            n = len(node.get_text(strip=True))
            if n > best_len:
                best, best_len = node, n
        if best_len > 500:  # good enough; earlier candidates are more specific
            break
    return best or soup


def mark_media(root, base_url):
    """Replace <video>/<audio>/player-<iframe> with an ordered text sentinel so
    their position survives markdown conversion. Non-player iframes are removed.
    Also recognizes custom player web components whose tag name matches
    *-video/*-audio/*-player (e.g. <hls-video>), per CUSTOM_MEDIA_RE.
    Runs BEFORE clean() strips tags — must run before STRIP_TAGS removal but the
    tags here aren't in STRIP_TAGS, so order vs clean() is not critical; call it
    inside clean() before the img loop."""
    from bs4 import NavigableString

    counts = {"video": 0, "audio": 0, "embed": 0}

    def _is_media(tag):
        return tag.name in ("video", "audio", "iframe") or bool(tag.name and CUSTOM_MEDIA_RE.match(tag.name))

    for tag in root.find_all(_is_media):
        name = tag.name
        if name == "iframe":
            src = tag.get("src") or tag.get("data-src") or ""
            full = urljoin(base_url or "", src)
            if not PLAYER_IFRAME_RE.search(full):
                tag.decompose()  # non-player iframe: drop as before
                continue
            typ = "embed"
        elif name == "audio" or name.endswith("-audio"):
            typ = "audio"
        else:
            typ = "video"  # <video>, <*-video>, <*-player>
        counts[typ] += 1
        tag.replace_with(NavigableString(_sentinel(typ, counts[typ])))
    return root


def clean(root, base_url):
    mark_media(root, base_url)
    for tag in root.find_all(STRIP_TAGS):
        tag.decompose()
    for img in root.find_all("img"):
        # prefer real URLs over lazy placeholders; drop data: images (icons)
        for attr in ("data-src", "data-lazy-src", "data-original"):
            if img.get(attr):
                img["src"] = img[attr]
                break
        if img.get("src", "").startswith("data:"):
            img.decompose()
            continue
        if base_url and img.get("src"):
            img["src"] = urljoin(base_url, img["src"])
    if base_url:
        for a in root.find_all("a", href=True):
            a["href"] = urljoin(base_url, a["href"])
    return root


# ------------------------------------------------- fallback converter


def _fb(node, ctx=None):
    """Minimal markdown emitter — covers article-shaped content."""
    ctx = ctx or {"list": []}
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    kids = lambda: "".join(_fb(c, ctx) for c in node.children)  # noqa: E731
    name = node.name
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return f"\n\n{'#' * int(name[1])} {kids().strip()}\n\n"
    if name == "p":
        return f"\n\n{kids().strip()}\n\n"
    if name == "br":
        return "  \n"
    if name == "hr":
        return "\n\n---\n\n"
    if name in ("strong", "b"):
        t = kids().strip()
        return f"**{t}**" if t else ""
    if name in ("em", "i"):
        t = kids().strip()
        return f"*{t}*" if t else ""
    if name == "code" and (not node.parent or node.parent.name != "pre"):
        return f"`{node.get_text()}`"
    if name == "pre":
        lang = ""
        code = node.find("code")
        if code and code.get("class"):
            m = re.search(r"language-(\w+)", " ".join(code["class"]))
            lang = m.group(1) if m else ""
        return f"\n\n```{lang}\n{node.get_text().rstrip()}\n```\n\n"
    if name == "blockquote":
        inner = kids().strip()
        quoted = "\n".join(f"> {l}" for l in inner.splitlines() if l.strip())
        return f"\n\n{quoted}\n\n"
    if name == "a":
        t = kids().strip() or node.get("href", "")
        return f"[{t}]({node.get('href', '')})" if node.get("href") else t
    if name == "img":
        return f"\n\n![{node.get('alt', '')}]({node.get('src', '')})\n\n"
    if name in ("ul", "ol"):
        ctx["list"].append(name)
        out = "\n\n" + kids().rstrip() + "\n\n"
        ctx["list"].pop()
        return out
    if name == "li":
        depth = max(len(ctx["list"]) - 1, 0)
        marker = "1." if (ctx["list"] and ctx["list"][-1] == "ol") else "-"
        return f"{'  ' * depth}{marker} {kids().strip()}\n"
    if name == "figcaption":
        t = kids().strip()
        return f"\n*{t}*\n" if t else ""
    if name in ("table",):
        rows = []
        for tr in node.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            rows.insert(1, "|" + "---|" * (rows[0].count("|") - 1))
        return "\n\n" + "\n".join(rows) + "\n\n"
    return kids()


def convert(root):
    try:
        from markdownify import MarkdownConverter

        return MarkdownConverter(heading_style="ATX", bullets="-").convert_soup(root)
    except ImportError:
        print("note: markdownify unavailable, using built-in converter", file=sys.stderr)
        return _fb(root)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html")
    ap.add_argument(
        "--out", default=None, help="default: page.md next to the html; '-' writes markdown to stdout instead of a file"
    )
    ap.add_argument("--selector", default=None)
    ap.add_argument(
        "--drop-selector",
        dest="drop_selectors",
        action="append",
        default=[],
        metavar="CSS",
        help="remove matching nodes before conversion (repeatable) — site nav, CTAs, disclaimers",
    )
    ap.add_argument(
        "--title-selector",
        dest="title_selector",
        default=None,
        metavar="CSS",
        help="venue's own title rule, when <title>/h1 is wrong",
    )
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args()

    html_path = Path(args.html)
    soup = BeautifulSoup(html_path.read_text(errors="replace"), "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    root = pick_root(soup, args.selector)
    dropped = drop_selectors(root, args.drop_selectors)
    picked_title = pick_title(soup, root, args.title_selector)
    root = clean(root, args.base_url)
    md = convert(root)
    md = SENTINEL_RE.sub(lambda m: f"<!-- media:{m.group(1)}:{m.group(2)} -->", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    # An explicit --title-selector is the venue saying the generic rule is
    # wrong here, so it OUTRANKS the `md.startswith("#")` guard: the heading
    # already in the body is exactly what was wrong on the pages that need
    # this flag.
    if picked_title:
        # The selected node is usually the body's own first heading (a venue
        # whose title lives in an h2 is the common case), so promoting it
        # would otherwise leave the same text twice — once as the note title
        # and once as its first section. Drop a leading heading of ANY level
        # whose text matches.
        md = re.sub(r"\A#{1,6}\s+" + re.escape(picked_title) + r"\s*\n+", "", md)
        md = f"# {picked_title}\n\n{md}"
    elif title and not md.startswith("#"):
        md = f"# {title}\n\n{md}"

    # Two modes, never both: '-' streams markdown to stdout; anything else
    # writes the file and keeps stdout empty. The status line is stderr so a
    # caller that redirects stdout over the written path cannot clobber the
    # bytes this script just wrote there.
    if args.out == "-":
        sys.stdout.write(md)
    else:
        out = Path(args.out) if args.out else html_path.with_name("page.md")
        out.write_text(md)
        print(
            f"{out}: {len(md)} chars, {md.count(chr(10))} lines" + (f", {dropped} node(s) dropped" if dropped else ""),
            file=sys.stderr,
        )
    if len(md) < 400:
        # Not a failure: the output IS written. Some pages are legitimately
        # short (a video lesson whose body is a player). Warn, exit 0.
        print("warning: output is suspiciously short — wrong content root? try --selector", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
