#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render a YouTube capture into the page body and capture record the generic
extractor reads — deterministic, so every YouTube page is consistent instead of
hand-assembled (which flattened transcripts into one paragraph and dumped raw
descriptions).

  youtube_note.py <wiki> --capture-dir <dir> [--item <url>] [--slug <slug>]
                  [--tag <tag>]... [--area <area>]... [--format-transcript <path>]

Run at HARVEST time, because harvest is the only stage that reaches a unit: every
process ticket goes to the host's generic extractor, which takes a `.md` body
verbatim and writes the page under the job's `dest` with its own frontmatter
(`title`, `status`, `resource`, `harvested`). A harvest slice cannot write
`dest`, so this script writes nothing there.

Reads, in the capture dir: `metadata.json` (`yt-dlp --dump-json`), the subtitle
file yt-dlp fetched (.vtt or .srt, under `captions/` or beside the metadata),
and `ticket.json` when the spawner left one (`slug`, `item`). `--slug`/`--item`
override it. A directory with NO `ticket.json` is refused unless `--item` says
what it holds (a hand run): `run` starts this script at the wiki root, so a
mistyped `--capture-dir` is otherwise a page built from the wrong directory.
The slug then defaults to the capture dir's parent (`_raw/<slug>/<leaf>`).

FIRST, before it reads anything, it removes what an earlier run left in the
capture dir — `capture.json`, `page.md`, `report.json` — because a capture dir
is stable across pulls and a respawn that fails must not be read as the
success the run before it had.

Writes, in the capture dir and nowhere else:
- `page.md` — BODY ONLY, never a `---` block (the extractor prepends its own
  and a second one corrupts the page): the video's TRUE title as the `# H1`,
  thumbnail, embed, a compact facts list, the description as a blockquote
  (bare URLs linkified, the creator's own TIMESTAMPS block turned into a list,
  hashtag soup collapsed), and the transcript as timestamped, chapter-headed
  sections, noise-stripped and de-duplicated. No summary placeholder: the
  summary is the host's process side's to write, not a harvest worker's.
- `capture.json` — `slug`, `item`, `title`, `body: "page.md"`,
  `content_type: "text/markdown"`, `fetched_at`, and a `frontmatter` object
  carrying the video's exact facts (type, channel, channel_url, published,
  duration, views, likes, video_id, thumbnail, source_host, source_title, tags,
  areas). `title` is `safe_title(<the video's title>)` — the page's FILE is
  named from it; `frontmatter.source_title` is the true one, when they differ.
  Unknown facts are omitted, never emitted empty. It never carries `title`,
  `resource`, `status` or any other key a host verb owns. The extractor ignores
  `frontmatter` today, which is why the same facts are also in the body.

Everything yt-dlp returns is the venue's text, and `page.md` is the FINAL page
body, taken verbatim. So: the title and every fact value are folded to one
line; the id goes into the embed's `src` only when it is shaped like one; a url
goes into a link only when it is a clean http(s) one; attribute text is
HTML-escaped; and the description is blockquoted line by line, so nothing in it
can open a fence or a heading that swallows the rest of the page.

`report.json` is NOT this script's: `write_report.py` beside it writes that,
last.

This script belongs to the `channel-youtube` skill unit and is WIKI-OWNED: it
ships in the catalog, `skills install` copies it, and the wiki's copy is the one
that runs. It is deliberately stdlib-only and imports nothing from the plugin —
a unit that imported plugin modules would couple itself to a layout it does not
control.

Transcript formatting is the one thing it does not do itself: cue parsing,
rolling-caption dedup and chapter bucketing are generic across video venues, so
they stay ABI machinery in the plugin. This script reaches them through the
front door, by its bare name on PATH, run from the wiki root —

    llm-wiki-ops run skills/process/scripts/format_transcript.py <captions> \
        --chapters <metadata.json> --interval 60

whose stdout is the markdown. `--format-transcript <path>` skips the front door
and runs a known path directly (tests, and any caller that already has one).
A non-zero status from either form ABORTS before anything is written: a page
that silently ships without its transcript, exit 0, reporting success, is the
exact failure this script exists to prevent.
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The front door, by the bare name every SKILL.md already runs this script
# under — never a path into the wiki, which stops carrying a shim.
OPS = "llm-wiki-ops"

