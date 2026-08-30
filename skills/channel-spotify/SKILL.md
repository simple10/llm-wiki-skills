---
name: channel-spotify
description: Spotify entity capture — playlists, shows, episodes, audiobooks. Metadata via the Web API; audio only from openly-distributed podcast RSS feeds; DRM catalog audio stays reference-only.
argument-hint: --stage harvest --capture-dir <dir> [--url <url>]
user-invocable: false
---

# Channel: Spotify

You capture **Spotify entities** — playlists, shows (podcasts), episodes,
albums, tracks, audiobooks — for this wiki. This file is authoritative for how
the venue is resolved and captured. The job already carries the resolved
config (assets, transcript policy) — honor it; never re-ask the operator.

This unit is **platform-general** — nothing in it is specific to any one
show or playlist. Once installed, the copy is **wiki-owned**: record your
wiki's watched entities and venue observations in it, and `skills list`
reporting it as diverged is provenance, not a problem. Chaining to another
unit means invoking it through the Skill tool — never read a sibling's
SKILL.md and improvise its behavior.

## The boundary this unit is built around

Spotify catalog audio (music tracks, Spotify-exclusive shows, audiobook
chapters) is **DRM-protected**. This unit **never rips it** — same contract as
the rest of the pipeline: DRM items are recorded as `drm_protected`
references (full metadata + `player_url`), and you move on. What it DOES
download is **openly-distributed audio**: most podcasts on Spotify also
publish a public RSS feed with plain MP3 enclosures; the unit resolves that
feed (keyless iTunes Search API) and hands the enclosures to the normal
asset download. A "Spotify capture" is therefore always full metadata, and
audio exactly when the creator distributes it openly.

## Stages

- **`--stage harvest`** — a claimed job carries `skill: channel-spotify`.
  Run `scripts/spotify.py capture <url> --capture-dir <dir>`, then the
  standard asset flow (below). Exit 4 = entity captured but no audio was
  resolvable (all-DRM or no feed match) — that is a metadata-only capture,
  not a failure; complete the job.
- **`--stage process`** — not declared. The capture ships a ready `page.md`
  (title, creator, description, item table with per-item audio route), so the
  generic scaffolder has everything; there is no HTML and no extract block.

## Fingerprints

- URLs: `open.spotify.com/<type>/<id>` where type ∈ playlist | show |
  episode | album | track | audiobook | artist; `spotify:<type>:<id>` URIs;
  `/intl-xx/` locale prefixes appear and are tolerated.
- oEmbed at `open.spotify.com/oembed?url=…` (keyless, title + thumbnail).
- Embed pages at `open.spotify.com/embed/<type>/<id>` carry `__NEXT_DATA__`
  JSON with the entity and a possibly truncated item list (keyless).

## Discovery

One watch = one entity URL (`scope: page`). Enumeration happens **inside**
the capture, via the Web API with full pagination — playlists → items,
shows → episodes, albums → tracks, audiobooks → chapters. There is no link
crawling; discovered-URL queueing is not used.

**Search-driven adds.** When the operator names content instead of pasting a
URL ("add the Lex Fridman podcast episode 400"), resolve it first:

    llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py search "lex fridman #400" --type episode

JSON rows come back with name/by/date/duration/url — confirm the match with
the operator, then watch the chosen URL. Search requires API credentials.

## Dates

Episodes, albums and audiobooks carry `release_date` (day precision).
Playlist items carry each item's own release date; the playlist itself has
none. `capture --min-date YYYY-MM-DD` drops older items, which is how a
show watch with `max_age` should be run. Keyless captures have no dates.

**`release_date` is this venue's `published`.** `capture` prints it as
`published` in its summary, already gated to day precision — copy that value
straight into `capture.json`, and `scaffold` stamps the note's `published:`
from it. Do not translate `release_date` yourself, and do not fall back to a
bare year: Spotify's `release_date` honors its `release_date_precision`, so
an album can return `"1979"`, which names no publication day. An empty
`published` in the summary means the entity declared none — leave the key out
of `capture.json` entirely. The listing table keeps its own `release_date`
column as is; that is display, not frontmatter.

