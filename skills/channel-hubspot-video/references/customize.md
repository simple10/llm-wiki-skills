# channel-hubspot-video — customizing this skill

This skill is a **platform template**: it ships the HubSpot CMS/Video
knowledge (lazy `data-hsv-src` player, Mux media path, signed-manifest
rewrite) and deliberately **no selectors** — every HubSpot customer runs its
own domain and its own theme. The wiki's copy turns site-specific the moment
you customize it.

1. **One copy, and every site in it.** `skills install` has no rename: the
   skill is `channel-hubspot-video` in every wiki, and identity is the JOB's
   slug. A second HubSpot-hosted site is a second job on this same skill, so
   keep each site's selectors and traps under its own heading in the
   installed SKILL.md rather than interleaved.
2. **Confirm the platform.** `skills search` answers a site's url with `no
   skill claims <host>` — no skill can enumerate every HubSpot customer's domain
   — so keywords, or a person, sent you here. Look for: assets under `/hubfs/`
   or `/hs-fs/hubfs/`; `hubspot` in the page scripts and `_hcms/` disallowed
   in `robots.txt`; nav links carrying `?hsLang=<lang>`; a player iframe at
   `play.hubspotvideo.com/v/<portal>/id/<video>`. Any two together is
   conclusive, and the last alone means the SKILL.md's *Media* section applies
   even where the rest of the site is not HubSpot-themed. A miss on all four
   means this is not the skill.

## Sandbox

`stages.harvest.sandbox_ref` is
`simple10/llm-wiki-skills:hubspot-cms/hubspot-cms.harvest`. What the stage
reaches, and why:

```sh
llm-wiki-ops packages reference simple10/llm-wiki-skills references/sandboxes/hubspot-cms/hubspot-cms.harvest.md
```

`/llm-wiki:sandbox channel-hubspot-video` reviews it into a wiki sandbox,
and `/llm-wiki:enable channel-hubspot-video` binds the stage. `process`
names no sandbox and runs with no network.
