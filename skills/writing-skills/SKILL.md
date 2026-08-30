---
name: writing-skills
description: >
  Use when customizing a skill unit this wiki installed, authoring a channel
  or media unit for a venue no package covers, or when `skills list` reports a
  unit diverged and the change is one to keep.
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

1. `llm-wiki-ops skills list --json` — the unit's row: `problems` (must end
   empty), `diverged` (provenance once you edit; not a fault), `enablement`.
2. Read the installed `SKILL.md`, `manifest.json` and `scripts/` of that
   unit — the paths the row prints. The install report's `install_notes`
   carried this venue's customization questions; answer them in the unit:
   venue fingerprints, extraction rules, the `## Stages` instructions, and
   `watch.inputs` prompts an operator will be asked.
3. Edit the wiki's copy in place. A manifest key the contract does not name
   does not exist — `skills list` refuses it, not a reader shrugging.
4. `llm-wiki-ops skills list --json` again: `problems` empty.
5. `llm-wiki-ops skills enable <unit>` on this machine (and every machine
   that runs it) — installed is not loaded.
6. Bind it: `llm-wiki-ops watch add --url … --skill <unit>` (a media unit:
   `[enrich]` in `.llm-wiki.toml`). Then record the run.

## Never

- Edit under `.agents/` — that is the enabled COPY; `refresh` overwrites it.
- Edit the package cache or the plugin's own tree — the wiki's copy is the
  one that runs, and the other two are not yours.
- Invent a manifest key, a stage, or a kind.
- Skip `enable` and report the unit working.
