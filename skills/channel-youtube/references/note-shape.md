# YouTube note template

The shape this unit's `scripts/youtube_note.py` produces. Documented here so the output
is reviewable and so other video platforms (Vimeo, Loom) can follow the same
structure. The process worker runs the script, then fills the summary and
optionally trims the description.

```markdown
---
title: "Video Title"
resource: https://www.youtube.com/watch?v=<id>
type: video
channel: Channel Name
channel_url: https://www.youtube.com/channel/<id>
published: 2026-06-18            # from upload_date, YYYY-MM-DD
duration: 10:02                  # duration_string
views: 517273
likes: 16124
video_id: <id>
thumbnail: https://i.ytimg.com/vi/<id>/maxresdefault.jpg
source_host: [www.youtube.com, youtube.com]   # the site, at both breadths
areas: ["[[Fitness]]"]                        # declared knowledge areas
tags: [youtube, video]
status: draft
---
```

`published` is a **protocol well-known field** (the note-format
contract's well-known set), not this unit's private convention — it means
the date the content was published at its source, in `YYYY-MM-DD`, and every
consumer reads it that way. This unit converts yt-dlp's `upload_date`
(`YYYYMMDD`) into it. The line is **omitted entirely** when the upload date
is missing or oddly shaped: absent means unknown, while an empty
`published:` is a malformed value that lint reports and the engine drops.

`source_host` and `areas` are two keys with two meanings — the mechanical
site at progressively broader breadths, and the curated knowledge areas
declared on the watch. They were once one `domains:` list told apart by
bracket shape, which is why neither filtered reliably.

```markdown
> [!summary]
> (process worker writes 2-4 sentences)

![thumbnail](<thumbnail-url>)

<iframe ... src="https://www.youtube.com/embed/<id>" ...></iframe>

## Description

(creator's description, converted to markdown: bare URLs linkified,
the creator's own TIMESTAMPS turned into a `- \`m:ss\` label` list,
trailing hashtag pile removed)

## Transcript

*Auto-generated captions, cleaned and split by chapter.*

### [00:00] Chapter title
(readable, de-duplicated paragraphs)

### [01:29] Next chapter title
...
```

## Why deterministic

Video metadata belongs in frontmatter (so it's queryable and OKF-selectable),
not scattered as bold lines in prose. And the transcript is the whole reason
to capture a video into a wiki — it has to be timestamped and readable so
ingest can cite knowledge by time. Hand-assembly flattened the transcript into
one `[music]`-littered paragraph; the script guarantees the structure every
time. Chapters (from `yt-dlp` metadata) become the transcript's section
anchors; if a video has none, the plugin's `format_transcript.py` falls back to fixed-
interval `[mm:ss]` sections.
