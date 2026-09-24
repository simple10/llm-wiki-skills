# channel-youtube — customizing this skill

Ask whether videos should be downloaded (`harvest.assets=download`) or kept as
cloud references (`harvest.assets=reference` — captions/transcript + a
watch-at-source link; the default worth suggesting for YouTube, since
the source rarely rots and video files are heavy).

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:youtube/youtube.harvest`. What the stage reaches,
and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/youtube/youtube.harvest.md
```

`/llm-wiki:sandbox channel-youtube` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-youtube` binds the stage. `process` names no
sandbox and runs with no network.
