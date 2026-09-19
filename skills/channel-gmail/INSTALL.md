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
   construction, so there is nothing else to seed. A job declared before this
   unit's 2.3.0 keeps its `sources/email/<slug>/` literal. To re-point it:
   `pipeline edit` refuses `dest`, but re-running `add` with the same target and slug moves it and
   keeps every other key:
   `llm-wiki-ops pipeline add <its target> slug=<slug> dest=research/channels/<slug>`
   (`pipeline show <slug>` names the target).
5. **Bind the credential on each pulling machine**, per watch:
   `llm-wiki-ops credential bind <slug> <credential>` — the credential must
   already be set on that machine (`llm-wiki-ops credential set <credential>`,
   the value on stdin). Connector auth is
   session-level on that machine, per mailbox; a watch with no binding is
   skipped by intake with a named reason rather than failing mid-pull.
6. Enable the unit on every OTHER machine that pulls
   (`llm-wiki-ops skills enable channel-gmail`) — installed is not loaded,
   and enablement never travels with a `git pull`.
