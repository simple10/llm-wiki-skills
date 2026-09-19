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
override it, and stand in for it on a hand run; with neither, the slug is the
capture dir's parent (`_raw/<slug>/<leaf>`) and the item is the metadata's
`webpage_url`.

Writes, in the capture dir and nowhere else:
- `page.md` — BODY ONLY, never a `---` block (the extractor prepends its own
  and a second one corrupts the page): thumbnail, embed, a compact facts list,
  the description converted to markdown (bare URLs linkified, the creator's own
  TIMESTAMPS block turned into a list, hashtag soup collapsed), and the
  transcript as timestamped, chapter-headed sections, noise-stripped and
  de-duplicated. No summary placeholder: the summary is the host's process
  side's to write, not a harvest worker's.
- `capture.json` — `slug`, `item`, `title`, `body: "page.md"`,
  `content_type: "text/markdown"`, `fetched_at`, and a `frontmatter` object
  carrying the video's exact facts (type, channel, channel_url, published,
  duration, views, likes, video_id, thumbnail, source_host, tags, areas).
  Unknown facts are omitted, never emitted empty. It never carries `title`,
  `resource`, `status` or any other key a host verb owns. The extractor ignores
  `frontmatter` today, which is why the same facts are also in the body.

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
# script. The re-entry guard is still set in here — this script is the front
# door's grandchild — and a call carrying it is refused (127) as a loop, which
# this is not. And the front door binds to `CLAUDE_PROJECT_DIR` AHEAD of the
# cwd, so without dropping it `cwd=<root>` would not be what picks the wiki.
NOT_INHERITED = ("LLM_WIKI_OPS_DISPATCHED", "CLAUDE_PROJECT_DIR")

# An address `run` serves out of the plugin, not a path in this wiki.
FORMATTER = "skills/process/scripts/format_transcript.py"


def _front_door_env():
    return {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}

URL_RE = re.compile(r"(?<![\(\]])\bhttps?://[^\s)]+")
TS_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—:]?\s*(.+)$")
HASHTAGS = re.compile(r"(?:(?:^|\s)#[\w]+){2,}\s*$")


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


def description_to_md(desc):
    """Linkify bare URLs, turn a TIMESTAMPS block into a list, collapse the
    trailing hashtag pile. Keep the creator's own words otherwise."""
    if not desc:
        return ""
    lines = desc.replace("\r\n", "\n").split("\n")
    out, in_ts = [], False
    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^\s*(timestamps|chapters)\s*:?\s*$", line, re.I):
            out.append("**Timestamps**")
            out.append("")
            in_ts = True
            continue
        m = TS_LINE.match(line)
        if in_ts and m:
            out.append(f"- `{m.group(1)}` {m.group(2).strip()}")
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
        line = URL_RE.sub(lambda mo: f"<{mo.group(0)}>", line)
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


TICKET_NAME = "ticket.json"
CAPTURE_NAME = "capture.json"
BODY_NAME = "page.md"

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
    return meta.get("duration_string") or (mmss(meta["duration"]) if meta.get("duration") else "")


def frontmatter_for(meta, tags=(), areas=()):
    """The video's exact facts, as the `frontmatter` object of `capture.json`:
    scalars and flat lists only, unknown facts OMITTED.

    `published` is protocol-well-known and optional, so it is emitted only when
    the upload date is actually known — never as an empty key, which would read
    as a malformed date to lint and as absent to everything else. The same rule
    is applied to every other fact here."""
    facts = {
        "type": "video",
        "channel": meta.get("uploader") or meta.get("channel"),
        "channel_url": meta.get("channel_url"),
        "published": yt_date(meta.get("upload_date")),
        "duration": duration_of(meta),
        "views": meta.get("view_count"),
        "likes": meta.get("like_count"),
        "video_id": meta.get("id"),
        "thumbnail": meta.get("thumbnail"),
        "source_host": source_hosts_for("www.youtube.com"),
        "tags": normalize_tags(tags),
        "areas": [f"[[{a}]]" for a in areas if a],
    }
    return {k: v for k, v in facts.items() if v not in (None, "", []) and k not in HOST_OWNED}


def facts_block(front, item):
    """The same facts as a compact list for the page body — the extractor
    ignores `frontmatter` today, so this is what keeps them on the page."""
    channel = front.get("channel")
    if channel and front.get("channel_url"):
        channel = f"[{channel}]({front['channel_url']})"
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
    """`page.md`: body only. Every block either opens with markup of its own or
    sits under a heading, so the body can never open with a `---` line."""
    title = meta.get("title") or "Untitled"
    vid = meta.get("id", "")
    parts = []
    if meta.get("thumbnail"):
        parts.append(f"![thumbnail]({meta['thumbnail']})\n")
    if vid:
        parts.append(
            f'<iframe width="560" height="315" '
            f'src="https://www.youtube.com/embed/{vid}" '
            f'title="{title.replace(chr(34), " ")}" frameborder="0" '
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
            "split by chapter. Not manually corrected.*\n\n" + transcript_md
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
    ap.add_argument("--item", default=None, help="the video url. Defaults to ticket.json's `item`, then the metadata's `webpage_url`")
    ap.add_argument("--slug", default=None, help="the job slug. Defaults to ticket.json's `slug`, then the capture dir's parent")
    ap.add_argument("--tag", action="append", default=[], help="a tag for `frontmatter.tags` (repeatable) — a hand run's; a ticket carries none")
    ap.add_argument("--area", action="append", default=[], help="a knowledge area for `frontmatter.areas` (repeatable) — a hand run's; a ticket carries none")
    ap.add_argument(
        "--format-transcript",
        default=None,
        metavar="PATH",
        help="run this format_transcript.py directly instead of reaching the plugin's copy through the front door",
    )
    args = ap.parse_args()

    cap_dir = args.wiki / args.capture_dir
    metadata_path = cap_dir / "metadata.json"
    if not metadata_path.is_file():
        sys.exit(f"youtube_note: {metadata_path} is not there — run `yt-dlp --dump-json --no-download <url>` into it first")
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    ticket = read_ticket(cap_dir)

    # A capture record left by an earlier run must not outlive a build that
    # fails: `capture.json` is what says "this item landed".
    (cap_dir / CAPTURE_NAME).unlink(missing_ok=True)

    slug = args.slug or ticket.get("slug") or cap_dir.resolve().parent.name
    item = args.item or ticket.get("item") or meta.get("webpage_url") or meta.get("original_url")
    front = frontmatter_for(meta, tags=args.tag, areas=args.area)

    captions = find_captions(cap_dir)
    transcript_md = ""
    if captions:
        transcript_md = format_transcript(
            captions, metadata_path if meta.get("chapters") else None, args.wiki, args.format_transcript
        )

    body, has_desc = build_body(meta, front, item, transcript_md)
    (cap_dir / BODY_NAME).write_text(body, encoding="utf-8")
    record = {
        "slug": slug,
        "item": item,
        "title": meta.get("title") or None,
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
                "chapters": len(meta.get("chapters") or []),
                "description": has_desc,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
