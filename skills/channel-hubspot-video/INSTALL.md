# channel-hubspot-video — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

This unit is a **platform template**: it ships the HubSpot CMS/Video
knowledge (lazy `data-hsv-src` player, Mux media path, signed-manifest
rewrite) and deliberately **no selectors** — every HubSpot customer runs its
own domain and its own theme. The wiki's copy turns site-specific the moment
you customize it.

1. **One copy, and every site in it.** `skills install` has no rename: the
   unit is `channel-hubspot-video` in every wiki, and identity is the JOB's
   slug. A second HubSpot-hosted site is a second job on this same unit, so
   keep each site's selectors and traps under its own heading in the
   installed SKILL.md rather than interleaved.
2. **Confirm the platform** against the SKILL.md Fingerprints section:
   `hubfs/` / `hs-fs/hubfs/` asset paths, `data-hsv-src` iframes, `_hcms/`
   in robots.txt, `?hsLang=` params. A miss means this is not the unit.
3. **Pin the hosts.** In the wiki's copy of `manifest.json` add to
   `requires.network` the site's real domain — from then on
   `skills search <domain>` answers from the wiki's copy directly — AND the
   platform's media hosts: `play.hubspotvideo.com`, `image.mux.com`,
   `stream.mux.com` and `*.mux.com` (the rendition hosts behind the master
   playlist; the exact set is unverified). A slice reaches the job's target
   host plus what the ENABLED manifest lists and nothing else, so without them
   the page renders and the video is refused; a host still missing comes back
   in the report's `missing[]` as `denied`. Add no other key: `skills doctor`
   fails a manifest carrying one the contract does not name.
4. **First capture, then selectors.** Run one capture, read the RENDERED
   `page.html`, and fill the site's entry in the wiki's copy of
   `references/sites.json`, keyed by host (`content_selector`,
   `drop_selectors`, `title_selector`, and `strip_params` / `exclude_urls`
   where the site needs them) per the SKILL.md's "Content extraction"
   section. That file — not the manifest — is what `leaves.py page` reads at
   harvest, which is the only time this venue's rendering happens: processing
   is the generic extractor's. Set `title_selector` whenever the site reuses
   one `<title>` across a section — HubSpot sites commonly do, and pages are
   FILED by title, so identically titled lessons overwrite each other
   (`leaves.py report` tells namesakes apart within one run only).
   Check it without the network:
   `llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/leaves.py page <capture_dir> <leaf_dir> --sites <ops dir>/skills/channel-hubspot-video/references/sites.json --no-media-leaf`
   re-renders `page.md` from the `page.html` already on disk.
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
   scope.
