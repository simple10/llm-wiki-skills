# channel-gmail — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

## A fresh install

Customize the wiki's copy now, while the operator is present:

1. Ask the lookback window for the FIRST pull (default 7d) and any
   mechanical filters — Gmail labels and senders to exclude beyond the
   defaults. They go into the installed SKILL.md's `## Stages` section, and
   they are the WIKI's, applying to every mailbox this unit pulls.
2. Ask the pull cadence (default daily) — that becomes `every` on the
   watch; the manifest pre-answers `1d`.
3. Customized is the point: writing the operator's filters into `## Stages`
   makes `skills ls` report the unit `customized`. That is configuration the
   wiki owns, not drift to repair — say so in your report so nobody "fixes"
   it with `skills install --force`.
4. One watch per mailbox. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-gmail`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   Ask a slug and a description for each, then:
   `llm-wiki-ops pipeline add <slug> slug=<slug>
   description="<whose mailbox>" skill=channel-gmail
   options.mailbox=<who>@example.com [every=1d]` — the target is a bare
   channel NAME, never a url, and a wiki holds ONE job per target: a second
   mailbox declared as `gmail` again is refused ("already pulls gmail"), so
   name each after its mailbox (`acme-mail`) and reuse that name as the slug.
   `add` also refuses without `options.mailbox` (the unit's one required
   input). The manifest's `watch.dest` puts its daily
   ledgers under `research/channels/<slug>/` — the ledger route, outside the
   searchable corpus and the curation lifecycle (a mailbox is a stream, not
   a set of pages; the wiki's synthesize policy is how its substance reaches
   `wiki/`). The watch's own `_raw/<slug>/` is machine-local by
   construction, so there is nothing else to seed.
   **A job declared before this unit's 2.3.0 MUST be re-pointed before its
   next pull — this is not optional.** Such a job keeps its staged literal,
   `sources/email/<slug>/`, and the rebuilt extractor decides the route from `dest`
   alone (`pipeline/extract.py::_route`): a dest that is not
   `research/channels/<slug>` sends the day directory down the one-page-per-
   capture route, which wants a `capture.json` a day directory never has.
   What that looks like: harvest says `ok`, and EVERY process ticket then
   fails — `no_capture`, "`_raw/<slug>/<day>` carries no capture.json" — with
   no ledger written. The unit cannot detect it: a download ticket's `dest`
   is null. Check each job with `llm-wiki-ops pipeline show <slug>` (it names
   `dest` and the target), and re-point any that is not under
   `research/channels/`. `pipeline edit` refuses `dest`, but re-running `add`
   with the same target and slug moves it and keeps every other key:
   `llm-wiki-ops pipeline add <its target> slug=<slug> dest=research/channels/<slug>`.
5. **Bind the job on each pulling machine**, per watch — this is a SWITCH,
   not a secret. The unit declares `requires.credential: true` and never
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
6. Enable the unit on every OTHER machine that pulls
   (`llm-wiki-ops skills enable channel-gmail`) — installed is not loaded,
   and enablement never travels with a `git pull`.
7. **Where this unit is expected to work: only where `llm-wiki-ops whereami`
   reports `spawn: none`** — the foreman runs the worker in its own session,
   which holds the connector — until the plugin grants a slice a connector.
   Read off the plugin's source, unconfirmed by a run: a spawned slice is
   deny-read on `~/.claude.json` and its two other homes (the MCP server
   configuration) and on `~/.claude/.credentials.json`
   (`schedule/runner/floor.py`), and its egress is this unit's
   `requires.network` alone — `mail.google.com`, which no step fetches and
   which is not a connector's endpoint. No host was invented to cover that.
   Under a spawning runner the worker reports `failed`, "no gmail connector
   in this session", with `missing: [{"host": "connector", "url":
   "mcp:gmail", "why": "denied"}]` — tell the operator now, so a scheduled
   run that fails this way is recognised and not retried into the ground.
