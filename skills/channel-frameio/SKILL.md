---
name: channel-frameio
description: Frame.io guest-share capture for this wiki — tree enumeration, per-job asset capture, document notes.
argument-hint: --stage harvest|process --capture-dir <dir> [--url <url>]
user-invocable: false
---

# Channel: Frame.io

You harvest Frame.io guest shares for this wiki. You are normally
dispatched by the harvest worker loop: a claimed job carrying `skill:
channel-frameio` means its watch named this skill, and this file is
authoritative for how the venue is enumerated and captured. The job already
carries the resolved config — honor it; never re-ask.

This copy is wiki-owned. Improve it as you learn the venue (new
fingerprints, changed selectors, corrected routes) — that is the intended
lifecycle, and `skills list` reporting it as diverged is provenance, not a
problem. Keep claims about the pipeline's own scripts out of here; those go
to the human via the run report.

**Dependencies**: `yt-dlp` on PATH (video capture hands the HLS master
straight to it) and real Chrome for the Playwright launches.

**The watch MUST be `scope: domain`.** This unit queues nothing itself —
it emits a `discovered` row and the HOST's intake applies it — and intake
enforces the watch's scope prefix on every discovered URL exactly as it
always did. The default `page` scope rejects every leaf as `out_of_scope`
while the run still exits 0, so a page-scoped Frame.io watch silently
harvests nothing. `section` is not enough either: the enumerator accepts a
FOLDER inside a share as its root, and a leaf viewer is
`/share/<share-id>/view/<asset-id>` — not under that folder's
`/share/<share-id>/<folder-asset-id>` prefix — so a folder-rooted section
watch rejects every leaf too. Only `domain` holds for every share URL the
enumerator accepts. **Where you see that changed**: this used to surface
here as the enumerator's own `out_of_scope` counts; intake runs host-side
now, so the signal is in `harvest_apply.py apply`'s output — the `reasons`
on your discovered row. Check the watch's scope up front rather than waiting
for it, and flag it in your run report. The install notes and the watch
example below both carry `--scope domain`.

## Stages

You are invoked by name through the Skill tool, not read as a document, and
`--stage` says which half of the pipeline is calling:

- **`--stage harvest`** — a claimed job carries `skill: channel-frameio`, and
  its URL says which of two shapes it is. A **share or folder root**
  (`next.frame.io/share/<share-id>[/<asset-id>]`): enumerate the tree once and
  emit its leaves for the host to queue — steps 1 and 2 below. A **leaf**
  (`.../view/<asset-id>`): capture that one asset — step 3. You run no queue
  verb in either case; you write bytes plus rows in `report.json`, and the
  host applies your rows.
- **`--stage process`** — the capture is on disk. Document assets (pdf/pptx/xlsx)
  have a deterministic builder in this unit:

  ```
  llm-wiki-ops run ops/skills/channel-frameio/scripts/frameio_doc_note.py <root> --capture-dir <dir>
  ```

  Video captures take the generic path plus the Media notes below.

Chaining to another unit? Invoke it **by name through the Skill tool** — never
read a sibling's SKILL.md and improvise its behavior from what you read.

## How a share harvest runs

Guest shares (`next.frame.io/share/<share-id>`) need no login — the share
link itself authorizes. The flow is: enumerate once, emit the leaves for the
host to queue, capture each dispatched leaf on its own job, then scaffold
notes for document assets. Paths are wiki-relative — an invoked skill runs
from the wiki root.

**Nothing here runs a queue verb.** No `intake.py`, no `job.py claim`, no
`complete`, no drain. A worker writes bytes into its own capture dir plus
rows in `report.json`, and the host applies them — which is what gets your
`parent` checked against the jobs it dispatched and your `watch_id` (the
report field name; it carries the slug) against that job's own.

### 1. Enumerate the tree (once, before any downloads)

```
llm-wiki-ops run ops/skills/channel-frameio/scripts/enumerate_tree.py <share-url> --out tree.json
```

Walks folders via `data-asset-id` routing (no clicks) and emits a flat
manifest of every leaf: asset id, display name, folder path, view URL.

### 2. Emit the leaves for the host to queue

```
llm-wiki-ops run ops/skills/channel-frameio/scripts/harvest_share.py tree.json \
    --slug <job.slug> --parent <job.id> [--skip N]
```

- **It queues nothing.** It takes no wiki root, no `--intake` and no `--job`,
  because it touches none of them.
- Its stdout is `{"discovered": {...}, "summary": {...}}`. **Copy the
  `discovered` object verbatim into your report's `discovered` array** — it
  is already the row shape (`parent`, `watch_id` — the report field name;
  it carries the slug — `urls`), and the host
  refuses a row naming any parent it did not dispatch or any watch but that
  job's own. Do not rebuild it and do not merge `summary` into it.
- The leaves still inherit scope, filters, tags, areas and assets mode from
  the watch entry, and intake still applies the denylist, the watch's
  `exclude_urls`, its scope prefix and the seen ledger. That work moved
  behind the host's `apply`, it did not go away — re-runs still skip what
  already landed.
- Report the share job itself as `{"id": <job.id>, "outcome": "complete",
  "capture_dir": "<dir>", "handoff": false}` — a share listing is
  provenance, not a note — with the summary JSON recorded in its
  `capture.json`.
