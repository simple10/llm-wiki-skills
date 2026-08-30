# channel-gmail — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

## A fresh install

Customize the wiki's copy now, while the operator is present:

1. Ask the lookback window for the FIRST pull (default 7d) and any
   mechanical filters — Gmail labels and senders to exclude beyond the
   defaults. They go into the installed SKILL.md's `## Stages` section, and
   they are the WIKI's, applying to every mailbox this unit pulls.
2. Ask the pull cadence (default daily) — that becomes `check_every` on the
   watch; the manifest pre-answers `1d`.
3. Diverged is the point: writing the operator's filters into `## Stages`
   makes `skills list` report the unit `diverged`. That is configuration the
   wiki owns, not drift to repair — say so in your report so nobody "fixes"
   it.
4. One watch per mailbox. Ask a slug and a description for each, then:
   `llm-wiki-ops watch add --slug <slug> --description "<whose mailbox>"
   --skill channel-gmail --inputs mailbox=<who>@example.com
   [--check-every 1d]`. The manifest's `watch.dirs.sources` puts its notes
   under `sources/email/<slug>/`; the watch's own `_raw/<slug>/` is
   machine-local by construction, so there is nothing else to seed.
5. **Bind the credential on each pulling machine**, per watch:
   `llm-wiki-ops credential bind <slug> <credential>`. Connector auth is
   session-level on that machine, per mailbox; a watch with no binding is
   skipped by intake with a named reason rather than failing mid-pull.
6. Enable the unit on every machine that pulls
   (`llm-wiki-ops skills enable channel-gmail`).
