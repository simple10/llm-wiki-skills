# channel-circle — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Circle communities run on `*.circle.so` **or their own custom domain**, but
a custom-domain community still renders Circle's own uniform chrome — so
ONE installed copy normally serves every community this wiki watches, with
one watch per community. Customize the wiki's copy now, while the operator
is present:

1. Ask which community this wiki watches (the `*.circle.so` domain or the
   custom domain fronting it) and record it in the installed SKILL.md under
   a "Watched communities" heading — the skill body is wiki-owned. For a
   custom-domain community, also add that host to the installed copy's
   `manifest.json` `requires.network` (keep `circle.so`) so
   `skills search <domain>` answers from the wiki's copy directly, and the
   harvest slice is granted that host.
   There is no second copy to install: identity is the JOB's slug, so two
   communities are two jobs on this one unit, each under its own heading
   here.
2. **Auth walkthrough** — Circle content is licensed, so do it now:
   `llm-wiki-ops run scripts/login.py <domain>` (run FROM the
   wiki root — the helper takes the current directory as the wiki root,
   and resolves the credential store from there) opens a real
   Chrome window; the operator logs in (solving any Turnstile/2FA), opens a
   gated lesson to confirm access, then presses Enter in the terminal. This
   saves the storage state AND the persistent per-domain profile — on
   Circle the profile is the load-bearing half (Cloudflare's `cf_clearance`
   is fingerprint-bound to it; see the SKILL.md's Auth section).
   The credential store never syncs, so every harvesting machine repeats
   this once. Tell the operator now: a SPAWNED slice's jail is granted no
   profile directory today (llm-wiki-plugins#2117 item 12), so until it is,
   this unit captures only where the worker itself runs unjailed — see the
   SKILL.md's Auth section.
3. Declare the job, naming the skill. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-circle`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add <space-root-url> slug=<community>
   description="<what this is>" skill=channel-circle` — the unit's manifest
   supplies `every=once`, `harvest.scope=section`, `harvest.access=licensed`
   and a `dest` of `sources/courses/<slug>`. Scope `section` means "under the
   job's url", so that url must be the space ROOT (`/c/<slug>`): from a
   lesson url every sibling lesson is outside it. Pass
   `harvest.scope=domain` to take the whole community. A course longer than
   one 30-minute slice ends `partial`, and an `every=once` job is never
   pulled again by itself: re-queue it with `llm-wiki-ops pipeline queue
   retry <ticket-id>` (three attempts per ticket), or declare a period
   (`every=1d`) until the course is held — each run skips what it already has.
