#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build a complete, well-formed note from a YouTube capture — deterministic,
so every YouTube note is consistent instead of hand-assembled (which flattened
transcripts into one paragraph and dumped raw descriptions).

  youtube_note.py <wiki> --capture-dir <dir> --notes-dir <dir> [--force]

Reads the capture's metadata.json (yt-dlp --dump-json) + transcript.<lang>
subtitle file (.vtt or .srt — whatever yt-dlp fetched) and
writes <notes-dir>/youtube.com/pages/<slug>.md with:
- rich frontmatter (channel, date, duration, views, likes, video id, thumbnail,
  source_host, areas, tags) — video metadata belongs in frontmatter, not prose
- a thumbnail + embed
- the description converted to markdown (bare URLs linkified, the creator's
  own TIMESTAMPS block turned into a list, hashtag soup collapsed)
- the transcript as timestamped, chapter-headed sections, noise-stripped and
  de-duplicated

The [!summary] is left as a placeholder for the process worker to fill.

This script belongs to the `channel-youtube` skill unit and is WIKI-OWNED: it
ships in the bundled catalog, `skills install` copies it, and the wiki's copy is
the one that runs. It is deliberately stdlib-only and imports nothing from the
plugin — a unit that imported plugin modules would couple itself to a layout it
does not control.

Transcript formatting is the one thing it does not do itself: cue parsing,
rolling-caption dedup and chapter bucketing are generic across video venues, so
they stay ABI machinery in the plugin. This script reaches them through the
wiki's own front door —

    <wiki>/<ops dir>/bin/llm-wiki-ops run scripts/format_transcript.py <captions> \
        --chapters <metadata.json> --interval 60

whose stdout is the markdown. `--format-transcript <path>` skips the shim and
runs a known path directly (tests, and any caller that already has one).
A non-zero status from either form ABORTS: a note that silently ships without
its transcript, exit 0, reporting success, is the exact failure this script
exists to prevent.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _shim_rel():
    """The wiki-relative shim path, from the tree name the front door
    exported (`LLM_WIKI_OPS_DIRNAME`). None outside `llm-wiki-ops run` — the
    caller aborts with the front-door hint rather than guessing a tree."""
    dirname = os.environ.get("LLM_WIKI_OPS_DIRNAME") or None
    if dirname is None:
        return None
    return Path(dirname) / "bin" / "llm-wiki-ops"


FORMATTER = "scripts/format_transcript.py"

URL_RE = re.compile(r"(?<![\(\]])\bhttps?://[^\s)]+")
TS_LINE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—:]?\s*(.+)$")
HASHTAGS = re.compile(r"(?:(?:^|\s)#[\w]+){2,}\s*$")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def source_hosts_for(host: str) -> list:
    """`www.a.com` -> `["www.a.com", "a.com"]` — progressively broader hosts so
    a query for either matches. No Public Suffix List; `co.uk` as a trailing
    entry is a correct host and a useless filter value, which is the cheaper
    trade. Duplicated from the pipeline's scaffold.py on purpose: a skill unit
    runs as a hosted script (the front door's `run` verb) and cannot import
    plugin/scripts/."""
    if not host:
        return []
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(max(1, len(labels) - 1))]


def normalize_tags(tags):
    """Lowercase-kebab tag vocabulary, order-preserving dedupe. Duplicated
    from the pipeline's scaffold.py for the same reason as source_hosts_for:
    a unit script cannot import plugin/scripts/. Everything outside
    [a-z0-9/] folds to '-' (Obsidian's tag charset; dots render as invalid
    tags there). keep-in-sync: scaffold.py / watch.py normalize_tags."""
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
    """The plugin's formatter, through the wiki's front door. Returns markdown.

    Aborts on failure rather than returning empty: the caller writes the note
    either way, so a swallowed error here ships a transcript-less note that
    reports success."""
    if override:
        cmd = [sys.executable, str(Path(override).resolve())]
    else:
        rel = _shim_rel()
        if rel is None:
            sys.exit(
                "youtube_note: LLM_WIKI_OPS_DIRNAME is not set — run "
                "this through the wiki's front door (`llm-wiki-ops "
                "run …`), which exports it"
            )
        shim = (wiki / rel).resolve()
        if not shim.is_file():
            sys.exit(
                f"youtube_note: {shim} is missing — the wiki's front door "
                "is how this unit reaches the plugin's transcript "
                "formatter; run /llm-wiki:init to restore the shim"
            )
        cmd = [str(shim), "run", FORMATTER]
    cmd += [str(Path(captions).resolve()), "--interval", "60"]
    if chapters_json:
        cmd += ["--chapters", str(Path(chapters_json).resolve())]
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode != 0 or not cp.stdout.strip():
        sys.exit(
            "youtube_note: transcript formatting failed "
            f"(exit {cp.returncode}) — refusing to write a note with no "
            f"transcript.\n{(cp.stderr or '').strip()[-500:]}"
        )
    return cp.stdout