# What a nested front-door call must NOT inherit from the one that ran this
# script. Both names belong to the MACHINE-GLOBAL bash dispatcher that is the
# bare `llm-wiki-ops` on PATH (the llm-wiki-global plugin's
# `plugin/scripts/llm-wiki-ops`) — not to the versioned CLI package, where a
# search for either finds nothing. That dispatcher exports
# `LLM_WIKI_OPS_DISPATCHED=1` before it execs the wiki's shim and refuses
# (127) any call that arrives carrying it, as a loop. The guard is still set in
# here — this script is the dispatcher's grandchild — and this call is not a
# loop. And the dispatcher seeds its walk for the wiki root from
# `$CLAUDE_PROJECT_DIR`, when that names a wiki, AHEAD of the cwd, so without
# dropping it `cwd=<root>` would not be what picks the wiki.
NOT_INHERITED = ("LLM_WIKI_OPS_DISPATCHED", "CLAUDE_PROJECT_DIR")

# An address `run` serves out of the plugin, not a path in this wiki.
FORMATTER = "skills/process/scripts/format_transcript.py"


def _front_door_env():
    return {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}

URL_RE = re.compile(r"(?<![\(\]])\bhttps?://[^\s)<>]+")
TS_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—:]?\s*(.+)$")
HASHTAGS = re.compile(r"(?:(?:^|\s)#[\w]+){2,}\s*$")


# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "'", "<": "(", ">": ")"}
TITLE_MAX = 120   # characters
# …and a filename is capped in BYTES by the filesystem (255 on ext4/APFS), with `.md` appended:
# 120 characters of CJK is 360 bytes, which `filename_for` lets through and the write then fails on.
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


def fold(text):
    """Venue text as ONE line: newlines, tabs and control characters become a
    space. What every title and fact value passes through before it reaches
    `page.md` or `capture.json`, so none of them can start a line of its own."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())


# Inline markup venue prose must not get to write: raw HTML, a wikilink or
# `![[embed]]` (a graph edge, or another page transcluded, that nobody here
# wrote), and the two Obsidian spans that hide everything after them when left
# unclosed (`%%` comment, `$$` math). Escaped, so each still READS as typed.
_INLINE = (("<", "&lt;"), ("[[", "\\[\\["), ("%%", "\\%\\%"), ("$$", "\\$\\$"))
# A markdown link whose target is not http(s) — `[x](javascript:…)`.
_BAD_LINK = re.compile(r"\]\((?!https?://)")
# What would open a block at the start of a line: an ATX heading, a code fence,
# a nested quote, a callout, a setext underline / rule made of `=` or `-`.
_LEADING = re.compile(r"^\s*(?:#{1,6}(?=\s|$)|```|~~~|>|\[!|(?:=+|-+)\s*$)")
# A clean http(s) url: nothing that ends a markdown link, an autolink or an
# HTML attribute early. YouTube's own urls never carry any of these.
_URL_OK = re.compile(r"^https?://[^\s<>\"'()\[\]\\`]+$")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_DURATION = re.compile(r"^\d{1,4}(?::\d{2}){0,2}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def plain(text, brackets=False):
    """One run of venue prose with its inline markup neutralised. `brackets`
    also escapes `[` and `]`, for text that is going INSIDE a link's label."""
    text = str(text or "")
    if brackets:
        text = text.replace("[", "\\[").replace("]", "\\]")
    for bad, good in _INLINE:
        text = text.replace(bad, good)
    return _BAD_LINK.sub(r"]\\(", text)


def unblocked(line):
    """A line that cannot open a block: a leading heading / fence / quote /
    callout / underline marker is backslash-escaped, so it reads as typed."""
    return "\\" + line.lstrip() if _LEADING.match(line) else line


def clean_url(value):
    """`value` when it is a clean http(s) url, else None — what may go into a
    link, an image or `frontmatter`."""
    return value if isinstance(value, str) and _URL_OK.match(value) else None


def video_id_of(meta):
    """The id, only when it is shaped like one: it is interpolated into the
    embed's `src`, and `x" onload="…` is a metadata value like any other."""
    vid = meta.get("id")
    return vid if isinstance(vid, str) and VIDEO_ID.match(vid) else None


