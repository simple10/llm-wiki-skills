# channel-hubspot-video — after enabling

1. **A browser, before the first run.** `capture_hubspot_video.py render`
   launches Chromium through Playwright INSIDE the harvest slice. `run`
   resolves the Playwright package like any script dependency; the browser
   BUILD it drives is a separate download into `~/.cache/ms-playwright`
   (`~/Library/Caches/ms-playwright` on a Mac), and a slice can never make it:
   both paths are on the slice's write-deny floor
   (`schedule/runner/floor.py::DENY_WRITE_OUTSIDE`). So the operator installs
   it on every machine that harvests this skill, outside any jail, with the
   Playwright version the script resolves (`playwright>=1.44`) — e.g.
   `uvx playwright install chromium`. Unverified, because no live slice has
   run this skill since the rebuild: that the harvest sandbox's read on that
   cache is enough,
   that the uv-cached build and the `uvx` one agree on a browser revision, and
   that Chromium's own sandbox starts under the jail. If the first render dies
   on a missing executable, that is this step, not the site.
2. **Pin the site's host.** In the wiki's copy of `manifest.json` add
   `host:<the site's real domain>` to `keywords` — from then on
   `skills search <domain>` answers from the wiki's copy directly. That is the
   only host to add: the platform's own — `play.hubspotvideo.com`,
   `image.mux.com`, `stream.mux.com` and `*.mux.com` (the rendition hosts
   behind the master playlist; the exact set is unverified) — ship in the
   harvest stage's sandbox reference, because they are the same for every
   HubSpot customer. A slice reaches the job's target host plus its bound
   sandbox and nothing else; a host still missing comes back in the report's `missing[]`
   as `denied`. Add no other key: `skills doctor` fails a manifest carrying
   one the contract does not name.
3. **First capture, then selectors.** Run one capture, read the RENDERED
   `page.html`, and fill the site's entry in the wiki's copy of
   `references/sites.json`, keyed by host (`content_selector`,
   `drop_selectors`, `title_selector`, and `strip_params` / `exclude_urls`
   where the site needs them). That file — not the manifest — is where both
   steps read the site's rules: `to_markdown.py` takes the selectors at
   process, `leaves.py plan` takes `strip_params` and `exclude_urls` at
   harvest. `content_selector` is the real content root. `drop_selectors` are
   the theme chrome that would otherwise be the bulk of every page — module
   navs, CTA blocks, lead forms, legal disclaimers — removed at the DOM level,
   the only point where they are still tellable from prose. `title_selector`
   is the page's true title: set it whenever the site reuses one `<title>`
   across a section, which HubSpot sites commonly do. `strip_params` and
   `exclude_urls` are query parameters beyond `hsLang` that do not change the
   page, and URL globs this site should never yield. Check the selectors
   without the network by running `to_markdown.py` over a `page.html` already
   on disk.
4. **Record site traps in the wiki's SKILL.md** as they surface, under the
   site's own heading: the section map, why each selector is what it is, what
   a pointer page looks like there. Traps a SCRIPT must act on go where a
   script reads them — a param to strip beyond `hsLang` or a URL tree this
   site should never yield in `references/sites.json`, a legacy section one
   job should skip in that job's `harvest.exclude_urls`. The copy is
   wiki-owned; site knowledge belongs in it, not in your head or the run
   report. **Then have the operator re-enable the skill** —
   `/llm-wiki:enable channel-hubspot-video` — because `run` and the
   slice serve the ENABLED copy, and steps 2–4 edited the wiki's.
5. **Declare the job.**
   `llm-wiki-ops pipeline add <section-root-url> slug=<site-section>
   description="<what this is>" skill=channel-hubspot-video` — the skill's
   manifest supplies `every=once`, `harvest.scope=section`,
   `harvest.assets=download`, `transcribe.when=always` (the video IS the
   content — keep it) and a `dest` of `sources/courses/<slug>`. Point it at
   the section ROOT, not a leaf page — the manifest's `watch.note` says why:
   the skill's own filter (`leaves.py plan`) takes the section prefix from the
   job's target, so under a leaf-rooted job every sibling is skipped as out of
   scope — and the run reports `failed`, naming `harvest.scope` and the
   target, rather than closing the job with nothing captured.
6. **Expect to continue it by hand.** Videos run ~28 minutes a page, a slice
   is killed at thirty minutes, and one run attempts six pages by default, so
   a section reports `partial` many times. `every=once` — the default above —
   is NOT pulled again after a `partial`: either
   `llm-wiki-ops pipeline queue retry <ticket>` (three attempts a ticket), or
   declare the job with a period while it fills (`every=1h`, at `pipeline add`
   or `llm-wiki-ops pipeline edit <slug> every=1h`) and set `every=once` when
   a run reports `skipped`. The report's `reason` says the same.
