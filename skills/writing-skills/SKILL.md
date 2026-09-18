---
name: writing-skills
description: >
  Use when customizing a skill unit this wiki installed, authoring a channel
  or media unit for a venue no package covers, or when `skills ls` reports a
  unit customized and the change is one to keep.
argument-hint: "[<unit-name>]"
---

# Writing skills — a unit this wiki owns

A skill unit is wiki content: the copy under this wiki's ops tree is the
only copy that runs, and this skill edits that copy and nothing else. The
contract for what a unit IS ships with the machinery and is read, never
restated here.

**REQUIRED BACKGROUND:** `llm-wiki-ops reference skill-authoring` — anatomy,
the manifest, the two shapes (enumerate → discovered; cursor-pull → items →
ledger), stages, fingerprints, scripts, credentials. Read it whole before
the first edit.

Scope: `$ARGUMENTS` — the unit. Default: the unit the last install report
named.

## Procedure

1. `llm-wiki-ops --json skills ls <unit>` — the unit's row, with the manifest
   it authored: `state`, `customized` (provenance once you edit; not a
   fault) and `drifted` (the enabled copy against the wiki's). Then
   `llm-wiki-ops --json skills doctor`: its `findings` for this unit must end
   with no `fail`.
2. Read the installed `SKILL.md`, `manifest.json` and `scripts/` of that
   unit, under `<ops dir>/skills/<unit>/`. The venue's customization
   questions are the unit's `INSTALL.md` in the PACKAGE it came from —
   `install` strips that file and prints none of it, so read it at the
   package's source (`skills/<unit>/INSTALL.md` in the repo the row's `from`
   names). Answer them in the unit: venue fingerprints, extraction rules, the
   `## Stages` instructions, and `watch.inputs` prompts an operator will be
   asked.
3. Edit the wiki's copy in place. A manifest key the contract does not name
   does not exist — `skills doctor` fails it (`unknown keys`), not a reader
   shrugging.
4. `llm-wiki-ops --json skills doctor` again: no `fail` for this unit.
5. `llm-wiki-ops skills enable <unit>` on this machine (and every machine
   that runs it) — installed is not loaded. It needs the operator: an
   unattended session is refused, so report the command and stop. A unit
   that was ALREADY enabled keeps running its old copy until
   `llm-wiki-ops skills disable <unit> && llm-wiki-ops skills enable <unit>`.
6. Bind it, once it is enabled: `llm-wiki-ops pipeline add <url-or-channel>
   slug=… skill=<unit>` (a media unit: `[enrich]` in `.llm-wiki.toml`). Then
   record the run.

## Never

- Edit under `.agents/` — that is the enabled COPY; the next `disable && enable` overwrites it.
- Edit the package cache or the plugin's own tree — the wiki's copy is the
  one that runs, and the other two are not yours.
- Invent a manifest key, a stage, or a kind.
- Skip `enable` and report the unit working.
