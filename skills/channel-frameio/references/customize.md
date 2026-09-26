# channel-frameio — customizing this skill

Ask which share link(s) this wiki watches and record them in the
installed SKILL.md under a "Watched shares" heading. Ask for a slug and a
content-named destination for each — share-link domains carry no source
identity, so a mechanically-derived one would be opaque
(`slug=<content-name> dest=sources/scrapes/<content-name>`).

Ask whether asset titles carry a share-wide suffix worth trimming — that
becomes `--title-strip` on the capture step (`harvest_share.py`, which
hands it to every leaf, and each leaf records it for the process step);
record the chosen value in the installed
SKILL.md. The share's author, group and tags go on the JOB, not in this
file — `meta.author=`, `meta.group=`, `meta.group_type=`, `meta.tags=`,
`meta.areas=` on the `pipeline jobs add` at enable — and the host stamps
them onto every page the job lands.

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:frameio/frameio.harvest`. What the stage reaches,
and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/frameio/frameio.harvest.md
```

`/llm-wiki:sandbox channel-frameio` reviews it into a wiki sandbox, and
`/llm-wiki:enable channel-frameio` binds the stage. `process` names no
sandbox and runs with no network.
