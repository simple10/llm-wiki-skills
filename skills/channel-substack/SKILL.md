---
name: channel-substack
description: Substack capture for this wiki — archive-API enumeration, paywall split, per-post extraction.
argument-hint: --stage harvest|process --capture-dir <dir> [--url <url>]
user-invocable: false
---

# Channel: Substack

You harvest Substack newsletters for this wiki. You are normally dispatched
by the harvest worker loop: a claimed job carrying `skill: channel-substack`
means its watch named this skill, and this file is authoritative for how the
venue is enumerated and captured. The job already carries the resolved
config — `min_date`, `access`, `assets`, tags, areas — honor it; never
re-ask.

This copy is wiki-owned. Improve it as you learn the venue (new
fingerprints, changed selectors, corrected routes) — that is the intended
lifecycle, and `skills list` reporting it as diverged is provenance, not a
problem. Keep claims about the pipeline's own scripts out of here; those go
to the human via the run report.

## Stages

You are invoked by name through the Skill tool, not read as a document, and
`--stage` says which half of the pipeline is calling:

- **`--stage harvest`** — a claimed job carries `skill: channel-substack`. For a
  listing/archive job, enumerate with the bundled script below; for a post job,
  capture into `--capture-dir` per Content extraction. The job already carries
  the resolved config — `min_date`, `access`, `assets`, tags, areas — honor it;
  never re-ask.
- **`--stage process`** — the capture is on disk. No deterministic builder here:
  the generic scaffolder makes the note, and your job is the venue knowledge
  below — subscribe CTAs and footer chrome to strip, paywall teasers to reject.

Chaining to another unit? Invoke it **by name through the Skill tool** — never
read a sibling's SKILL.md and improvise its behavior from what you read.

## Enumerate (listing/archive jobs)

Run the bundled enumerator instead of fetching archive pages:

```
llm-wiki-ops run ops/skills/channel-substack/scripts/enumerate_archive.py <domain-or-archive-url> \
    --slug <job.slug> --parent <job.id> \
    [--min-date <job.min_date>] [--access <job.access>] [--max-urls N] \
    [--max-date <resume ceiling>]
```

- **It queues nothing.** It emits, and the host applies — you run no queue
  verb here any more than anywhere else in a slice. It takes no wiki root
  and no intake path, because it touches neither.
- Its stdout is `{"discovered": {...}, "summary": {...}}`. **Copy the
  `discovered` object verbatim into your report's `discovered` array** —
  it is already the row shape (`parent`, `watch_id` — the report field
  name; it carries the slug — `urls`), and the host
  refuses a row naming any parent it did not dispatch or any watch but that
  job's own. Do not rebuild it and do not merge `summary` into it.
- Child jobs still inherit scope, filters, tags and assets mode from the
  watch entry, and intake still applies the denylist, the watch's
  `exclude_urls`, its scope prefix and the seen ledger. That work moved
  behind the host's `apply`, it did not go away.
- **The watch MUST be `scope: domain`**: the default `page` scope rejects
  every queued URL as `out_of_scope` while the run still exits 0, so a
  page-scoped watch silently harvests nothing. **Where you see that changed**:
  it used to show here as `queued: 0` with `intake_reasons` full of
  `out_of_scope`. Those are intake's counts and intake now runs host-side,
  so the signal is in `harvest_apply.py apply`'s output — the `reasons` on
  your discovered row. Check the watch's scope up front rather than waiting
  for it, and flag it in your run report.
- `--access free` (the default) emits only `audience: everyone` posts; paid
  posts are counted in the summary, never fetched. After the owner
  subscribes, re-run with `--access licensed`.
- **A big archive is bounded, and truncation is not a failure.** A report
  carrying more than 2000 discovered URLs is refused WHOLE — every job in
  your slice goes back to `pending/`. So the enumerator stops at
  `--max-urls` (default 2000), sets `summary.truncated`, and names
  `summary.resume_max_date`: the oldest post it kept. Report what you have,
  and take the rest on a later pass with **`--max-date <that value>`**.
  **Not `--min-date`** — that is a floor on a walk which always restarts at
  `offset=0`, so lowering it re-emits the same posts and makes no progress
  at all. `--max-date` is inclusive, so the boundary post comes back once
  and intake's seen ledger drops it. If anything else in your slice also
  discovers, lower `--max-urls` — the ceiling is across every row in one
  report, not per row.
- **`summary.stalled` means the walk cannot continue and you must act.** The
  ceiling only moves if the emission spans more than one date, so an archive
  with `--max-urls` or more posts on the boundary date returns the same set
  every pass — the same silent shape as resuming with `--min-date`. Raise
  `--max-urls` above the number sharing that date and re-run; do not re-run
  unchanged, and say so in your run report.
