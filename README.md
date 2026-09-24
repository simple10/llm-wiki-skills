# llm-wiki-skills

Skills for [llm-wiki](https://github.com/simple10/llm-wiki-plugins) wikis. The ops CLI reads this repo as a **package**: `llm-wiki-ops skills install simple10/llm-wiki-skills@<name>` copies a unit from here into a wiki (`skills search <task>` finds one), and `llm-wiki-package.json` is the list of what ships.

```toml
# .llm-wiki.toml — this package is the default when nothing is declared
[[packages]]
source = "simple10/llm-wiki-skills"
version = "latest"
```

- `skills/<name>/` — a skill unit: `SKILL.md`, `manifest.json`, `references/enable.md`, `references/customize.md`, `scripts/`, `tests/`
- `skills/<name>/tests/` — the unit's own tests: its scripts against its fixtures, no CLI. They ship with the unit, so an agent in a wiki can run them from the enabled copy: `uv run --with pytest pytest <ops dir>/skills/<name>/tests -p no:cacheprovider` — one unit per invocation; the cache would read as drift
- `tests/` — the package's: the harness (every unit installed and enabled through the real CLI, `test_<venue>_harness.py`), the manifests, the docs gate, and what has to hold across every unit
- `references/sandboxes/<venue>/<venue>.harvest.md` — the sandbox a unit's `stages.harvest.sandbox_ref` names: its hosts, bins, credential and profile snippet
- `scripts/check-manifest.py` — the manifest agrees with the tree

Authoring a unit: `llm-wiki-ops reference skill-authoring` prints the contract inside any wiki.
