# channel-frameio — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask which share link(s) this wiki watches and record them in the
   installed SKILL.md under a "Watched shares" heading. Ask for a slug and a
   content-named destination for each — share-link domains carry no source
   identity, so a mechanically-derived one would be opaque
   (`--slug <content-name> --dest sources/scrapes/<content-name>`).
2. Ask whether asset titles carry a share-wide suffix worth trimming — that
   becomes `--title-strip` on the doc-note step; record the chosen value in
   the installed SKILL.md.
3. Check `yt-dlp` is on PATH on the harvesting machine (video capture
   depends on it); tell the operator if it is missing.
4. No auth walkthrough: guest share links authorize themselves.
5. Point the watch at the skill — **scope MUST be `domain`**, because the
   host's intake applies the watch's scope prefix to every leaf this unit
   discovers: `page` rejects all of them as out_of_scope while the run
   still exits 0, and `section` rejects them too whenever the watch URL is
   a FOLDER inside the share rather than its root:
   `llm-wiki-ops watch add --slug <content-name> --description "<what this is>"
   --url <share-url> --skill channel-frameio
   --scope domain --mode once --dest sources/scrapes/<content-name>`
