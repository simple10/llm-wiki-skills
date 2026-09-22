# channel-circle — after enabling

1. **Auth walkthrough** — Circle content is licensed, so do it now:
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
   profile directory today, so until it is, this skill captures only where the
   worker itself runs unjailed — see the SKILL.md's Auth section.
2. Declare the job, naming the skill: `llm-wiki-ops pipeline add
   <space-root-url> slug=<community> description="<what this is>"
   skill=channel-circle` — the skill's manifest supplies `every=once`,
   `harvest.scope=section`, `harvest.access=licensed`,
   `harvest.assets=download` and a `dest` of `sources/courses/<slug>`.
   `reference` keeps no lesson video and yields no transcript (the venue's
   only reference is a signed HLS manifest, dead within hours); `download`
   is many GB per course. Pass `harvest.assets=reference` only for a
   community whose lessons are text. Scope `section` means "under the
   job's url", so that url must be the space ROOT (`/c/<slug>`): from a
   lesson url every sibling lesson is outside it. Pass
   `harvest.scope=domain` to take the whole community. A course longer than
   one 30-minute slice ends `partial`, and an `every=once` job is never
   pulled again by itself: re-queue it with `llm-wiki-ops pipeline queue
   retry <ticket-id>` (three attempts per ticket), or declare a period
   (`every=1d`) until the course is held — each run skips what it already has.
