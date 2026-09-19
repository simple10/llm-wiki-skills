# channel-spotify — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize and set up now, while the operator is present:

1. **API credentials** — needed for search and guaranteed-complete item
   lists (public catalog only; no user login exists in this unit). Have
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
   One `skill=channel-spotify` covers both stages: this unit renders its own
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

The manifest's `requires.network` covers the Spotify endpoints, Spotify's
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

Established from the host source (`pipeline/slicing.py::credential_for`,
`schedule/runner/slice.py::compose_slice`, `common/wiki/secrets.py::get`), not
yet from a live run. A slice is granted read on ONE credential payload, and
only when the unit declares `requires.credential: true` and the job has a
binding. This unit ships `requires.credential: false`, so inside a slice:

- a machine with **no** `spotify` credential captures keyless, as documented
  (`partial`, possibly-truncated list);
- a machine that **has** one cannot read it: `credential get spotify` answers
  `cannot read credential …`. The capture still lands, keyless, and its
  report is `partial` with a `missing[]` entry `why: auth` and a reason that
  points here. The API — full item lists, dates, `min_date` — is not used.

Two ways to give a confined harvest the API; both are the operator's call:

1. **Grant the one file on this machine.** `~/.config/llm-wiki/sandbox/slice.jsonc`
   is the operator's own layer and may grant one path to every slice on the
   box: a `filesystem.read` entry for the payload, which is `spotify.json`
   inside the `store` directory that `llm-wiki-ops --json credential get spotify`
   reports. Keeps keyless capture working on machines with no credentials.
   Every slice on the box can then read that file. Unverified live.
2. **Declare the credential.** Set `requires.credential: true` in this wiki's
   copy of the manifest and bind it per job: `llm-wiki-ops credential bind <slug> spotify`.
   The host then grants the slice that payload and puts its name on the
   ticket, which the script honors. The cost: a machine with no binding is
   INCAPABLE of the stage — skipped at the claim gate with a `bindings`
   doctor row, never run — so the keyless fallback stops being reachable
   from a ticket on that machine.

Hand runs outside a slice read the store directly and need neither.
