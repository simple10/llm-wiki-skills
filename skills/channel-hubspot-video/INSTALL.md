# channel-hubspot-video — install notes (for the installing agent)

This unit is a **platform template**: it ships the HubSpot CMS/Video
knowledge (lazy `data-hsv-src` player, Mux media path, signed-manifest
rewrite) and deliberately **no selectors** — every HubSpot customer runs its
own domain and its own theme. One installed copy serves ONE site, and it
turns site-specific the moment you customize it.

1. **Name it for the site.** If this copy was installed under the catalog
   name and the wiki may ever watch a second HubSpot-hosted site, reinstall
   now, before customizing — the rename is free while the copy is pristine:
   move this copy aside, then
   `llm-wiki-ops skills install channel-hubspot-video --as channel-<site>`.
   Later it means untangling one site's selectors and traps from another's.
2. **Confirm the platform** against the SKILL.md Fingerprints section:
   `hubfs/` / `hs-fs/hubfs/` asset paths, `data-hsv-src` iframes, `_hcms/`
   in robots.txt, `?hsLang=` params. A miss means this is not the unit.
3. **Pin the match.** In the installed copy's `manifest.json` set
   `match.hosts` to the site's real domain and `custom_domains` to `false`
   — from then on `skills find <domain>` answers from the wiki's copy
   directly instead of reporting a "possible" template.
4. **First capture, then selectors.** Run one capture, read the RENDERED
   `page.html`, and fill the manifest's `extract` block (`content_selector`,
   `drop_selectors`, `title_selector`) per the SKILL.md's "Content
   extraction" section. Set `title_selector` whenever the site reuses one
   `<title>` across a section — HubSpot sites commonly do.
5. **Record site traps in the installed SKILL.md** as they surface: legacy
   URL sections to exclude, params to strip beyond `hsLang`, the section
   map. The copy is wiki-owned; site knowledge belongs in it, not in your
   head or the run report.
6. **Watch shape**: keep `transcript: always` (the video IS the content) and
   point the watch at the section ROOT, not a leaf page — the manifest's
   `watch.note` says why.
