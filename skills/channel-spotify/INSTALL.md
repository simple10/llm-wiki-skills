# channel-spotify — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

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
3. **Declare the job**: one per entity URL. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-spotify`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <entity-url> slug=<content-name>
   description="<what this is>" skill=channel-spotify
   meta.group="<name>" meta.group_type=playlist|series` — a playlist or show
   is a bundle. open.spotify.com is a generic share host carrying no source
   identity, so name the slug after the content. The unit's manifest supplies
   `every=once`, `harvest.scope=page` (the capture enumerates the entity's
   items itself — there is no link crawling), `harvest.assets=download` and a
   `dest` of `sources/podcasts/<slug>`; pass `dest=` to land it elsewhere.
4. **Set audio expectations**: downloads happen only when the content is
   openly distributed (podcast episodes matched to their show's public
   RSS feed). Music tracks and Spotify-exclusive audio are captured as
   `drm_protected` references with full metadata — never ripped.

## Known limitation — media egress under a confined harvest

The manifest's `requires.network` covers the Spotify endpoints and the
keyless iTunes feed lookup, and deliberately nothing more: the open-audio
route ends at the creator's own RSS feed and MP3 enclosure (one show's
lookup resolves to `https://lexfridman.com/feed/podcast/`), and those hosts
differ per show and are not known when the host computes the session's
egress. A confined harvest therefore reaches metadata and the feed lookup
and fails the download with a named allowlist refusal. The gap is
deliberate — do not try to finish the allowlist.
