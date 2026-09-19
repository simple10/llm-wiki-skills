# channel-youtube — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask whether videos should be downloaded (`harvest.assets=download`) or kept as
   cloud references (`harvest.assets=reference` — captions/transcript + a
   watch-at-source link; the default worth suggesting for YouTube, since
   the source rarely rots and video files are heavy).
2. Check `yt-dlp` is on PATH on the harvesting machine; tell the operator
   if it is missing.
3. Declare the job, naming the skill. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-youtube`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <video-url> slug=<video-name>
   description="<what this is>" skill=channel-youtube` — the unit's manifest
   supplies the rest (`every=once`, `harvest.scope=page`,
   `harvest.assets=reference`, and a `dest` of `sources/youtube/<slug>`), so
   add `harvest.assets=download` only if the operator chose downloads. Single
   videos are the proven shape; channel/playlist enumeration is untested (see
   the SKILL.md's Discovery section).
