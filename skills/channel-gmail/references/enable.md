# channel-gmail — after enabling

1. One watch per mailbox. Ask a slug and a description for each, then:
   `llm-wiki-ops pipeline jobs add <slug> slug=<slug>
   description="<whose mailbox>" skill=channel-gmail
   options.mailbox=<who>@example.com [every=1d]` — the target is a bare
   channel NAME, never a url, and a wiki holds ONE job per target: a second
   mailbox declared as `gmail` again is refused ("already pulls gmail"), so
   name each after its mailbox (`acme-mail`) and reuse that name as the slug.
   `add` also refuses without `options.mailbox` (the skill's one required
   input). The manifest's `watch.dest` puts its daily
   ledgers under `research/channels/<slug>/` — the ledger route, outside the
   searchable corpus and the curation lifecycle (a mailbox is a stream, not
   a set of pages; the wiki's synthesize policy is how its substance reaches
   `wiki/`). The watch's own `_raw/<slug>/` is machine-local by
   construction, so there is nothing else to seed.
   **A job declared before this skill's 2.3.0 MUST be re-pointed before its
   next pull — this is not optional.** Such a job keeps its staged literal,
   `sources/email/<slug>/`, and `dest` is the whole of the route: the process
   step writes the day's page wherever the ticket's `dest` points, and the
   readers of ledgers — the daily report, the synthesize stage — look under
   `research/channels/` and nowhere else. What that looks like: every step
   says `ok`, and the days pile up as pages in the staged tree that nothing
   reads and curate is left to judge. The skill cannot detect it: `dest` is
   the job's answer, and a harvest ticket does not carry one at all. Check
   each job with `llm-wiki-ops pipeline jobs show <slug>` (it names `dest` and the
   target), and re-point any that is not under `research/channels/`.
   `pipeline jobs edit` refuses `dest`, but re-running `add` with the same target
   and slug moves it and keeps every other key:
   `llm-wiki-ops pipeline jobs add <its target> slug=<slug> dest=research/channels/<slug>`.
2. **Bind the job on each pulling machine**, per watch — this is a SWITCH,
   not a secret. The skill declares `requires.credential: true` and never
   reads the value: connector auth is the session's. What the binding does is
   the claim gate (`pipeline/claims.py`): a machine with no binding for THIS
   job is incapable of its harvest and skips it — a `bindings` doctor row,
   nothing fails — and a binding goes `stale`, and the machine stops
   claiming, when a peer's commit changes what the job fetches
   (`options.mailbox` included). So it is the operator's consent, per
   machine and per mailbox: "this machine's session is signed into THIS
   mailbox". Exactly what to run, on the machine that pulls:
   `printf '%s' '<who>@example.com' | llm-wiki-ops credential set <slug>-mailbox`
   — the name is the operator's to choose (`<slug>-mailbox` reads well in
   `credential bindings`); the VALUE is unused by everything, so set it to
   the mailbox's address, a label, and NEVER to a real password or token
   (a spawned slice is read-granted that one payload file). Then:
   `llm-wiki-ops credential bind <slug> <slug>-mailbox` — `bind` refuses a
   name that is not set on this machine. After a peer changes the job, look
   at what it now pulls and `bind` again. Do NOT bind on a machine whose
   session is not signed into that mailbox.
3. Enable the skill on every OTHER machine that pulls
   (`/llm-wiki:enable channel-gmail`, which enables the bound harvest
   sandbox there too) — installed is not loaded, and enablement never
   travels with a `git pull`.
4. **Where this skill's HARVEST is expected to work: only where
   `llm-wiki-ops whereami` reports `spawn: none`** — the foreman runs the
   worker in its own session, which holds the connector — until the plugin
   grants a slice a connector. Read off the plugin's source, unconfirmed by a
   run: a spawned slice is deny-read on `~/.claude.json` and its two other
   homes (the MCP server configuration) and on `~/.claude/.credentials.json`
   (`schedule/runner/floor.py`). Its egress is the sandbox its harvest stage
   is bound to, which reaches the connector's endpoint,
   `gmailmcp.googleapis.com`; whether a jailed session loads the account's
   connectors at all is unmeasured (llm-wiki-plugins#2282).
   Under a spawning runner the worker reports `failed`, "no gmail connector
   in this session", with `missing: [{"host": "connector", "url":
   "mcp:gmail", "why": "denied"}]` — tell the operator now, so a scheduled
   run that fails this way is recognized and not retried into the ground.
   The process step has no such limit: it reads the day's files and runs the
   front door, and needs neither connector nor credential.
