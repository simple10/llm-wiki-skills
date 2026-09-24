# channel-notion-tasks — after enabling

1. Ask a slug and a description, then declare the job:
   `llm-wiki-ops pipeline add notion-tasks slug=notion-tasks
   description="<what this is>" skill=channel-notion-tasks
   options.workspace=<workspace> [every=1d]` — the target is a bare channel
   NAME, never a url, and a wiki holds ONE job per target: a second workspace
   needs its own name (`acme-tasks`), reused as its slug. `add` refuses
   without `options.workspace` (the skill's one required input). The manifest's `watch.dest` puts its daily
   ledgers under `research/channels/<slug>/` — the ledger route, outside the
   searchable corpus and the curation lifecycle (tasks are a stream; the
   wiki's synthesize policy is how their substance reaches `wiki/`). The
   watch's own `_raw/<slug>/` is already machine-local by construction, so
   there is nothing else to seed.
   **A job declared before this skill's 1.7.0 MUST be re-pointed before its
   next pull — this is not optional.** Such a job keeps its staged literal,
   `sources/tasks/<slug>/`, and `dest` is the whole of the route: it is where
   the skill's process step writes the day's ledger, and it is what the host
   reads to decide what a day directory is. A dest outside
   `research/channels/` puts a `type: ledger` page in the corpus, staged and
   in the curation lifecycle, where a ledger does not belong — and sends the
   day directory down the one-page-per-capture route, which is not what a
   channel harvest leaves. The skill cannot detect it: a harvest ticket's
   `dest` is null. Check each job with `llm-wiki-ops pipeline show <slug>` (it
   names `dest` and the target), and re-point any that is not under
   `research/channels/`. `pipeline edit` refuses `dest`, but re-running `add`
   with the same target and slug moves it and keeps every other key:
   `llm-wiki-ops pipeline add <its target> slug=<slug> dest=research/channels/<slug>`.
2. **No credential to bind, and why.** Connector auth is session-level on
   the pulling machine, and this skill declares `requires.credential: false`
   where `channel-gmail` — the same connector model — declares `true` as a
   per-machine claim switch. This skill cannot use that switch: a credentialed
   slice that would reach no host is REFUSED at spawn
   (`pipeline/dispatch.py`), and this skill has no host it can truthfully
   declare. So say which machine pulls the other two ways: ENABLE the skill
   only on the machine whose session holds the Notion connector, and where
   more than one machine has it enabled, pin the job:
   `llm-wiki-ops pipeline edit <slug> harvest.machine=<machine id>`.
3. **Where this skill is expected to work: only where `llm-wiki-ops whereami`
   reports `spawn: none`** — the foreman runs the worker in its own session,
   which holds the connector — until the plugin grants a slice a connector.
   Read off the plugin's source, unconfirmed by a run: a spawned slice is
   deny-read on `~/.claude.json` and its two other homes (the MCP server
   configuration) and on `~/.claude/.credentials.json`
   (`schedule/runner/floor.py`). Its egress is the sandbox its harvest stage
   is bound to, which reaches `mcp.notion.com` (unverified as the connector's
   endpoint); whether a jailed session loads the account's connectors at all
   is unmeasured (llm-wiki-plugins#2282). Under a spawning runner the harvest worker reports
   `failed`, "no notion connector in this session", with `missing: [{"host": "connector",
   "url": "mcp:notion", "why": "denied"}]` — tell the operator now, so a
   scheduled run that fails this way is recognized and not retried into the
   ground.
