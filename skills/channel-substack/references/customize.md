# channel-substack — customizing this skill

Ask which newsletter(s) this wiki watches (domain or archive URL) and a
slug for each, and record them in the installed SKILL.md under a
"Watched newsletters" heading — the skill body is wiki-owned.

Ask free-only vs licensed. Free needs no auth; licensed needs a
Playwright storage state for the newsletter's own domain (custom-domain
newsletters may not share substack.com cookies). Licensed also needs the
skill to DECLARE a credential — see `references/enable.md` for the steps
and their cost.

Ask for an age floor (e.g. only posts from the last 3 months) — that
becomes `harvest.max_age` on the job, and rides each ticket as `min_date`.

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:substack/substack.harvest`. What the stage
reaches, and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/substack/substack.harvest.md
```

`/llm-wiki:sandbox channel-substack` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-substack` binds the stage. `process` names no
sandbox and runs with no network.
