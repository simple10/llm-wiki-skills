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
3. **Pin the host.** In the installed copy's `manifest.json` add the
   site's real domain to `requires.network` — from then on
   `skills search <domain>` answers from the wiki's copy directly, and the
   slice is granted that host.
4. **First capture, then selectors.** Run one capture, read the RENDERED
   `page.html`, and fill the manifest's `extract` block (`content_selector`,
   `drop_selectors`, `title_selector`) per the SKILL.md's "Content
   extraction" section. Set `title_selector` whenever the site reuses one
   `<title>` across a section — HubSpot sites commonly do.
5. **Record site traps in the installed SKILL.md** as they surface: legacy
   URL sections to exclude, params to strip beyond `hsLang`, the section
   map. The copy is wiki-owned; site knowledge belongs in it, not in your
   head or the run report.
6. **Declare the job.** The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-hubspot-video`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <section-root-url> slug=<site-section>
   description="<what this is>" skill=channel-hubspot-video` — the unit's
   manifest supplies `every=once`, `harvest.scope=section`,
   `harvest.assets=download`, `transcribe.when=always` (the video IS the
   content — keep it) and a `dest` of `sources/courses/<slug>`. Point it at
   the section ROOT, not a leaf page — the manifest's `watch.note` says why.