def count_of(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def published_of(meta):
    """`yt_date`'s answer, kept only when it is a day that exists."""
    raw = meta.get("upload_date")
    day = yt_date(raw) if isinstance(raw, str) else ""
    if not _DAY.match(day):
        return ""
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return ""
    return day


def source_hosts_for(host: str) -> list:
    """`www.a.com` -> `["www.a.com", "a.com"]` — progressively broader hosts so
    a query for either matches. No Public Suffix List; `co.uk` as a trailing
    entry is a correct host and a useless filter value, which is the cheaper
    trade. Carried here rather than imported: a skill unit runs as a hosted
    script (the front door's `run` verb) and imports nothing from the plugin."""
    if not host:
        return []
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(max(1, len(labels) - 1))]


def normalize_tags(tags):
    """Lowercase-kebab tag vocabulary, order-preserving dedupe. Carried here
    for the same reason as source_hosts_for: a unit script imports nothing
    from the plugin. Everything outside [a-z0-9/] folds to '-' (Obsidian's
    tag charset; dots render as invalid tags there)."""
    out = []
    for tag in tags:
        tag = re.sub(r"[^a-z0-9/]+", "-", str(tag).strip().lower())
        tag = re.sub(r"-*/+-*", "/", tag)
        tag = re.sub(r"-{2,}", "-", tag).strip("-/")
        if tag and tag not in out:
            out.append(tag)
    return out


def mmss(sec):
    """Seconds -> m:ss / h:mm:ss. Inlined from the plugin's formatter: six
    lines is cheaper than a subprocess, and the shape is frozen."""
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_transcript(captions, chapters_json, wiki, override):
    """The plugin's formatter, through the front door. Returns markdown.

    Aborts on failure rather than returning empty: the caller writes the page
    either way, so a swallowed error here ships a transcript-less page that
    reports success."""
    if override:
        cmd, where = [sys.executable, str(Path(override).resolve())], {}
    else:
        ops = shutil.which(OPS)
        if ops is None:
            sys.exit(
                f"youtube_note: `{OPS}` is not on PATH — the front door is "
                "how this unit reaches the plugin's transcript formatter; "
                "install the ops plugin on this machine"
            )
        # The wiki root is what binds the front door to THIS wiki.
        cmd, where = [ops, "run", FORMATTER], {"cwd": str(wiki), "env": _front_door_env()}
    cmd += [str(Path(captions).resolve()), "--interval", "60"]
    if chapters_json:
        cmd += ["--chapters", str(Path(chapters_json).resolve())]
    cp = subprocess.run(cmd, capture_output=True, text=True, **where)
    if cp.returncode != 0 or not cp.stdout.strip():
        sys.exit(
            "youtube_note: transcript formatting failed "
            f"(exit {cp.returncode}) — refusing to write a note with no "
            f"transcript.\n{(cp.stderr or '').strip()[-500:]}"
        )
    return cp.stdout


def yt_date(d):
    """yt-dlp's `upload_date` (`YYYYMMDD`) as the protocol's `published`
    shape, or `""` when there is nothing to convert.

    Anything that is not exactly 8 digits yields nothing rather than being
    passed through: `published` is a protocol-well-known field now, day
    precision or absent, and an odd-shaped value reads as absent at every
    consumer while still looking authoritative in the note."""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and d.isdigit() and len(d) == 8 else ""


def _linkified(line):
    """Bare urls as autolinks, the prose between them neutralised."""
    out, at = [], 0
    for mo in URL_RE.finditer(line):
        out += [plain(line[at:mo.start()]), f"<{mo.group(0)}>"]
        at = mo.end()
    return "".join(out) + plain(line[at:])


def description_to_md(desc):
    """Linkify bare URLs, turn a TIMESTAMPS block into a list, collapse the
    trailing hashtag pile. Keep the creator's own words otherwise.

    The words are the VENUE's and the page is taken verbatim, so the result is
    a BLOCKQUOTE, every line of it: a line that starts with `> ` cannot be a
    top-level heading, rule or frontmatter fence, and a code fence opened in a
    quote ends with the quote — so nothing in a description swallows the
    transcript below it. Inside the quote the same markers are escaped as well
    (`unblocked`, `plain`), so a stray fence does not swallow the rest of the
    DESCRIPTION either, and the words read as typed."""
    if not isinstance(desc, str) or not desc.strip():
        return ""
    lines = re.split(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", desc)
    out, in_ts = [], False
    for raw in lines:
        line = "".join(ch if ch.isprintable() else " " for ch in raw).rstrip()   # tabs, control characters
        if re.match(r"^\s*(timestamps|chapters)\s*:?\s*$", line, re.I):
            out.append("**Timestamps**")
            out.append("")
            in_ts = True
            continue
        m = TS_LINE.match(line)
        if in_ts and m:
            out.append(f"- `{m.group(1)}` {plain(m.group(2).strip())}")
            continue
        if in_ts and not line.strip():
            in_ts = False
            out.append("")
            continue
        if in_ts:
            in_ts = False
            out.append("")  # separate the list from the prose that follows
        in_ts = False
        # collapse a trailing wall of hashtags
        if HASHTAGS.search(line) and len(line.split()) > 3:
            continue
        out.append(unblocked(_linkified(line)))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.split("\n"))


