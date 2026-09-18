# channel-notion-tasks — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask which Notion databases hold the operator's tasks and record their
   ids/names in the installed SKILL.md's `## Stages` harvest section's
   "Mechanical filters", along
   with the lookback window for the FIRST pull (default 14d) and any
   statuses to exclude (e.g. Archived). These belong in the unit, not the
   watch entry.
2. Ask the pull cadence (default daily) — that becomes `every` on
   the watch.
3. Customized is the point: writing the operator's databases and filters into
   SKILL.md makes `skills ls` report the unit `customized`. That is
   configuration the wiki owns, not drift to repair — say so in your report
   so nobody "fixes" it with `skills install --force`.
4. Ask a slug and a description, then declare the job. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-notion-tasks`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add notion-tasks slug=notion-tasks
   description="<what this is>" skill=channel-notion-tasks
   options.workspace=<workspace> [every=1d]` — the target is the channel's
   bare name, never a url, and `add` refuses without `options.workspace`
   (the unit's one required input). The manifest's `watch.dest` puts its daily
   ledgers under `research/channels/<slug>/` — the ledger route, outside the
   searchable corpus and the curation lifecycle (tasks are a stream; the
   wiki's synthesize policy is how their substance reaches `wiki/`). The
   watch's own `_raw/<slug>/` is already machine-local by construction, so
   there is nothing else to seed. `dest` is fixed at `add` — `pipeline edit`
   refuses it — so a job declared before this unit's 1.7.0 keeps its
   `sources/tasks/<slug>/` literal. Remind the operator that connector auth
   is session-level on the pulling machine.
