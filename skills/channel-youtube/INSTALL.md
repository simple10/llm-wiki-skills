# channel-youtube — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask whether videos should be downloaded (`--assets download`) or kept as
   cloud references (`--assets reference` — captions/transcript + a
   watch-at-source link; the default worth suggesting for YouTube, since
   the source rarely rots and video files are heavy).
2. Check `yt-dlp` is on PATH on the harvesting machine; tell the operator
   if it is missing.
3. Point the watch at the skill:
   `llm-wiki-ops watch add --slug <video-name> --description "<what this is>"
   --url <video-url> --skill channel-youtube
   --scope page --mode once --assets reference` — single videos are the
   proven shape; channel/playlist enumeration is untested (see the
   SKILL.md's Discovery section).
