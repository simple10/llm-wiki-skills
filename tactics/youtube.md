# YouTube (as a research venue)

Every entry carries `(verified|unverified, YYYY-MM-DD, venue-observed-on)`.
Librarian-only file — workers file field notes instead. This covers *finding
and judging* videos; harvesting them is the scraper's youtube playbook.

## Recognizing it

`youtube.com` / `youtu.be` URLs; lane briefs naming channels or "video
coverage of X".

## Finding content (search & discovery)

- `yt-dlp --flat-playlist --dump-json <channel-or-search-url>` enumerates a
  channel's uploads or a search result page without rendering anything —
  titles, ids, upload dates in one pass. Search URL shape:
  `https://www.youtube.com/results?search_query=…` (unverified seed, 2026-07-10).
- Web-search the topic + `site:youtube.com` to find videos *people link to* —
  external citation is a stronger quality signal than YouTube's own ranking
  (unverified seed, 2026-07-10).
- A channel's community tab and video descriptions often link the creator's
  blog/newsletter — the denser-signal home of the same material
  (unverified seed, 2026-07-10).

## Judging quality fast

- Skim the transcript, don't watch: the scraper's captions route
  (`yt-dlp --skip-download --write-auto-sub`) works standalone for a quick
  relevance check on a single candidate (unverified seed, 2026-07-10).
- Chapters + a specific title beat length; talking-head videos over 30 min
  with no chapters are rarely worth a capture-rec without other evidence
  (unverified seed, 2026-07-10).

## Capture notes

- Single video: `scope=page`, `transcribe` per goal; channel watch:
  `scope=domain`-equivalent is untested in the scraper (its YouTube venue
  notes mark channel enumeration unverified) — recommend specific videos
  until that's proven (unverified seed, 2026-07-10).

## Quirks log

- (empty)
