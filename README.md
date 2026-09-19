# llm-wiki-skills

Skill units and venue-tactics playbooks for [llm-wiki](https://github.com/simple10/llm-wiki-plugins) wikis. The ops CLI reads this repo as a **package**: `llm-wiki-ops skills install simple10/llm-wiki-skills@<name>` copies a unit from here into a wiki (`skills search <task>` finds one), and `llm-wiki-package.json` is the list of what ships. The playbooks under `tactics/` ship in that list too; the rebuilt CLI has no `tactics` verb to install them yet.

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
