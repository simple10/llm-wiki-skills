# channel-frameio — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask which share link(s) this wiki watches and record them in the
   installed SKILL.md under a "Watched shares" heading. Ask for a slug and a
   content-named destination for each — share-link domains carry no source
   identity, so a mechanically-derived one would be opaque
   (`slug=<content-name> dest=sources/scrapes/<content-name>`).
2. Ask whether asset titles carry a share-wide suffix worth trimming — that
   becomes `--title-strip` on the doc-note step; record the chosen value in
   the installed SKILL.md.
3. Check `yt-dlp` is on PATH on the harvesting machine (video capture
   depends on it); tell the operator if it is missing.
4. No auth walkthrough: guest share links authorize themselves.
5. Declare the job, naming the skill. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-frameio`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <share-url> slug=<content-name>
   description="<what this is>" skill=channel-frameio
   dest=sources/scrapes/<content-name>` — the unit's manifest supplies
   `every=once` and `harvest.scope=domain`. **Scope MUST stay `domain`**: a
   share's leaves sit under the share host, not under the watched URL, so
   `page` and (for a FOLDER inside the share) `section` both exclude them.
   Name `dest` for the content now: moving it later means re-running this
   `add` with a new `dest=`, since `pipeline edit` refuses that key.
