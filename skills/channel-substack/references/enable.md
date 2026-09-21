# channel-substack — after enabling

1. **Licensed newsletters need the skill to DECLARE a credential**, which
   the shipped manifest does not (`requires.credential: false`, so that
   free jobs need no binding): a slice is granted a secret only when the
   skill declares the need and the job is bound. SKILL.md's "Auth" lists the
   steps — set `requires.credential` to `true` in this wiki's copy,
   re-enable, `llm-wiki-ops credential set <name>`, `llm-wiki-ops credential
   bind <slug> <name>` — and their cost: every job on this skill then needs
   a binding.
2. Declare the job, naming the skill: `llm-wiki-ops pipeline add
   <archive-url> slug=<newsletter>
   description="<what this is>" skill=channel-substack
   [harvest.max_age=3m] [harvest.access=free|licensed]` — the skill's manifest
   supplies `every=1d`, `harvest.scope=domain` and a `dest` of
   `sources/newsletters/<slug>`. **Scope MUST stay `domain`**: an archive is
   every post under the newsletter's host, so never pass `harvest.scope=page`
   for this skill. `llm-wiki-ops pipeline edit <slug> every=<period>` changes
   the cadence later.