- Report the enumeration job itself as `{"id": <job.id>, "outcome":
  "complete", "capture_dir": "<dir>", "handoff": false}` — a listing page is
  provenance, not a note — with the summary JSON recorded in its
  `capture.json`.

## Venue knowledge

### Fingerprints

- Newsletters live on `<name>.substack.com` or custom domains that still
  load `substackcdn.com` assets.
- Posts at `/p/<slug>`; archive at `/archive`; `window._preloads` JSON blob
  in page source.

### Discovery

- **Archive API**: `GET /api/v1/archive?sort=new&offset=<n>&limit=<n>` —
  enumerates the full archive without fetching a single post page
  (verified on a 597-post archive). Items carry `post_date` (ISO) and
  `audience`, so date and access filters run before any page fetch.
- **Pagination trap**: `offset=0` silently caps the response at 23 items
  regardless of the requested `limit`; `offset=1`+ return full pages. A
  "stop when `len(page) < limit`" loop misreads the truncated first page as
  the end and can miss ~96% of an archive. Advance the offset by the actual
  page length; stop only on a truly empty page. The bundled enumerator
  implements this.
- HTML fallback: `/archive?sort=new` with infinite scroll (unverified).

### Dates

- Archive API: `post_date`, ISO format (verified).
- Post page fallbacks: `<meta property="article:published_time">`, JSON-LD
  `datePublished`.

**A post's `post_date` is the protocol's `published`**, and on this venue you
get it for free: `enumerate_archive.py` reads `post_date` only to window
discovery (`--min-date`), and the note itself comes from the generic scrape
path, where `scaffold` runs the `published` ladder over the captured
`page.html` — whose first two rungs are exactly the two post-page fallbacks
above. So do not hand-carry a date into `capture.json` here; verify instead
that the staged note came out with a `published:` line, and treat its absence
on a post page as a capture problem (wrong content root, a paywall shell)
rather than as a missing field.

### Access / paywall

- `audience` values: `everyone` (free), `only_paid`, `founding` — note the
  top tier is literally `"founding"`. Observed split on one live archive:
  38 free / 505 paid / 54 founding — free-only harvest is viable and worth
  doing without a subscription.
- Paid posts without a session render a free preview then a paywall block
  ("This post is for paid subscribers"); JSON-LD `isAccessibleForFree:
  false`. Never capture the truncated preview as if complete.

### Content extraction (per-post capture jobs)

- Main body in `.available-content` / article markup; usually static enough
  for Firecrawl without Playwright (confirm on first capture).
- Comments are separate; not part of the article body.
- `og:title` does not put `property` first
  (`<meta data-rh="true" property="og:title" …>`) — match attributes
  order-independently, and HTML-unescape properly (`&#x27;`, `&amp;`); a
  naive anchored regex missed 38/38 titles in one batch.
- The meta-derived title can diverge from the on-page headline
  (`<h1 class="header-anchor-post">`) — clickbait/email-subject variant vs
  the calmer real title. When both headings appear in the extracted body
  (meta-title-as-H1, short author intro, CTA line, `---`, real H1), use the
  on-page H1 as the true title, fold the intro into the body, drop the
  duplicate heading and the CTA line.
- Each post carries one custom per-post illustration adjacent to a
  subscribe/share CTA sentence inside `.available-content`. It looks like
  CTA chrome but is real artwork, and `alt` text is unreliable (`null` or
  `"og-image"`). Strip only the CTA sentence; open the image before
  deleting anything adjacent to it.

### Media

- Images proxied via `substackcdn.com/image/fetch/…` — the original URL is
  embedded in the fetch path; downloading the CDN URL works. These URLs
  contain commas (`/w_424,c_limit,f_webp/`), so srcset handling must not
  split on commas.
- Podcast posts: `<audio data-testid="audio-element"
  src="https://api.substack.com/api/v1/audio/upload/…">` sits OUTSIDE
  `.available-content`, so a selector-scoped extraction never mentions the
  audio in the body even when the asset downloads — check the capture's
  asset list for `type: audio` and embed it in the note deliberately. The
  upload endpoint serves real MP3 bytes with `Content-Type: text/plain`
  and no path extension.
- Native video player for video posts; no DRM observed on standard tiers.
- Posts repeat the same banner/avatar/CTA images heavily — one 39-capture
  batch held 643 asset references but only 154 distinct files (76%
  reduction), all by URL match. The watch's own deduped asset store (the
  job's `dirs.assets`) is the right default here.

### Auth

- Magic-link email login; Playwright storage state works once logged in.
- Custom-domain newsletters may not share `substack.com` session cookies —
  save storage state per domain.
