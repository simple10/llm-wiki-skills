# channel-gmail — customizing this skill

Ask the lookback window for the FIRST pull (default 7d) and any
mechanical filters — Gmail labels and senders to exclude beyond the
defaults. They go into the installed SKILL.md's `### harvest` section, and
they are the WIKI's, applying to every mailbox this skill pulls. Ask what
the mailbox's junk is, too: the junk rules and the line each message gets
are the `### process` section's, and equally the wiki's to write.

Ask the pull cadence (default daily) — that becomes `every` on the
watch; the manifest pre-answers `1d`.

Customized is the point: writing the operator's filters into `## Stages`
makes `skills ls` report the skill `customized`. That is configuration the
wiki owns, not drift to repair — say so in your report so nobody "fixes"
it with `skills install --force`.

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:gmail/gmail.harvest`. What the stage reaches, and
why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/gmail/gmail.harvest.md
```

`/llm-wiki:sandbox channel-gmail` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-gmail` binds the stage. `process` names no sandbox
and runs with no network.
