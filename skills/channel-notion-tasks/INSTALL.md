# channel-notion-tasks — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask which Notion databases hold the operator's tasks and record their
   ids/names in the installed SKILL.md's `## Stages` harvest section's
   "Mechanical filters", along
   with the lookback window for the FIRST pull (default 14d) and any
   statuses to exclude (e.g. Archived). These belong in the unit, not the
   watch entry.
2. Ask the pull cadence (default daily) — that becomes `--check-every` on
   the watch.
3. Diverged is the point: writing the operator's databases and filters into
   SKILL.md makes `skills list` report the unit `diverged`. That is
   configuration the wiki owns, not drift to repair — say so in your report
   so nobody "fixes" it.
4. Ask a slug and a description, then point the watch at the unit:
   `llm-wiki-ops watch add --slug notion-tasks --description "<what this is>"
   --skill channel-notion-tasks --inputs workspace=<workspace>
   [--check-every 1d]`. The manifest's `watch.dirs.sources` puts its notes
   under `sources/tasks/<slug>/`; the watch's own `_raw/<slug>/` is already
   machine-local by construction, so there is nothing else to seed. Remind
   the operator that connector auth is session-level on the pulling
   machine.