def slugify(text, fallback):
    s = SLUG_RE.sub("-", (text or "").lower()).strip("-")[:70].rstrip("-")
    return s or fallback


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wiki", type=Path)
    ap.add_argument("--capture-dir", required=True)
    ap.add_argument(
        "--notes-dir",
        default=None,
        help="wiki-relative bundle root. Defaults to the `dest` "
        "the host wrote onto capture.json — the watch's own "
        "dirs.sources. Pass it only for a hand-run capture "
        "with no job behind it",
    )
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--format-transcript",
        default=None,
        metavar="PATH",
        help="run this format_transcript.py directly instead of reaching the plugin's copy through the wiki shim",
    )
    args = ap.parse_args()

    cap_dir = args.wiki / args.capture_dir
    meta = json.loads((cap_dir / "metadata.json").read_text())
    cap = {}
    if (cap_dir / "capture.json").exists():
        cap = json.loads((cap_dir / "capture.json").read_text())

    # `--dest`-or-`capture.json` is `scaffold.py`'s rule, and this is the
    # same question: the HOST decides where a watch's notes land and writes
    # it onto the capture, so a unit reads it and never guesses. No third
    # fallback — a capture with neither is one `job_from_watch` never
    # produced, and a guessed bundle would silently disagree with wherever
    # the watch's notes actually go.
    notes_rel = args.notes_dir or cap.get("dest")
    if not notes_rel:
        sys.exit(
            f"{cap_dir / 'capture.json'} carries no `dest` and "
            f"--notes-dir was not given — every job-driven capture has "
            f"one (dirs.sources is required on every watch); pass "
            f"--notes-dir explicitly for a hand-run capture"
        )
    notes_dir = Path(notes_rel)
    title = meta.get("title") or "Untitled"
    slug = slugify(title, cap_dir.name.rsplit("--", 1)[0])
    # `<dest>/pages/<slug>.md`, with NO component composed under the bundle
    # root. The capture dir's parent is the watch's slug, which is already
    # what `dest` is named for, so inserting it buries every note a level
    # deeper under a duplicate. `scaffold.py` writes the same shape for the
    # same captures, and two builders disagreeing about where a note lands
    # is exactly what its own comment warns against.
    note_rel = notes_dir / "pages" / f"{slug}.md"
    note_path = args.wiki / note_rel
    if note_path.exists() and not args.force:
        sys.exit(f"{note_rel} exists — use --force")
    note_path.parent.mkdir(parents=True, exist_ok=True)

    vid = meta.get("id", "")
    source_host = source_hosts_for("www.youtube.com")
    areas = [f'"[[{a}]]"' for a in (cap.get("areas") or []) if a]
    tags = normalize_tags(cap.get("tags") or [])
    dur = meta.get("duration_string") or (mmss(meta["duration"]) if meta.get("duration") else "")

    # Protocol-well-known and OPTIONAL, so it is emitted only when the upload
    # date is actually known — never as an empty key, which would read as a
    # malformed date to lint and as absent to everything else.
    published = yt_date(meta.get("upload_date"))

    fm = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f"resource: {meta.get('webpage_url') or meta.get('original_url')}",
        "type: video",
        f"channel: {meta.get('uploader') or meta.get('channel') or ''}",
        f"channel_url: {meta.get('channel_url') or ''}",
        *([f"published: {published}"] if published else []),
        f"duration: {dur}",
        f"views: {meta.get('view_count') or ''}",
        f"likes: {meta.get('like_count') or ''}",
        f"video_id: {vid}",
        f"thumbnail: {meta.get('thumbnail') or ''}",
        f"source_host: [{', '.join(source_host)}]",
        f"areas: [{', '.join(areas)}]",
        f"tags: [{', '.join(tags)}]",
        "status: draft",
        "---",
    ]

    parts = ["\n".join(fm), ""]
    parts.append("> [!summary]\n> TODO-SUMMARY — 2-4 information-dense sentences before completing the handoff.\n")

    if meta.get("thumbnail"):
        parts.append(f"![thumbnail]({meta['thumbnail']})\n")
    if vid:
        parts.append(
            f'<iframe width="560" height="315" '
            f'src="https://www.youtube.com/embed/{vid}" '
            f'title="{title.replace(chr(34), " ")}" frameborder="0" '
            f'allowfullscreen></iframe>\n'
        )

    desc = description_to_md(meta.get("description", ""))
    if desc:
        parts.append("## Description\n\n" + desc + "\n")

    # transcript — yt-dlp writes whatever sub format it fetched (suffix is
    # not provenance; .vtt is its common default, .srt still appears)
    # Harvest files them under `captions/`; a bare capture dir is also honored.
    # Prefer a non-`-orig` track: YouTube ships `<id>.en.vtt` alongside
    # `<id>.en-orig.vtt`, and the plain one is the corrected caption track.
    srt = next(
        (
            p
            for pat in (
                "captions/transcript*.vtt",
                "captions/transcript*.srt",
                "captions/*.vtt",
                "captions/*.srt",
                "transcript*.vtt",
                "transcript*.srt",
                "*.vtt",
                "*.srt",
            )
            for p in sorted(cap_dir.glob(pat), key=lambda q: ("-orig" in q.stem, q.name))
        ),
        None,
    )
    if srt:
        tx = format_transcript(
            srt, (cap_dir / "metadata.json") if meta.get("chapters") else None, args.wiki, args.format_transcript
        )
        parts.append(
            "## Transcript\n\n*Auto-generated captions, cleaned "
            "(sound tags removed, rolling overlap de-duplicated) and "
            "split by chapter. Not manually corrected.*\n\n" + tx
        )

    note_path.write_text("\n".join(parts).rstrip() + "\n")
    print(
        json.dumps(
            {
                "note": str(note_rel),
                "has_transcript": bool(srt),
                "chapters": len(meta.get("chapters") or []),
                "description": bool(desc),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
