# channel-circle — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Circle communities run on `*.circle.so` **or their own custom domain**, but
a custom-domain community still renders Circle's own uniform chrome — so
ONE installed copy normally serves every community this wiki watches, with
one watch per community. Customize the wiki's copy now, while the operator
is present:

1. Ask which community this wiki watches (the `*.circle.so` domain or the
   custom domain fronting it) and record it in the installed SKILL.md under
   a "Watched communities" heading — the skill body is wiki-owned. For a
   custom-domain community, also add that host to the installed copy's
   `manifest.json` `match.hosts` (keep `circle.so` and `custom_domains`) so
   `skills find <domain>` answers from the wiki's copy directly.
   Only if a community later needs genuinely different customization than
   the others: install a separate copy for it —
   `llm-wiki-ops skills install channel-circle --as channel-<community>`
   — rather than forking this one's knowledge in place.
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
   this once.
3. Point the watch at the skill — the INSTALLED name, in case this copy was
   installed under a per-community `--as` name:
   `llm-wiki-ops watch add --slug <community> --description "<what this is>"
   --url <space-root-url> --skill <installed-name>
   --scope section --mode once --access licensed` — scope `section` is a
   prefix test on the watch URL, so it must be the space ROOT
   (`/c/<slug>`): a lesson URL rejects every sibling lesson as
   out_of_scope while the run still exits 0. Use `--scope domain` to take
   the whole community.
