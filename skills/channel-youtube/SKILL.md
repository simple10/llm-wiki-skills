---
name: channel-youtube
description: YouTube capture and note building for this wiki — yt-dlp ground truth, deterministic notes.
argument-hint: --stage harvest|process --capture-dir <dir> [--url <url>]
user-invocable: false
---

# Channel: YouTube

You harvest YouTube videos for this wiki. You are normally dispatched by
the harvest worker loop: a claimed job carrying `skill: channel-youtube`
means its watch named this skill, and this file is authoritative for how
the venue is captured. The job already carries the resolved config — honor
it; never re-ask.

This copy is wiki-owned — body and `scripts/` both. Improve it as you learn
the venue; `skills list` reporting it as diverged is provenance, not a
problem. What is NOT yours to fix from here is the PLUGIN's own generic
machinery (the scaffolder, the transcript formatter, asset handling): claims
about those go to the human via the run report, never into this file.

**Dependency**: `yt-dlp` on PATH.

## Stages

You are invoked by name through the Skill tool, not read as a document, and
`--stage` says which half of the pipeline is calling:

- **`--stage harvest`** — a claimed job carries `skill: channel-youtube`. Capture
  the video into `--capture-dir` per Content extraction and Media below:
  `metadata.json` from `yt-dlp --dump-json`, captions alongside it. The job
  already carries the resolved config (`assets`, `transcript`, tags, areas) —
  honor it; never re-ask.
- **`--stage process`** — the capture is on disk and the note is due. Run this
  unit's own builder, from the wiki root:

  ```
  llm-wiki-ops run ops/skills/channel-youtube/scripts/youtube_note.py <root> --capture-dir <dir>
  ```

  Then write the summary it left as a placeholder. Do not hand-assemble the
  note; see Content extraction for why.

Chaining to another unit? Invoke it **by name through the Skill tool** — never
read a sibling's SKILL.md and improvise its behavior from what you read.

## Venue knowledge

### Fingerprints

- Host is `youtube.com`/`www.youtube.com` or the `youtu.be` short-link
  form.

### Discovery

- No Firecrawl/Playwright needed — `yt-dlp` alone handles a single video
  page: metadata and captions both come from it directly, no DOM to
  render.
- `scope: page` is the proven shape (single video). Enumerating a whole
  channel or playlist is untested; `yt-dlp --flat-playlist --dump-json
  <channel-or-playlist-url>` is the likely route but has not been
  exercised — treat as an unverified seed.

### Dates

- `upload_date` field in the `yt-dlp --dump-json` output, `YYYYMMDD`
  format.

### Access / paywall

- No paywall concept for standard public videos. Age/region-gated or
  members-only videos not yet encountered — `needs_auth` has been `false`
  on everything harvested so far; treat gated content as unverified until
  one is seen.

### Content extraction

- Use `yt-dlp --dump-json --no-download <url>` as ground truth in place of
  `page.html`, saved as `metadata.json` in the capture dir — returns
  `title`, `uploader`/`channel`, `channel_url`, `upload_date`, `duration`/
  `duration_string`, `chapters` (list of `{start_time, end_time, title}`),
  `view_count`, `like_count`, `thumbnail`, `description`, `webpage_url`,
  `id` in one JSON blob, everything a note's frontmatter/body needs.
- **Note building is scripted — don't hand-assemble.** `--stage process` runs
  this unit's own builder,
  `llm-wiki-ops run ops/skills/channel-youtube/scripts/youtube_note.py <root> --capture-dir <dir>`,
  which builds the whole note deterministically: full metadata
  frontmatter, thumbnail + embed, the description converted to markdown
  (URLs linkified, the creator's TIMESTAMPS turned into a list, hashtag
  pile removed), and the transcript as chapter-headed timestamped
  sections. Hand-assembly is what once produced a one-paragraph,
  `[music]`-littered transcript and a raw description — the script exists
  so that can't recur. The output shape is documented beside it, at
  `references/note-shape.md` in this unit.

### Media

- **Captions/transcript**: `yt-dlp --skip-download --write-auto-sub
  --write-sub --sub-lang en --convert-subs srt <url> -o "<path>"`. Passing
  both `--write-sub` and `--write-auto-sub` fetches manual captions if
  present, else falls back to auto-generated (ASR) captions automatically —
  no need to branch on `metadata.subtitles` vs
  `metadata.automatic_captions` yourself.
- **ASR rolling-caption overlap**: YouTube's auto-generated `.srt`/`.vtt`
  is a rolling-caption format — consecutive cues repeat part of the
  previous cue's text, and the file is peppered with `[Music]`/`[Applause]`
  sound tags. The plugin's `format_transcript.py` handles both (longest
  suffix/prefix word-overlap dedup + sound-tag stripping) and buckets the
  result under the video's chapters as timestamped `### [mm:ss] Title`
  sections. This unit's `youtube_note.py` calls it through the wiki's front
  door (`llm-wiki-ops run scripts/format_transcript.py`) rather than
  importing it — don't reimplement the dedup by hand.
- **Reference-mode (no video download)**: with `assets: reference`, record
  the watch URL and thumbnail URL in `capture.json` as asset entries with
  `status: "referenced"` — that is the asset script's own vocabulary for
  reference mode; use it rather than inventing a different status string.
- Actual video/audio download (when `assets: download`) not yet exercised —
  `yt-dlp -f <video_format from <ops dir>/config.json>` is the expected route
  (unverified seed).

### Auth

- No auth wall encountered on public videos. Age-gated, region-locked, or
  unlisted/private videos will likely need cookies via
  `--cookies-from-browser` or a storage-state equivalent — untested.

## Quirks log

- One harvested video had no manual captions (`subtitles: {}`); the
  auto-captions (`automatic_captions.en`) covered it — don't treat an
  empty `subtitles` field as a failure, check `automatic_captions` before
  giving up.
