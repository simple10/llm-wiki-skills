---
name: channel-hubspot-video
description: HubSpot CMS pages whose content is a lazy-loaded HubSpot Video (Mux underneath) rather than text — a static fetch finds no player at all.
argument-hint: --stage harvest --capture-dir <dir> [--url <url>]
user-invocable: false
---

# Channel: HubSpot Video

You harvest a site built on **HubSpot CMS** whose pages carry **HubSpot Video**
players. This file is authoritative for how that platform is captured. The job
already carries the resolved config (scope, assets, transcript policy) — honor
it; never re-ask the operator.

This unit is **platform-general, not site-specific.** Every HubSpot CMS site
runs on its own domain and themes its own markup, so `match.hosts` is empty and
`custom_domains: true` — `skills find` reports this unit under `possible[]` for
an unmatched host, and the *Fingerprints* section below is how you settle
whether it applies. Once installed, the copy is **wiki-owned**: put the site's
own selectors, URL map and exclusions in it, and `skills list` reporting it as
diverged is provenance, not a problem.

## Stages

- **`--stage harvest`** — a claimed job carries `skill: channel-hubspot-video`.
  Capture the URL into `--capture-dir` with `scripts/capture_hubspot_video.py`
  and the platform knowledge below.
- **`--stage process`** — not declared. The generic scaffolder builds the note;
  give it your site's `extract` block (below) so it strips the theme's chrome.

## Fingerprints — is this venue HubSpot CMS?

`possible[]` sent you here on a host nobody enumerated. Confirm before
installing:

- Assets served under `/hubfs/` or `/hs-fs/hubfs/`.
- `hubspot` in the page scripts; `_hcms/` paths disallowed in `robots.txt`.
- Internal nav links carrying `?hsLang=<lang>`.
- A player iframe at `play.hubspotvideo.com/v/<portal>/id/<video>`.

Any two of those together is conclusive. The last one alone means this unit's
*Media* section applies even if the rest of the site is not HubSpot-themed.

## Discovery

`<host>/sitemap.xml` is typically one flat `urlset` covering the whole site —
the enumeration route worth using. Page sidebars are frequently partial or
carry stale spellings, so prefer the sitemap and let the sidebar be a
cross-check.

Strip `?hsLang=` from discovered URLs before queueing, or the same page is
queued twice under two URLs. The wiki's watch should also carry an
`--exclude-url '*hsLang=*'`.

## Media — the reason this unit exists

1. **The iframe is lazy.** The markup carries `data-hsv-src`, never `src`. A
   curl or Firecrawl fetch therefore finds *no player at all* — asset
   detection returns images only, and the capture looks complete while missing
   its entire content. **Rendering is mandatory**, not an optimization.
2. **HubSpot Video is Mux underneath.** After render, the network log holds
   `image.mux.com/<PLAYBACK_ID>/storyboard.vtt` — that request fires even
   before play is clicked, and is the most reliable place to read the id.
3. **The manifests the player fetches are signed and expiring.**
   `manifest-*.edgemv.mux.com/…/rendition.m3u8` carry `expires=` and are
   per-rendition. Do not download those — they die in hours. Rewrite to the
   stable master `https://stream.mux.com/<PLAYBACK_ID>.m3u8`, which does not
   expire and offers every rendition to yt-dlp.
4. **No DRM, and typically no `<track>` captions.** `captions/` stays empty and
   the generated transcript is the page's only text — which is why the watch
   runs `transcript: always` and why `assets: download-audio` is worth
   considering when only the transcript is wanted.
5. **Drop `verifi.podscribe.com/tag`** from the asset manifest: an analytics
   beacon the generic detector types as an image.

A page with **no player at all** is a real shape, not a failure: a pointer page
whose payload is an external link (a podcast host, a PDF, a YouTube mirror).
Record the external URL in `capture.json` and complete the job rather than
flagging it forever.

## Content extraction — set this per site

HubSpot themes vary completely between customers, so this unit ships no
selectors. After the first capture, put the site's own rules in **this
installed copy's** `manifest.json`, where both pipeline stages read them:

```json
"extract": {
  "content_selector": "main#main-content",
  "drop_selectors": [".cta-block", ".legal-disclaimer", ".course-modules"],
  "title_selector": "main#main-content h2"
}
```

- **`content_selector`** — the page's real content root.
- **`drop_selectors`** — theme chrome that would otherwise be the bulk of every
  note: repeated module navs, CTA blocks, lead forms, legal disclaimers. These
  are removed at the DOM level, before markdown conversion, which is the only
  point where they are still distinguishable from prose.
- **`title_selector`** — set it whenever the site reuses one `<title>` across a
  section, which HubSpot sites commonly do. Without it every page in a course
  is named identically, and `title` is an indexed field.

A thin `page.md` is expected here and is **not** a truncated capture — the
content genuinely is the video. Do not record a `partial` verdict for a short
body on this platform.

## Scripts

Resolve relative to this file; run from the wiki root.

- **`scripts/capture_hubspot_video.py render <url> --capture-dir <dir>`** —
  renders the page, clicks play, writes `page.html`, `net.json`, and
  `meta.json` (title, Mux playback id, stable stream URL). Exit 4 = rendered
  but no video resolved, which is the pointer-page case above.
- **`scripts/capture_hubspot_video.py patch-assets <assets.json> --meta
  <meta.json>`** — applies the *Media* rules to a manifest from the plugin's
  `assets.py detect`: drops the expiring `edgemv` manifests and the podscribe
  beacon, appends the stable Mux master.

## Budgeting

Video-first pages are much longer than they look. One measured corpus averaged
~28 minutes per page across 75 videos — 31.7 GB and ~35 hours of audio at
yt-dlp's default format pick. Estimate before committing to a section, and
prefer `assets: download-audio` when the transcript is the only thing wanted:
the same corpus would have been roughly 2 GB.

Cap concurrency at 2 workers with 2–5 s jitter, per the fan-out defaults in
`llm-wiki-ops reference pipeline` (`domain_limit`,
`request_delay_s`).

## Quirks log

- **2026-07-31** — Sites in this family have shipped misspelled sidebar links
  that 301 to the sitemap spelling. Following them works, but enumerating from
  the sitemap keeps capture directories and note names correctly spelled.
