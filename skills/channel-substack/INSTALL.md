# channel-substack — install notes (for the installing agent)

Customize the wiki's copy now, while the operator is present:

1. Ask which newsletter(s) this wiki watches (domain or archive URL) and a
   slug for each, and record them in the installed SKILL.md under a
   "Watched newsletters" heading — the skill body is wiki-owned.
2. Ask free-only vs licensed. Free needs no auth; licensed needs a
   Playwright storage state for the newsletter's own domain (custom-domain
   newsletters may not share substack.com cookies).
3. Ask for an age floor (e.g. only posts from the last 3 months) — that
   becomes `--max-age` on the watch, and rides jobs as `min_date`.
4. Point the watch at the skill — **scope MUST be `domain`**. The
   enumerator reports every discovered post and the host queues it, and the
   default `page` scope silently rejects all of them as `out_of_scope`
   while the run still exits 0. That shows up host-side, in
   `harvest_apply.py apply`'s `reasons` for the discovered row — not in the
   enumerator's own output, which no longer counts what intake did:
   `llm-wiki-ops watch add --slug <newsletter> --description "<what this is>"
   --url <archive-url> --skill channel-substack
   --scope domain --mode continual [--max-age 3m] [--access free|licensed]`
