# YouTube capture shape

What this unit's `scripts/youtube_note.py` leaves in the capture directory at
HARVEST time: a page BODY (`page.md`) and the capture record (`capture.json`)
that names it. Documented here so the output is reviewable and so other video
platforms (Vimeo, Loom) can follow the same structure.

The unit writes no page. The host's generic extractor (`pipeline extract`)
takes `page.md` verbatim and writes the page under the job's `dest`, with
frontmatter of its own — `title`, `status: draft`, `resource` (the capture's
`item`), `harvested`. That is why neither file below carries any of those.

## `capture.json`

```json
{
  "slug": "<job slug>",
  "item": "https://www.youtube.com/watch?v=<id>",
  "title": "Video Title",
  "body": "page.md",
  "content_type": "text/markdown",
  "fetched_at": "2026-09-19T03:09:15Z",
  "frontmatter": {
    "type": "video",
    "channel": "Channel Name",
    "channel_url": "https://www.youtube.com/channel/<id>",
    "published": "2026-06-18",
    "duration": "10:02",
    "views": 517273,
    "likes": 16124,
    "video_id": "<id>",
    "thumbnail": "https://i.ytimg.com/vi/<id>/maxresdefault.jpg",
    "source_host": ["www.youtube.com", "youtube.com"],
    "tags": ["youtube", "video"],
    "areas": ["[[Fitness]]"]
  }
}
```

`slug` and `item` come from `ticket.json` beside the capture (`--slug`/`--item`
on a hand run); `title` is the video's; `fetched_at` is when yt-dlp wrote
`metadata.json`.

`frontmatter` is the video's exact facts, scalars and flat lists only. **The
extractor ignores it today** — it is accepted, not merged — so every fact in it
is ALSO in the body's facts list, and nothing is lost in the meantime. It never
carries a key a host verb owns: `title`, `resource`, `status`, `harvested`,
`extracted`, `document_id`, `document_revision`.

**A fact that is not known is omitted, never emitted empty.** `published` is a
**protocol well-known field** (the note-format contract's well-known set), not
this unit's private convention — it means the date the content was published at
its source, in `YYYY-MM-DD`, and every consumer reads it that way. This unit
converts yt-dlp's `upload_date` (`YYYYMMDD`) into it, and leaves the key out
when the upload date is missing or oddly shaped: absent means unknown, while an
empty `published` is a malformed value that lint reports and the engine drops.

`source_host` and `areas` are two keys with two meanings — the mechanical site
at progressively broader breadths, and the curated knowledge areas. They were
once one `domains:` list told apart by bracket shape, which is why neither
filtered reliably. `tags` and `areas` are present only on a hand run that
passed `--tag`/`--area`: a job's `meta` never crosses into a harvest slice, so
a ticket carries neither, and the foreman's `pipeline apply` is what stamps the
job's own onto the page.

## `page.md`

Body only. **Never a `---` block**: the extractor prepends its own, and a
second one corrupts the page. Every block opens with markup of its own or sits
under a heading, so the body cannot open with a `---` line even when a
creator's description does.

```markdown
![thumbnail](<thumbnail-url>)

<iframe ... src="https://www.youtube.com/embed/<id>" ...></iframe>

- **Channel**: [Channel Name](<channel_url>)
- **Published**: 2026-06-18
- **Duration**: 10:02
- **Views**: 517273 · **Likes**: 16124
- **Video ID**: `<id>`
- **Source**: <https://www.youtube.com/watch?v=<id>>

## Description

(creator's description, converted to markdown: bare URLs linkified,
the creator's own TIMESTAMPS turned into a `- \`m:ss\` label` list,
trailing hashtag pile removed)

## Transcript

*Auto-generated captions, cleaned and split by chapter.*

#### [00:00] Chapter title
(readable, de-duplicated paragraphs)

#### [01:29] Next chapter title
...
```

A facts line whose value is unknown is left out, the same rule as the
`frontmatter` object. The transcript's heading level is the plugin
formatter's, not this unit's. There is no `[!summary]` callout: that was a
placeholder for a process worker of this unit, and no process ticket reaches a
unit — a summary is the host's process side's to write. A job with
`process.embeds: false` has the iframe taken out by the extractor.

## Why deterministic

Video metadata belongs in structured fields (so it's queryable and
OKF-selectable), not scattered through prose. And the transcript is the whole
reason to capture a video into a wiki — it has to be timestamped and readable
so ingest can cite knowledge by time. Hand-assembly flattened the transcript
into one `[music]`-littered paragraph; the script guarantees the structure every
time. Chapters (from `yt-dlp` metadata) become the transcript's section
anchors; if a video has none, the plugin's `format_transcript.py` falls back to
fixed-interval `[mm:ss]` sections.
