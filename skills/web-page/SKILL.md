---
name: web-page
description: One page, by url — the plain-url harvester behind a job that names no channel unit.
argument-hint: "ticket=<id>"
---

# web-page

You are the plain-url harvest ticket's unit. Unlike every channel unit,
your one stage runs with no model session at all: the pass starts
`scripts/fetch.py` directly — `llm-wiki-ops run ops/skills/web-page/scripts/fetch.py
ticket=<id>` (G2) — under the harvest sandbox, and that script reads the
ticket, fetches, and posts `tickets update` itself. You are read only when a
session hand-runs this ticket instead of leaving it to the pass — because
nothing spawned a slice, or an operator is debugging one by hand.

## Hand run

```sh
llm-wiki-ops --json pipeline tickets run <id> spawn=self
```

This prints the worker's own invocation (G2): run exactly that line,
unjailed, in this session —

```sh
llm-wiki-ops run ops/skills/web-page/scripts/fetch.py ticket=<id>
```

— then land it:

```sh
llm-wiki-ops --json pipeline tickets close <id>
```

`close` reads whatever `tickets update` the script posted and routes it:
`ok` mints the process ticket the plugin's own `scripts/extract.py` runs;
`failed` with attempts left re-queues; `gone` (a refresh only) lands the
page as gone. The script never closes its own ticket.

## What it does

`fetch.py` fetches the ticket's own `target` — only `http`/`https` — and
writes `capture.json` (`slug`, `item`, `title: null`, `body`,
`content_type`, `fetched_at`) beside the body, the whole of what the
plugin's `extract` reads. A target already in `known[]` is `ok` with nothing
captured and a reason naming `known`; a refused host, a timeout, an auth
wall or any other failure is `failed` with `missing=<host>,<url>,<why>`; a
refresh whose source answers 404 or 410 is `gone`. See `scripts/fetch.py`
for the full rule set — denial classification, the one retry, and what a
refresh does differently.

## Reference

`llm-wiki-ops reference pipeline-ticket` for the worker loop and the
report every worker leaves.
