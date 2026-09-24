# channel-circle — customizing this skill

Circle communities run on `*.circle.so` **or their own custom domain**, but
a custom-domain community still renders Circle's own uniform chrome — so
ONE installed copy normally serves every community this wiki watches, with
one watch per community.

Ask which community this wiki watches (the `*.circle.so` domain or the
custom domain fronting it) and record it in the installed SKILL.md under
a "Watched communities" heading — the skill body is wiki-owned. For a
custom-domain community, also add `host:<domain>` to the installed copy's
`manifest.json` `keywords`, so `skills search <domain>` answers from the
wiki's copy directly. The harvest slice reaches that host as the ticket's
own; the sandbox needs no entry for it.
There is no second copy to install: identity is the JOB's slug, so two
communities are two jobs on this one skill, each under its own heading
here.

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:circle/circle.harvest`. What the stage reaches,
and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/circle/circle.harvest.md
```

`/llm-wiki:sandbox channel-circle` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-circle` binds the stage. `process` names no
sandbox and runs with no network.
