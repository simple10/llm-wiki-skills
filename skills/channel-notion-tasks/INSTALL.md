# channel-notion-tasks — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install simple10/llm-wiki-skills@writing-skills` (over an unedited copy this just refreshes it; a refusal means this wiki customized its copy, which is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

Customize the wiki's copy now, while the operator is present:

1. Ask which Notion databases hold the operator's tasks and record their
   ids/names in the installed SKILL.md's `## Stages` harvest section's
   "Mechanical filters", along
   with the lookback window for the FIRST pull (default 14d) and any
   statuses to exclude (e.g. Archived). These belong in the unit, not the
   watch entry.
2. Ask the pull cadence (default daily) — that becomes `every` on
   the watch.
3. Customized is the point: writing the operator's databases and filters into
   SKILL.md makes `skills ls` report the unit `customized`. That is
   configuration the wiki owns, not drift to repair — say so in your report
   so nobody "fixes" it with `skills install --force`.
4. Ask a slug and a description, then declare the job. The unit must be ENABLED on this machine first — `llm-wiki-ops skills enable channel-notion-tasks`, which the
   operator runs (an unattended session is refused): `pipeline add` reads `dest` and the
   defaults off the enabled copy, and answers `dest required` without it.
   `llm-wiki-ops pipeline add notion-tasks slug=notion-tasks
   description="<what this is>" skill=channel-notion-tasks
   options.workspace=<workspace> [every=1d]` — the target is a bare channel
   NAME, never a url, and a wiki holds ONE job per target: a second workspace
   needs its own name (`acme-tasks`), reused as its slug. `add` refuses
   without `options.workspace` (the unit's one required input). The manifest's `watch.dest` puts its daily
   ledgers under `research/channels/<slug>/` — the ledger route, outside the
   searchable corpus and the curation lifecycle (tasks are a stream; the
   wiki's synthesize policy is how their substance reaches `wiki/`). The
   watch's own `_raw/<slug>/` is already machine-local by construction, so
   there is nothing else to seed.
   **A job declared before this unit's 1.7.0 MUST be re-pointed before its
   next pull — this is not optional.** Such a job keeps its staged literal,
   `sources/tasks/<slug>/`, and the rebuilt extractor decides the route from `dest`
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
5. **No credential to bind, and why.** Connector auth is session-level on
   the pulling machine, and this unit declares `requires.credential: false`
   where `channel-gmail` — the same connector model — declares `true` as a
   per-machine claim switch. This unit cannot use that switch: a credentialed
   slice that would reach no host is REFUSED at spawn
   (`pipeline/dispatch.py`), and this unit has no host it can truthfully
   declare. So say which machine pulls the other two ways: ENABLE the unit
   only on the machine whose session holds the Notion connector, and where
   more than one machine has it enabled, pin the job:
   `llm-wiki-ops pipeline edit <slug> harvest.machine=<machine id>`.
6. **Where this unit is expected to work: only where `llm-wiki-ops whereami`
   reports `spawn: none`** — the foreman runs the worker in its own session,
   which holds the connector — until the plugin grants a slice a connector.
   Read off the plugin's source, unconfirmed by a run: a spawned slice is
   deny-read on `~/.claude.json` and its two other homes (the MCP server
   configuration) and on `~/.claude/.credentials.json`
   (`schedule/runner/floor.py`); and this unit declares no
   `requires.network` and its target is a channel name, so its slice is
   minted with no hosts and its network is BLOCKED outright
   (`pipeline/dispatch.py`, `schedule/runner/slice.py`). No host was invented
   to cover that. Under a spawning runner the worker reports `failed`, "no
   notion connector in this session", with `missing: [{"host": "connector",
   "url": "mcp:notion", "why": "denied"}]` — tell the operator now, so a
   scheduled run that fails this way is recognised and not retried into the
   ground.