- **A big share is bounded, and truncation is not a failure.** A report
  carrying more than 2000 discovered URLs, or more than 256 KiB in total, is
  refused WHOLE — every job in your slice goes back to `pending/`. Frame.io
  leaf URLs are ~106 bytes each and every share emits them at full length,
  so the BYTE bound is the one that usually bites: the emitter stops at
  `--max-bytes` (default 192 KiB) or `--max-urls` (REQUIRED — the host's
  ceiling, `limits.max_discovered_urls` in your assignment), sets
  `summary.truncated`, names which bound in `summary.bound`, and gives
  `summary.resume_skip`. Report what you have, and take the rest on a later
  pass with **`--skip <that value>`**. Re-emitting from the start makes no
  progress: the cap is on the REPORT, not on what intake queues, so the same
  tail is dropped again.

### 3. Capture one dispatched leaf

A leaf job (`.../view/<asset-id>`) is one asset and one report row:

```
llm-wiki-ops run ops/skills/channel-frameio/scripts/capture_job.py <root> \
    --assignment <path-to-assignment.json> --job-id <job.id>
```

- It wraps `capture_asset.py` for exactly that job: opens the viewer, takes
  the HLS master or the signed document proxy out of the network log,
  downloads it, and writes `capture.json` beside it.
- The capture dir is the job's own `capture_dir` — host-derived, already
  inside the watch's `_raw/<slug>` slice — and checked against the
  assignment's granted `slices` BEFORE anything is fetched, because a path
  under no granted slice is refused when the host applies your report and
  by then the bytes are already down.
- Its stdout is `{"job": {...}, "summary": {...}}`. **Copy the `job` object
  verbatim into your report's `jobs` array.** `title`, `verdict` and the
  asset summary are not report fields — the host reads them out of the
  `capture.json` this wrote.
- Exit 1 means the capture failed and the `job` row is a `fail` row: report
  it, and the job retries. Exit 2 means the job id is not in that assignment
  or the derived dir is outside every granted slice — nothing was fetched
  and there is no row to report.
- **A dispatched leaf job carries a URL and nothing else**, so the asset's
  original filename and its folder breadcrumb — both of which live in the
  leaf manifest from step 1 — do not reach it. Pass `--name`/`--path` only
  when you actually have that manifest in hand; without them the slug comes
  from the URL's own asset id and the document extension comes off the
  signed proxy route. The note step below is written for both cases.

### 4. Scaffold notes for document assets

```
llm-wiki-ops run ops/skills/channel-frameio/scripts/frameio_doc_note.py <root> --capture-dir <dir> \
    [--group "<bundle name>"] [--group-type <kind>] [--author NAME] \
    [--title-strip "<share suffix>"]
```

`--author`/`--group`/`--group-type` are the scoping dimensions, and each
defaults from the capture record when the watch already declared it — so pass
them only to override. `group_type` is an open vocabulary (`course`,
`playlist`, `series`, `newsletter`, `docs`, `thread`, …). The note comes out
with the same `source_host:`/`areas:`/`tags:` shape a generic scrape note has,
so the same filters reach it.

Frame.io captures have no page.md, so the generic scaffolder can't build
these. Copies the doc into the bundle's `assets/docs/` as
`<url-hash8>-<original-name>.<ext>` (the original name from `meta.json` —
every raw capture is `document.<ext>`, so unsanitized copies would
collide), writes frontmatter + TODO-SUMMARY, and inlines extracted text for
pptx/xlsx. Video assets flow through the normal media stage instead. Use
`--title-strip` when the share appends its own name to every asset title.

When step 3 ran without `--name`, `meta.json` carries no original name and
the copy falls back to `document`; the URL hash still keeps the filenames
apart, and the note's title comes from the captured page title either way.
`capture.json`'s `path` is empty for the same reason, so the breadcrumb line
is simply absent — not wrong.

## Venue knowledge

### Fingerprints

- Guest share URLs: `next.frame.io/share/<uuid>`; folders append
  `/<asset-id>`, leaf viewers `/view/<asset-id>`.
- Listing rows are `[data-testid="asset-panel-grid-asset-card"]` divs
  carrying `data-asset-id`; a `.folder-svg` child marks a folder.

### Discovery

- The whole tree is walkable by URL construction from `data-asset-id` — no
  clicking, no API. Leaf cards prefix a duration or page-count badge
  before the real filename; prefer the line ending in a file extension.
- Enumerate once up front; the tree does not change mid-run for a
  `mode: once` share.

### Access / paywall

- Guest shares need no auth — the share link authorizes everything under
  it. No login helper involvement, no free/paid split.

### Media

- Video streams over HLS: a `sahls.frame.io/encode-hls/.../main.m3u8`
  master (JWT-signed) requested once the player mounts — network log only,
  never the DOM. `capture_asset.py` watches for it and hands it to yt-dlp.
- Documents (pdf/pptx/xlsx/mht) render via signed proxy conversions
  (`assets.frame.io/.../*_proxy.<ext>?...`) — also network-log-only; the
  signed URL alone authorizes the download.
- A page that renders neither within the watch window exits 2 — usually
  the SPA changed and `capture_asset.py` needs updating; say so in the run
  report.

### Naming

- Every document downloads as `document.<ext>`; the real filename lives in
  the leaf manifest (`name`), and reaches the capture's `meta.json` only
  when the capture was given `--name`. A dispatched leaf job has no
  manifest, so plan for `name: null` there. Anything that copies documents
  out of captures must therefore key on the source URL, not on the file
  name, or every PDF collides on `document.pdf`.
- Without a `--name` the extension comes off the signed proxy route
  (`.../<kind>_proxy.<ext>?…`), which names what Frame.io CONVERTED the
  asset to rather than what was uploaded. Where the two differ, the
  captured bytes are the conversion — trust the extension, not the title.
