# channel-spotify — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize and set up now, while the operator is present:

1. **API credentials** — needed for search and guaranteed-complete item
   lists (public catalog only; no user login exists in this unit). Have
   the operator create an app at developer.spotify.com → Dashboard, then
   store per machine:
   `llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py auth --client-id … --client-secret …`
   (writes + tests the `spotify` credential, machine-local and never
   synced). Without credentials the capture degrades to the keyless embed
   fallback: possibly-truncated item lists, flagged `"keyless": true` and
   with a warning callout in the note.
2. **Non-URL requests** ("add the Lex Fridman podcast episode 400")
   resolve through the unit's search before anything is watched:
   `llm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py search "lex fridman #400" --type episode`
   — confirm the match with the operator, then watch the chosen URL.
3. **Watch shape**: one watch per entity URL, `--scope page` (the capture
   enumerates the entity's items itself — there is no link crawling).
   open.spotify.com is a generic share host carrying no source identity,
   so pick `--slug <content-name> --dest sources/scrapes/<content-name>`
   named after the
   content, and set `--group "<name>" --group-type playlist|series` — a
   playlist or show is a bundle.
4. **Set audio expectations**: downloads happen only when the content is
   openly distributed (podcast episodes matched to their show's public
   RSS feed). Music tracks and Spotify-exclusive audio are captured as
   `drm_protected` references with full metadata — never ripped.

## 1.7.0 — the declared hosts were unreachable, and the media route still is

Nothing to migrate; re-`refresh` an installed copy so the fix reaches it.

`requires.network` declared `["spotify.com"]`, and a bare hostname grants
exactly that hostname on both sandbox runtimes — no implicit subdomains. So a
confined harvest reached none of this unit's endpoints. MEASURED under nono
0.74.0, macOS Darwin 25.5.0 arm64, 2026-08-21, with an unsandboxed control on
every target: under `["spotify.com"]`, `open.spotify.com`, `api.spotify.com`
and `accounts.spotify.com` all answer `000` (unsandboxed `200`, `401`, `405`
— all three reached). Under `["*.spotify.com"]` all three are reached.
`itunes.apple.com` is measured too (`200`) and is covered by no Spotify
wildcard: it is the keyless feed lookup this unit resolves podcast RSS
through.

The declaration is now
`["*.spotify.com", "spotify.com", "itunes.apple.com"]`.

**What that still does not cover, deliberately.** The open-audio route ends
at the creator's own RSS feed and MP3 enclosure — measured, the iTunes lookup
for one show returns `https://lexfridman.com/feed/podcast/` — and those hosts
differ per show and are not known when the host computes the session's
egress. A confined harvest therefore reaches metadata and the feed lookup and
fails the download with a named allowlist refusal. That gap is known and open; do not
try to finish the list.
