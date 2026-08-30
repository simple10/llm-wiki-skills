# channel-gmail — install notes (for the installing agent)

## Customizing this unit

Install this package's `writing-skills` first — `llm-wiki-ops skills install writing-skills --repo simple10/llm-wiki-skills` (an "already installed" refusal is fine) — then invoke `writing-skills` to customize this unit against the questions below. Without it, `llm-wiki-ops reference skill-authoring` is the contract; customize the wiki's copy by hand.

## STOP if this wiki has a channel-gmail from before 2.2.0

**2.2.0 changes this unit's SHAPE**, and `skills list` cannot tell you that
— it compares name and version only, so it offers the update exactly as it
would for any other bump. Nothing migrates for you; what follows
is the whole hand-migration.

Before 2.2.0 this unit was shared prose with no SKILL.md, and each mailbox
ran a separate per-identity unit beside it (`channel-gmail-joe` and friends,
each carrying its own declaration file). One unit serves every mailbox now:
the mailbox is the WATCH's `inputs.mailbox`, its permanent slug keys the
cursor and the ledgers, and this machine's credential binding says which
login to spend.

1. **Re-point each mailbox's watch at `channel-gmail`** and give it the
   mailbox as an input — same slug, so the cursor and every past day's items
   carry forward untouched:
   `llm-wiki-ops watch add --slug <that watch's own slug>
   --description "<unchanged>" --skill channel-gmail
   --inputs mailbox=<who>@example.com` (an upsert, so the slug must be the
   EXISTING watch's).
2. **Bind the credential on every machine that pulls that mailbox**:
   `llm-wiki-ops credential bind <slug> <credential>`. The unit declares
   `requires.credential: true` and nothing else — which credential is the
   machine's answer, and intake skips an unbound watch with a named reason.
3. **Fold any per-mailbox filters** from the old per-identity units into
   this unit's `## Stages` section, or keep a mailbox that genuinely needs
   its own by forking this unit under a second name and pointing that watch
   at the fork. Then remove the old units.

If none of that applies — no `channel-gmail` installed here — ignore this
section and read on.

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