# The plugin formatter's own section heads: `#### [mm:ss] Chapter title`.
_SECTION_HEAD = re.compile(r"^#{1,6} \[\d")


def guard_transcript(md):
    """The formatter's markdown with caption TEXT kept from opening a block:
    a manual caption track is the creator's typing, and a cue that is a code
    fence would swallow the rest of the transcript. The formatter's own section
    heads are left alone."""
    return "\n".join(line if _SECTION_HEAD.match(line) else unblocked(line) for line in md.split("\n"))


def safe_chapters(meta):
    """The chapter list with each title folded to one line and neutralised —
    the plugin formatter writes a title straight into a `####` heading — or
    `[]`. Rows without a numeric start are dropped."""
    rows = []
    for ch in meta.get("chapters") or []:
        if not isinstance(ch, dict) or not isinstance(ch.get("start_time"), (int, float)):
            continue
        row = {"start_time": ch["start_time"], "title": plain(fold(ch.get("title")))}
        if isinstance(ch.get("end_time"), (int, float)):
            row["end_time"] = ch["end_time"]
        rows.append(row)
    return rows


TICKET_NAME = "ticket.json"
CAPTURE_NAME = "capture.json"
BODY_NAME = "page.md"
REPORT_NAME = "report.json"
# Written for the formatter's one call and removed after it: `metadata.json`'s
# chapters with their titles made safe to print (`safe_chapters`).
CHAPTERS_NAME = "chapters.safe.json"
# What an earlier run over this SAME directory may have left. Removed first.
STALE = (CAPTURE_NAME, BODY_NAME, REPORT_NAME, CHAPTERS_NAME)

# Keys a host verb owns on the page. `frontmatter` never carries one: the
# extractor writes `title`, `status`, `resource` and `harvested` itself, and
# identity and the `extracted` flag are minted outside any slice.
HOST_OWNED = ("status", "document_id", "document_revision", "harvested", "extracted", "title", "resource")

# Tried in order. yt-dlp writes whatever sub format it fetched (suffix is not
# provenance; .vtt is its common default, .srt still appears). Captions are
# filed under `captions/`; a bare capture dir is also honored.
CAPTION_GLOBS = (
    "captions/transcript*.vtt",
    "captions/transcript*.srt",
    "captions/*.vtt",
    "captions/*.srt",
    "transcript*.vtt",
    "transcript*.srt",
    "*.vtt",
    "*.srt",
)


