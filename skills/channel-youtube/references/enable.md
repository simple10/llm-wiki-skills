# channel-youtube — after enabling

1. Declare the job, naming the skill: `llm-wiki-ops pipeline jobs add
   <video-url> slug=<video-name>
   description="<what this is>" skill=channel-youtube` — the skill's manifest
   supplies the rest (`every=once`, `harvest.scope=page`,
   `harvest.assets=reference`, and a `dest` of `sources/youtube/<slug>`), so
   add `harvest.assets=download` only if the operator chose downloads. Single
   videos are the proven shape; channel/playlist enumeration is untested (see
   the SKILL.md's Discovery section).
