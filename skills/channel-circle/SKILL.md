---
name: channel-circle
description: Circle.so capture for this wiki — persistent-profile Chrome, HLS/Wistia media, captions, outage probe.
argument-hint: --stage harvest|process --capture-dir <dir> [--url <url>]
user-invocable: false
---

# Channel: Circle

You harvest Circle.so-hosted communities for this wiki. You are normally
dispatched by the harvest worker loop: a claimed job carrying `skill:
channel-circle` means its watch named this skill, and this file is
authoritative for how the venue is captured. The job already carries the
resolved config — `min_date`, `access`, `assets`, tags, areas — honor it;
never re-ask.

This copy is wiki-owned. Improve it as you learn the venue (new
fingerprints, changed selectors, corrected routes) — that is the intended
lifecycle, and `skills list` reporting it as diverged is provenance, not a
problem. Keep claims about the pipeline's own scripts out of here; those go
to the human via the run report.

Circle.so is a hosted community/course platform (React SPA, Rails backend).
Communities run on `*.circle.so` or a **custom domain** (e.g.
`community.example.com`), so fingerprint on assets/cookies, not the
hostname.

## Stages

You are invoked by name through the Skill tool, not read as a document, and
`--stage` says which half of the pipeline is calling:

- **`--stage harvest`** — a claimed job carries `skill: channel-circle`. Capture
  the lesson into `--capture-dir` with the bundled script below. The job already
  carries the resolved config — `min_date`, `access`, `assets`, tags, areas —
  honor it; never re-ask.
- **`--stage process`** — the capture is on disk. This venue has no deterministic
  builder: the generic scaffolder makes the note, and your job is the venue
  knowledge below — which chrome to strip, how captions and HLS media are
  referenced, what a paywalled or errored lesson looks like.

Chaining to another unit? Invoke it **by name through the Skill tool** — never
read a sibling's SKILL.md and improvise its behavior from what you read.

## Capture (per-lesson jobs)

Run the bundled capture script instead of a generic Playwright fetch:

```
llm-wiki-ops run ops/skills/channel-circle/scripts/capture_lesson.py <root> <lesson-url> --out <capture-dir> \
    [--headed] [--timeout-ms 45000]
```

- Paths are wiki-relative, because an invoked skill runs from the wiki root;
  `<capture-dir>` is the
  job's own `capture_dir` — host-derived, already inside the watch's slice.
- Dumps `page.html` (rendered DOM — asset-discovery ground truth),
  `net.json` (the stream-URL network log), `meta.json` (title, final URL,
  discovered sidebar links), and resolves in-page `<track>` captions to
  `captions/<lang>.vtt` while the page is live — blob: track srcs are
  unreachable after the browser session ends.
- Exit 2 = no auth profile yet, or auth expired (landed on a sign-in page);
  exit 3 = a Cloudflare challenge that did not clear. Both mean re-run the
  login helper (below), not retry the capture. Exit 5 = the credential
  store itself could not be reached (denied/unreadable) — a real failure,
  not "no profile yet"; re-running the login helper will not fix it.
- The script is pure capture I/O: run `to_markdown.py` and `assets.py` from
  the plugin's scripts on its outputs as usual.

## Auth (one-time, per domain)

- One-time manual login via the plugin's login helper, reached through the
  wiki's front door — run it FROM the wiki root (the helper takes the
  current directory as the wiki root, and resolves the credential store
  from there):
  `llm-wiki-ops run scripts/login.py <domain>` — saves the
  `<domain>.storage` credential AND a persistent Chrome profile named
  `<domain>`.
- **Cloudflare-fronted — the profile is what replays, and the
  `<domain>.storage` credential is deliberately unused here.** A fresh
  bundled-Chromium context gets a Turnstile challenge; real Chrome
  (`channel="chrome"`) with the persistent per-domain profile clears it,
  because the profile carries the `cf_clearance` the human's login earned
  and that clearance is fingerprint-bound. `capture_lesson.py` therefore
  launches the persistent profile and never loads the bare
  `<domain>.storage` credential into fresh Chromium — login still writes it
  (other tools consume it), but on Circle it does not authorize anything by
  itself.
- Login sets cookies on `.circle.so` **and** the community's own domain.

## Venue knowledge

### Fingerprints

- Asset hosts `assets-v2.circle.so` and
  `app.circle.so/rails/active_storage/...` (Rails ActiveStorage image
  proxy) referenced in page HTML.
- Lesson URL shape `/c/<space-slug>/sections/<section-id>/lessons/<lesson-id>`;
  login flow `/users/sign_in` → `/sign_in#email`, page title
  "Sign in | <Community>".
- Video streamed as HLS from `cdn-media.circle.so`.
- On a custom apex domain, cookies land on both `.circle.so` and the custom
  domain — fingerprint on asset hosts, not hostname.

### Discovery

- The lesson page's sidebar lists the whole section curriculum as
  `/c/<slug>/sections/<id>/lessons/<id>` links; the body shows "Topic N of
  M", giving an expected count to check completeness against. Extract the
  tree at once for section-scoped runs rather than daisy-chaining "next".
