# channel-notion-tasks — customizing this skill

Ask which Notion databases hold the operator's tasks and record their
ids/names in the installed SKILL.md's `### harvest` "Mechanical filters",
along
with the lookback window for the FIRST pull (default 14d) and any
statuses to exclude (e.g. Archived). These belong in the skill, not the
watch entry.

Ask the pull cadence (default daily) — that becomes `every` on
the watch.

Customized is the point: writing the operator's databases and filters into
SKILL.md makes `skills ls` report the skill `customized`. That is
configuration the wiki owns, not drift to repair — say so in your report
so nobody "fixes" it with `skills install --force`.

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:notion-tasks/notion-tasks.harvest`. What the stage
reaches, and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/notion-tasks/notion-tasks.harvest.md
```

`/llm-wiki:sandbox channel-notion-tasks` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-notion-tasks` binds the stage. `process` names no
sandbox and runs with no network.
