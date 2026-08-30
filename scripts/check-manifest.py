#!/usr/bin/env python3
"""The package's own check: every artifact the manifest names exists, and
every skill or tactic on disk is named. Exit 1 with the list otherwise."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
doc = json.loads((ROOT / "llm-wiki-package.json").read_text())
problems = []
if doc.get("schema_version") != 1:
    problems.append("schema_version must be 1")
named = {(a["type"], a["name"]): a["path"] for a in doc.get("artifacts", [])}
for (kind, name), path in named.items():
    if not (ROOT / path).exists():
        problems.append(f"{kind} {name}: {path} does not exist")
    if kind == "skill" and not (ROOT / path / "SKILL.md").is_file():
        problems.append(f"skill {name}: no SKILL.md under {path}")
on_disk = {("skill", d.name) for d in (ROOT / "skills").iterdir() if (d / "SKILL.md").is_file()}
on_disk |= {("tactic", p.stem) for p in (ROOT / "tactics").glob("*.md")}
for key in sorted(on_disk - set(named)):
    problems.append(f"{key[0]} {key[1]} is on disk but not in the manifest")
for p in problems:
    print(p, file=sys.stderr)
print(f"{len(named)} artifacts, {len(problems)} problems")
sys.exit(1 if problems else 0)