- The **space root page** (`/c/<slug>`) also renders all lesson links in
  its DOM when the backend is healthy — enumeration can come from the
  root, not only a lesson page. Each `<a>`'s text carries the lesson title
  + duration (e.g. "…Example Lesson\n\n04:07"), a free title source for
  `capture.json`.
- No JSON archive/sitemap API confirmed — enumeration observed so far is
  via the rendered sidebar (whether a headless API exists is unverified).

### Dates

- Not yet needed on observed runs (course lessons, no `min_date` filter);
  where publish/created dates live is still to be determined.

### Access / paywall

- Gated content redirects unauthenticated requests to `/users/sign_in`.
  With a valid session the lesson renders fully. No free/paid split
  observed within a course — licensing is community-wide.

### Content extraction

- React SPA: `page.goto(..., wait_until="commit")` then wait on a content
  selector (`main` / `[class*="lesson"]`) plus a short fixed pause.
  Waiting on `domcontentloaded`/`networkidle` times out (~45s) — long-poll
  + analytics beacons never idle. `capture_lesson.py` implements this.
- `to_markdown.py` with **no** `--selector` extracts the lesson body
  cleanly (title, "Topic N of M", headings, pull-quote, bullets); an
  explicit content selector has not been needed.
- Junk to expect: the rendered page carries many chrome images (sidebar
  course thumbnails, member avatars, reaction icons) that show up as
  `image` candidates but never appear in the lesson markdown. For a
  text+video lesson the only real asset is the HLS video — don't
  bulk-download the chrome images.
- Not every lesson is text+video-only: some attach downloadable files
  (e.g. PDF templates) via a "Resources" block in the body, served from
  `https://assets-v2.circle.so/<opaque-hash>` with **no file extension in
  the URL**. Extensionless URLs are easy to miss in extension-keyed asset
  detection, so verify the manifest actually lists the Resources files —
  grep `page.md` for `assets-v2.circle.so` links and add any missing ones
  by hand as `type: "file"`; download resolves the real extension from the
  content type.

### Media

- HLS master playlist at
  `https://cdn-media.circle.so/bcdn_token=<tok>&token_path=%2F<uuid>%2F&expires=<ts>/<uuid>/hls/playlist.m3u8`
  (BunnyCDN signed-token style; `playlist_N.m3u8` are renditions). The
  `.m3u8` URL appears **only in the network log (XHR), never in the DOM** —
  capturing the network log is mandatory, which `capture_lesson.py` does.
- The DOM does carry an entity-encoded (`&amp;`) copy of the HLS URL —
  keep the clean network-log URL, drop the entity-encoded duplicate; the
  encoded form does not fetch.
- Download with yt-dlp on the master playlist. **No special referer/Origin
  header required** — the signed token alone authorizes the fetch.
- `cdn-media.circle.so` can be transiently slow: a first-attempt HLS
  timeout that succeeds on immediate retry is normal, and under concurrent
  load (2 workers sharing the domain) expect roughly one retry per lesson.
  Always retry once before burning failure budget or diagnosing an outage.
- Not every video is native Circle HLS: some sections embed via **Wistia**
  (`fast.wistia.net/embed/iframe/<media-id>`). A single Wistia embed's
  network log surfaces 4 stream-shaped URLs for the *same* video, not 4
  videos: keep `fast.wistia.com/embed/medias/<media-id>.m3u8` (the true
  master, keyed to the iframe's media id); drop
  `embed-cloudfront.wistia.com/deliveries/<hash>.m3u8` entries (per-
  rendition adaptive-bitrate manifests the player already selected from
  the master) and anything ending in `.ts` (raw segment fragments). One
  master per player/media id.
- No DRM observed.

### Captions

- Lesson videos carry in-page `<track>` captions that `capture_lesson.py`
  resolves to `captions/en.vtt` in-browser. Every lesson of one observed
  section had an `en` track, so `--transcript if-no-captions` enqueued
  zero generated-transcript jobs.
- Headless capture (persistent `channel="chrome"` profile, no `--headed`)
  renders the SPA and fires the HLS XHR fine **when Circle's backend is
  healthy** — headed is not required for the render itself.

## Outage / health signal

Circle backend outages present as an authenticated app *shell*: no
`/sign_in` redirect, `cf_clearance` valid, but `internal_api/spaces?include_sidebar=true`
(and related `internal_api/*`) return **HTTP 500**, the
`.standard-layout-v2__content-wrapper` div renders **empty** (an
access/licensing block would instead show an upgrade CTA), page `<title>`
stays the generic community name, and no lesson links / HLS XHR appear.
The status page can be all green throughout. Fix signal: the wrapper gains
children AND the 5xx count hits 0. Probe with the bundled script
(diagnostic only — not part of the worker loop):

```
llm-wiki-ops run ops/skills/channel-circle/scripts/outage_probe.py <root> <course-url> [--settle-ms 8000]
```

Always exits 0 and prints a JSON verdict; `fixed` means auth is OK, no 5xx
was seen, and the content wrapper has real children. Use it to gate a
harvest behind an outage.