def read_ticket(cap_dir):
    """`ticket.json` as the spawner wrote it, or `{}` — a hand run has none."""
    try:
        ticket = json.loads((Path(cap_dir) / TICKET_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return ticket if isinstance(ticket, dict) else {}


def find_captions(cap_dir):
    """The caption track to format, or None. Prefers a non-`-orig` track:
    YouTube ships `<id>.en.vtt` alongside `<id>.en-orig.vtt`, and the plain one
    is the corrected caption track."""
    return next(
        (
            p
            for pat in CAPTION_GLOBS
            for p in sorted(Path(cap_dir).glob(pat), key=lambda q: ("-orig" in q.stem, q.name))
        ),
        None,
    )


def duration_of(meta):
    given = meta.get("duration_string")
    if isinstance(given, str) and _DURATION.match(given):
        return given
    seconds = meta.get("duration")
    return mmss(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0 else ""


def true_title(meta):
    """The video's own title, folded to one line — or `""`."""
    return fold(meta.get("title")) if isinstance(meta.get("title"), str) else ""


def page_title(meta):
    """`capture.json`'s `title`: the true one made a legal filename."""
    vid = video_id_of(meta)
    return safe_title(true_title(meta), fallback=f"YouTube video {vid}" if vid else "Untitled video")


def frontmatter_for(meta, tags=(), areas=()):
    """The video's exact facts, as the `frontmatter` object of `capture.json`:
    scalars and flat lists only, unknown facts OMITTED.

    `published` is protocol-well-known and optional, so it is emitted only when
    the upload date is actually known — never as an empty key, which would read
    as a malformed date to lint and as absent to everything else. The same rule
    is applied to every other fact here — and a fact whose value is not
    shaped like one (an id that is not an id, a url that is not a clean
    http(s) url, a count that is not a number) is unknown, not passed through.

    `source_title` is the video's TRUE title, present only when the filename
    rule made `capture.json`'s `title` differ from it."""
    channel = meta.get("uploader") or meta.get("channel")
    title = true_title(meta)
    facts = {
        "type": "video",
        "channel": fold(channel) if isinstance(channel, str) else None,
        "channel_url": clean_url(meta.get("channel_url")),
        "published": published_of(meta),
        "duration": duration_of(meta),
        "views": count_of(meta.get("view_count")),
        "likes": count_of(meta.get("like_count")),
        "video_id": video_id_of(meta),
        "thumbnail": clean_url(meta.get("thumbnail")),
        "source_title": title if title and title != page_title(meta) else None,
        "source_host": source_hosts_for("www.youtube.com"),
        "tags": normalize_tags(tags),
        "areas": [f"[[{a}]]" for a in areas if a],
    }
    return {k: v for k, v in facts.items() if v not in (None, "", []) and k not in HOST_OWNED}


def facts_block(front, item):
    """The same facts as a compact list for the page body — the extractor
    ignores `frontmatter` today, so this is what keeps them on the page."""
    channel = plain(front.get("channel"), brackets=True)
    if channel and front.get("channel_url"):
        channel = f"[{channel}]({front['channel_url']})"
    item = clean_url(item)
    counts = " · ".join(
        f"**{label}**: {front[key]}" for label, key in (("Views", "views"), ("Likes", "likes")) if key in front
    )
    rows = [
        f"**Channel**: {channel}" if channel else "",
        f"**Published**: {front['published']}" if front.get("published") else "",
        f"**Duration**: {front['duration']}" if front.get("duration") else "",
        counts,
        f"**Video ID**: `{front['video_id']}`" if front.get("video_id") else "",
        f"**Source**: <{item}>" if item else "",
    ]
    lines = [f"- {row}" for row in rows if row]
    return "\n".join(lines)


def build_body(meta, front, item, transcript_md):
    """`page.md`: body only. It opens with the `# H1` — the video's TRUE title,
    which the filename rule may have kept out of `capture.json`'s — and every
    block after it opens with markup of its own, so the body can never open
    with a `---` line. `front` is where the validated id and urls come from:
    nothing is interpolated into the embed or a link straight off `meta`."""
    title = true_title(meta) or page_title(meta)
    vid = front.get("video_id")
    parts = [f"# {plain(title)}\n"]
    if front.get("thumbnail"):
        parts.append(f"![thumbnail]({front['thumbnail']})\n")
    if vid:
        parts.append(
            f'<iframe width="560" height="315" '
            f'src="https://www.youtube.com/embed/{vid}" '
            f'title="{html.escape(title, quote=True)}" frameborder="0" '
            f'allowfullscreen></iframe>\n'
        )
    facts = facts_block(front, item)
    if facts:
        parts.append(facts + "\n")
    desc = description_to_md(meta.get("description", ""))
    if desc:
        parts.append("## Description\n\n" + desc + "\n")
    if transcript_md:
        parts.append(
            "## Transcript\n\n*Auto-generated captions, cleaned "
            "(sound tags removed, rolling overlap de-duplicated) and "
            "split by chapter. Not manually corrected.*\n\n" + guard_transcript(transcript_md)
        )
    return "\n".join(parts).rstrip() + "\n", bool(desc)


def fetched_at_of(metadata_path):
    """When yt-dlp wrote `metadata.json` — that IS when the item was fetched,
    and it keeps a re-run over the same capture byte-identical."""
    stamp = datetime.fromtimestamp(Path(metadata_path).stat().st_mtime, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wiki", type=Path, help="the wiki root — what binds the front door to this wiki")
    ap.add_argument("--capture-dir", required=True, help="wiki-relative capture dir: `capture_dir` off ticket.json")
    ap.add_argument("--item", default=None, help="the video url. Defaults to ticket.json's `item`; REQUIRED where there is no ticket.json (a hand run)")
    ap.add_argument("--slug", default=None, help="the job slug. Defaults to ticket.json's `slug`, then the capture dir's parent")
    ap.add_argument("--tag", action="append", default=[], help="a tag for `frontmatter.tags` (repeatable) — a hand run's; a ticket carries none")
    ap.add_argument("--area", action="append", default=[], help="a knowledge area for `frontmatter.areas` (repeatable) — a hand run's; a ticket carries none")
    ap.add_argument(
        "--format-transcript",
        default=None,
        metavar="PATH",
        help="run this format_transcript.py directly instead of reaching the plugin's copy through the front door "
             "(tests, hand runs). The one path here NOT read against the capture dir: absolute, or relative to the "
             "cwd — which under `llm-wiki-ops run` is the wiki root",
    )
    args = ap.parse_args()

    given = Path(args.capture_dir)
    if given.is_absolute() or ".." in given.parts:
        sys.exit(f"youtube_note: --capture-dir is WIKI-RELATIVE (ticket.json's `capture_dir`, verbatim), not {args.capture_dir!r}")
    cap_dir = args.wiki / given
    if not cap_dir.is_dir():
        sys.exit(f"youtube_note: {cap_dir} is not a directory — --capture-dir is relative to the wiki root, {args.wiki}")
    ticket = read_ticket(cap_dir)
    if not ticket and not args.item:
        sys.exit(
            f"youtube_note: no {TICKET_NAME} in {cap_dir} — not a spawned capture directory. "
            "For a hand run say what it holds: --item <video url> (and --slug)"
        )

    # FIRST, before anything can fail: a capture dir is stable across pulls, so
    # what an earlier run left here must not outlive a build that fails.
    # `capture.json` is what says "this item landed", and `report.json` is what
    # `apply` reads — it does not check whose ticket a report answers.
    for name in STALE:
        (cap_dir / name).unlink(missing_ok=True)

    metadata_path = cap_dir / "metadata.json"
    if not metadata_path.is_file():
        sys.exit(f"youtube_note: {metadata_path} is not there — run `yt-dlp --dump-json --no-download <url>` into it first")
    # `yt-dlp … > metadata.json` leaves an EMPTY file when yt-dlp fails.
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(f"youtube_note: {metadata_path} is not yt-dlp's JSON ({exc}) — yt-dlp failed; nothing was captured")
    if not isinstance(meta, dict):
        sys.exit(f"youtube_note: {metadata_path} is not yt-dlp's JSON (not an object) — nothing was captured")

    slug = fold(args.slug or ticket.get("slug") or cap_dir.resolve().parent.name)
    item = fold(args.item or ticket.get("item")) or None
    front = frontmatter_for(meta, tags=args.tag, areas=args.area)

    captions = find_captions(cap_dir)
    transcript_md = ""
    if captions:
        chapters = safe_chapters(meta)
        chapters_path = cap_dir / CHAPTERS_NAME
        try:
            if chapters:
                chapters_path.write_text(json.dumps(chapters), encoding="utf-8")
            transcript_md = format_transcript(
                captions, chapters_path if chapters else None, args.wiki, args.format_transcript
            )
        finally:
            chapters_path.unlink(missing_ok=True)

    body, has_desc = build_body(meta, front, item, transcript_md)
    (cap_dir / BODY_NAME).write_text(body, encoding="utf-8")
    record = {
        "slug": slug,
        "item": item,
        "title": page_title(meta),
        "body": BODY_NAME,
        "content_type": "text/markdown",
        "fetched_at": fetched_at_of(metadata_path),
        "frontmatter": front,
    }
    # Written after the body it names, so a capture record never points at a
    # page that is not there.
    (cap_dir / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "page": f"{args.capture_dir.rstrip('/')}/{BODY_NAME}",
                "capture": f"{args.capture_dir.rstrip('/')}/{CAPTURE_NAME}",
                "has_transcript": bool(captions),
                "chapters": len(safe_chapters(meta)),
                "description": has_desc,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
