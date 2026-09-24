# channel-spotify — customizing this skill

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:spotify/spotify.harvest`. What the stage reaches,
and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/spotify/spotify.harvest.md
```

`/llm-wiki:sandbox channel-spotify` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-spotify` binds the stage. `process` names no
sandbox and runs with no network.
