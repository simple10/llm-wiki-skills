# channel-hubspot-video — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

This unit is a **platform template**: it ships the HubSpot CMS/Video
knowledge (lazy `data-hsv-src` player, Mux media path, signed-manifest
rewrite) and deliberately **no selectors** — every HubSpot customer runs its
own domain and its own theme. The wiki's copy turns site-specific the moment
you customize it.

0. **A browser, before the first run.** `capture_hubspot_video.py render`
   launches Chromium through Playwright INSIDE the harvest slice. `run`
   resolves the Playwright package like any script dependency; the browser
   BUILD it drives is a separate download into `~/.cache/ms-playwright`
   (`~/Library/Caches/ms-playwright` on a Mac), and a slice can never make it:
   both paths are on the slice's write-deny floor
   (`schedule/runner/floor.py::DENY_WRITE_OUTSIDE`). So the operator installs
   it on every machine that harvests this unit, outside any jail, with the
   Playwright version the script resolves (`playwright>=1.44`) — e.g.
   `uvx playwright install chromium`. Unverified, because no live slice has
   run this unit since the rebuild: that the slice may READ that cache (the
   floor's read list does not name it, so the machine-local
   `~/.config/llm-wiki/sandbox/base.jsonc` or `slice.jsonc` has to grant it),
   that the uv-cached build and the `uvx` one agree on a browser revision, and
   that Chromium's own sandbox starts under the jail. If the first render dies
   on a missing executable, that is this step, not the site.
1. **One copy, and every site in it.** `skills install` has no rename: the
   unit is `channel-hubspot-video` in every wiki, and identity is the JOB's
   slug. A second HubSpot-hosted site is a second job on this same unit, so
   keep each site's selectors and traps under its own heading in the
   installed SKILL.md rather than interleaved.
2. **Confirm the platform.** `skills search` answers a site's url with `no
   unit claims <host>` — no unit can enumerate every HubSpot customer's domain
   — so keywords, or a person, sent you here. Look for: assets under `/hubfs/`
   or `/hs-fs/hubfs/`; `hubspot` in the page scripts and `_hcms/` disallowed
   in `robots.txt`; nav links carrying `?hsLang=<lang>`; a player iframe at
   `play.hubspotvideo.com/v/<portal>/id/<video>`. Any two together is
   conclusive, and the last alone means the SKILL.md's *Media* section applies
   even where the rest of the site is not HubSpot-themed. A miss on all four
   means this is not the unit.
3. **Pin the site's host.** In the wiki's copy of `manifest.json` add the
   site's real domain to `requires.network` — from then on
   `skills search <domain>` answers from the wiki's copy directly. That is the
   only host to add: the platform's own — `play.hubspotvideo.com`,
   `image.mux.com`, `stream.mux.com` and `*.mux.com` (the rendition hosts
   behind the master playlist; the exact set is unverified) — ship in the
   manifest, because they are the same for every HubSpot customer. A slice
   reaches the job's target host plus what the ENABLED manifest lists and
   nothing else; a host still missing comes back in the report's `missing[]`
   as `denied`. Add no other key: `skills doctor` fails a manifest carrying
   one the contract does not name.
4. **First capture, then selectors.** Run one capture, read the RENDERED
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
5. **Record site traps in the wiki's SKILL.md** as they surface, under the
   site's own heading: the section map, why each selector is what it is, what
   a pointer page looks like there. Traps a SCRIPT must act on go where a
   script reads them — a param to strip beyond `hsLang` or a URL tree this
   site should never yield in `references/sites.json`, a legacy section one
   job should skip in that job's `harvest.exclude_urls`. The copy is
   wiki-owned; site knowledge belongs in it, not in your head or the run
   report. **Then have the operator re-enable the unit** —
   `llm-wiki-ops skills disable channel-hubspot-video` and
   `llm-wiki-ops skills enable channel-hubspot-video` — because `run` and the
   slice serve the ENABLED copy, and steps 3–5 edited the wiki's.
6. **Declare the job.** The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-hubspot-video`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <section-root-url> slug=<site-section>
   description="<what this is>" skill=channel-hubspot-video` — the unit's
   manifest supplies `every=once`, `harvest.scope=section`,
   `harvest.assets=download`, `transcribe.when=always` (the video IS the
   content — keep it) and a `dest` of `sources/courses/<slug>`. Point it at
   the section ROOT, not a leaf page — the manifest's `watch.note` says why:
   the unit's own filter (`leaves.py plan`) takes the section prefix from the
   job's target, so under a leaf-rooted job every sibling is skipped as out of
   scope — and the run reports `failed`, naming `harvest.scope` and the
   target, rather than closing the job with nothing captured.
7. **Expect to continue it by hand.** Videos run ~28 minutes a page, a slice
   is killed at thirty minutes, and one run attempts six pages by default, so
   a section reports `partial` many times. `every=once` — the default above —
   is NOT pulled again after a `partial`: either
   `llm-wiki-ops pipeline queue retry <ticket>` (three attempts a ticket), or
   declare the job with a period while it fills (`every=1h`, at `pipeline add`
   or `llm-wiki-ops pipeline edit <slug> every=1h`) and set `every=once` when
   a run reports `skipped`. The report's `reason` says the same.
