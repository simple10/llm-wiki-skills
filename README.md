# llm-wiki-skills

Skills for [llm-wiki](https://github.com/simple10/llm-wiki-plugins) wikis. The ops CLI reads this repo as a **package**: `llm-wiki-ops skills install simple10/llm-wiki-skills@<name>` copies a unit from here into a wiki (`skills search <task>` finds one), and `llm-wiki-package.json` is the list of what ships.

```toml
# .llm-wiki.toml — this package is the default when nothing is declared
[[packages]]
source = "simple10/llm-wiki-skills"
version = "latest"
```

- `skills/<name>/` — a skill unit: `SKILL.md`, `manifest.json`, `references/enable.md`, `references/customize.md`, `scripts/`
- `scripts/check-manifest.py` — the manifest agrees with the tree

Authoring a unit: `llm-wiki-ops reference skill-authoring` prints the contract inside any wiki.
