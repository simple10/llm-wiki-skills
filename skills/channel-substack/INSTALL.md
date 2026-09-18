# channel-substack — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask which newsletter(s) this wiki watches (domain or archive URL) and a
   slug for each, and record them in the installed SKILL.md under a
   "Watched newsletters" heading — the skill body is wiki-owned.
2. Ask free-only vs licensed. Free needs no auth; licensed needs a
   Playwright storage state for the newsletter's own domain (custom-domain
   newsletters may not share substack.com cookies).
3. Ask for an age floor (e.g. only posts from the last 3 months) — that
   becomes `harvest.max_age` on the job, and rides each ticket as `min_date`.
4. Declare the job, naming the skill. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-substack`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <archive-url> slug=<newsletter>
   description="<what this is>" skill=channel-substack
   [harvest.max_age=3m] [harvest.access=free|licensed]` — the unit's manifest
   supplies `every=1d`, `harvest.scope=domain` and a `dest` of
   `sources/newsletters/<slug>`. **Scope MUST stay `domain`**: an archive is
   every post under the newsletter's host, so never pass `harvest.scope=page`
   for this unit. `llm-wiki-ops pipeline edit <slug> every=<period>` changes
   the cadence later.