## Access / auth

- **API credentials** (client-credentials flow; public catalog only):
  stored as the `spotify` credential (`{"client_id": …, "client_secret": …}`)
  — machine-local, never synced. Set up once per machine:
  `llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py auth --client-id … --client-secret …`
  (creates + tests). The operator gets credentials from
  developer.spotify.com → create an app; no user login is involved.
- **Keyless degradation**: with no credentials, `meta`/`capture` fall back to
  the public embed endpoint — entity name and a possibly **truncated** item
  list, `"keyless": true` in the output, and a warning callout in `page.md`.
  Good enough to probe; re-capture with credentials for the real thing.
- **Out of scope**: private playlists and the user library (would need user
  OAuth, which nothing here implements). Search needs credentials.

## Media

- **Podcast episodes** — the open route. The show's public RSS is resolved
  via the keyless iTunes Search API (show name → `feedUrl`), episodes matched
  by normalized-title overlap + duration window (±150 s, score ≥ 1.0). A
  match yields a plain MP3 enclosure as a `pending` `audio` entry in
  `assets.json`; the match score and feed are recorded in the entry's `note`.
- **Tracks / audiobook chapters / unmatched episodes** — DRM or exclusive:
  recorded in `meta.json` `drm_refs` (id, name, url, `status:
  drm_protected`, `preview_url` when the API offers a 30 s preview) and
  merged into `capture.json`'s asset list by the worker. Never fed to
  yt-dlp, never in `assets.json`.
- No captions anywhere on this venue's open route (RSS rarely carries
  transcripts) — the transcript policy is what produces text from downloaded
  episodes.

## Capture flow (harvest worker)

```
llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py capture <url> \
    --capture-dir <job.capture_dir>                              # writes:
#   meta.json    entity + items + feeds + drm_refs + counts
#   items.json   normalized item list
#   page.md      ready for stage 2 (no HTML exists for this venue)
#   assets.json  pending entries: cover image + matched MP3 enclosures
llm-wiki-ops run scripts/assets.py download <cap>/assets.json \
    --dest <job.dirs.assets> --referer <url>
# capture.json: assemble as usual; assets = assets.json entries (now
# downloaded) + meta.json drm_refs verbatim. tool: "spotify-api".
# published: copy the capture summary's `published` when non-empty (Dates).
# transcript: enqueue per job policy — audio type is what transcribe expects.
```

`--no-audio` captures metadata + cover only (fast probe). `--keyless` forces
the embed fallback. A `verdict` of `partial` is right only when the API
item list itself was truncated (keyless) — an all-DRM capture with complete
metadata is `complete`, with the reasons naming the DRM route.

## Scripts

All in `scripts/spotify.py` (PEP 723; run from the wiki root):

- `auth --client-id … --client-secret …` — store + test credentials.
- `search <query> [--type …] [--limit N] [--market US]` — catalog search,
  JSON rows out; the add flow's resolver for non-URL requests.
- `meta <url> [--keyless]` — normalized entity JSON, full item pagination.
- `resolve-feed <show-or-episode-url> | --show-name <name>` — public RSS
  feed + episode list, no credentials needed.
- `capture <url> --capture-dir <dir> [--market US] [--min-date D]
  [--keyless] [--no-audio]` — the whole capture; exit 4 = no audio resolved.

## Quirks log

- **2026-08-01** — A playlist labeled "audiobook" may actually be podcast
  **episodes**: the $100M Money Models playlist (`4rprjH5cIR72vskqa6RhpC`)
  is 9 episodes of the openly-distributed "The Game with Alex Hormozi", so
  its audio was fully downloadable via the open feed. Check item URIs
  (`spotify:episode:` vs `spotify:track:`) before assuming DRM.
- **2026-08-01** — Feed episode titles may embed a subtitle after a pipe
  ("Part 2: … | $100M Money Models Audiobook"); escape `|` when rendering
  markdown tables (the capture script does).
- **2026-08-01** — The embed endpoint's `trackList` showed all 9 playlist
  items, but treat keyless item lists as possibly truncated on larger
  entities (the embed player paginates around ~100).
