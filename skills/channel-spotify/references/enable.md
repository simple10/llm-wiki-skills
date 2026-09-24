# channel-spotify — after enabling

1. **API credentials** — needed for search and guaranteed-complete item
   lists (public catalog only; no user login exists in this skill). Have
   the operator create an app at developer.spotify.com → Dashboard, then
   store per machine — the OPERATOR runs this, at a terminal:
   `llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py auth --client-id <id>`
   It prompts for the client secret without echo (with no terminal it reads
   one line of stdin), then writes + tests the `spotify` credential,
   machine-local and never synced. Never pass the secret as an argument: argv
   is readable in `ps` and lands in shell history. Without credentials the
   capture degrades to the keyless embed fallback: possibly-truncated item
   lists, flagged `"keyless": true` and with a warning callout in the note.
   **Read "Credentials under a confined harvest" below** — stored
   credentials alone do not reach a scheduled, confined run.
2. **Non-URL requests** ("add the Lex Fridman podcast episode 400")
   resolve through the skill's search before anything is watched:
   `llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py search "lex fridman #400" --type episode`
   — confirm the match with the operator, then watch the chosen URL.
3. **Declare the job**: one per entity URL.
   `llm-wiki-ops pipeline add <entity-url> slug=<content-name>
   description="<what this is>" skill=channel-spotify
   meta.group="<name>" meta.group_type=playlist|series` — a playlist or show
   is a bundle. open.spotify.com is a generic share host carrying no source
   identity, so name the slug after the content. The skill's manifest supplies
   `every=once`, `harvest.scope=page` (the capture enumerates the entity's
   items itself — there is no link crawling), `harvest.assets=download` and a
   `dest` of `sources/podcasts/<slug>`; pass `dest=` to land it elsewhere.
   One `skill=channel-spotify` covers both stages: this skill renders its own
   venue's page into `dest`.
   **A show or playlist the operator wants RE-pulled for new episodes needs
   two more keys**: `every=<period> harvest.refresh=<period>`. The container
   URL is the job's one page, so once it is held every later pull is
   `skipped`; only a refresh ticket re-captures it, and the host mints those
   from `harvest.refresh` (`never`, the default, or a period — and it refuses
   a refresh period on an `every=once` job). Ask which the operator wants: a
   one-time snapshot (the defaults) or a followed show.
4. **Set audio expectations**: downloads happen only when the content is
   openly distributed (podcast episodes matched to their show's public
   RSS feed). Music tracks and Spotify-exclusive audio are captured as
   `drm_protected` references with full metadata — never ripped.

## Known limitation — media egress under a confined harvest

The harvest stage's sandbox reference covers the Spotify endpoints, Spotify's
cover-art CDNs (`*.scdn.co`, `*.spotifycdn.com` — the cover is an asset of
every capture) and the keyless iTunes feed lookup, and deliberately nothing
more: the open-audio
route ends at the creator's own RSS feed and MP3 enclosure (one show's
lookup resolves to `https://lexfridman.com/feed/podcast/`), and those hosts
differ per show and are not known when the host computes the session's
egress. A confined harvest therefore reaches metadata and the feed lookup
and fails the download with a named allowlist refusal. The gap is
deliberate — do not try to finish the allowlist.

## Credentials under a confined harvest

This skill declares `requires.credential: "optional"`, which never makes the
stage unclaimable. A job with no binding on this machine captures keyless
(`partial`, possibly-truncated list). A job bound with `llm-wiki-ops
credential bind <slug> spotify` gets that one payload in its slice, named on
its ticket, and the capture uses the API: full item lists, dates,
`min_date`. It may be spent only at `api.spotify.com` and
`accounts.spotify.com`, the exact `host:` keywords the manifest claims.
Unverified live.

Hand runs outside a slice read the store directly and need neither.
