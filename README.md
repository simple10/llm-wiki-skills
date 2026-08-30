# llm-wiki-skills

Skill units and venue-tactics playbooks for [llm-wiki](https://github.com/simple10/llm-wiki-plugins) wikis. The ops CLI reads this repo as a **package**: `llm-wiki-ops skills install <name>` and `llm-wiki-ops tactics install <name>` copy artifacts from here into a wiki, and `llm-wiki-package.json` is the list of what ships.

```toml
# .llm-wiki.toml — this package is the default when nothing is declared
[[packages]]
source = "simple10/llm-wiki-skills"
version = "latest"
```

- `skills/<name>/` — a skill unit: `SKILL.md`, `manifest.json`, `INSTALL.md`, `scripts/`
- `tactics/<name>.md` — a venue-tactics playbook
- `scripts/check-manifest.py` — the manifest agrees with the tree

Authoring a unit: `llm-wiki-ops reference skill-authoring` prints the contract inside any wiki.
