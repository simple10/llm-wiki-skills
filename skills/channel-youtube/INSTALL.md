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

## 1.10.0 — the declared hosts were unreachable, and now are not

Nothing to migrate; re-`refresh` an installed copy so the fix reaches it.

`requires.network` declared `["youtube.com", "youtu.be"]`, and a bare
hostname grants exactly that hostname on both sandbox runtimes — no implicit
subdomains. So a confined harvest could not reach `www.youtube.com` at all.
MEASURED under nono 0.74.0, macOS Darwin 25.5.0 arm64, 2026-08-21, with an
unsandboxed control on every target: `["youtube.com"]` → `www.youtube.com`
`000` (unsandboxed `200`), and `yt-dlp --dump-json` under that jail exits 1
with *"403 Forbidden: host www.youtube.com:443 is not in the allowlist"*.
With `*.youtube.com` added it exits 0, and the SKILL.md captions command
writes its `.srt`.

The declaration is now
`["*.youtube.com", "youtube.com", "youtu.be", "*.googlevideo.com",
"*.ytimg.com"]`. `youtu.be` is measured separately — a short-link watch under
`["youtu.be"]` alone still fails, because the link `303`s to
`www.youtube.com`. The last two serve `--assets download` only: media comes
from a per-request `rr9---sn-….googlevideo.com` host that only a wildcard can
express, and thumbnails from `i.ytimg.com`, which `*.youtube.com` does not
cover (`000`, measured). Reference mode records both URLs and fetches
neither.
