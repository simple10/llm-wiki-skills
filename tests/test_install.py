"""Every artifact the manifest names installs into a wiki through the real
CLI, lists clean, and — for a skill — enables; a channel unit that matches a
host binds a watch with the flags `skills find` renders for it. The spike's
exit test (llm-wiki-plugins #1288), scripted per artifact."""

from __future__ import annotations

import pytest

from conftest import SKILLS, SOURCE, TACTICS, run, unit_manifest


@pytest.mark.parametrize("name", SKILLS)
def test_skill_installs_with_package_provenance_lists_clean_and_enables(ops, env, wiki, name):
    r = run(ops, env, "skills", str(wiki), "install", name, "--repo", SOURCE)
    assert r.returncode == 0, r.stderr
    src = r.data["source"]
    assert src["type"] == "package" and src["repo"] == SOURCE and len(src["ref"]) == 12, src
    # a platform template warns about per-site copies on every install — that is the unit talking, not a fault
    assert [w for w in r.data["warnings"] if "platform TEMPLATE" not in w] == [], r.data["warnings"]

    rows = run(ops, env, "skills", str(wiki), "list", "--json").data["skills"]
    row = next(s for s in rows if s["name"] == name)
    assert row["problems"] == [] and row["stale"] is None and not row["diverged"], row

    r = run(ops, env, "skills", str(wiki), "enable", name)
    assert r.returncode == 0, r.stderr
    assert (wiki / ".agents" / "skills" / name / "SKILL.md").is_file()


@pytest.mark.parametrize("name", [n for n in SKILLS if (unit_manifest(n).get("match") or {}).get("hosts")])
def test_channel_unit_binds_a_watch_with_the_flags_find_renders(ops, env, wiki, name):
    host = unit_manifest(name)["match"]["hosts"][0].lstrip("*.")
    url = f"https://{host}/harness/{name}"
    found = run(ops, env, "skills", str(wiki), "find", url)
    assert found.returncode == 0, found.stderr
    installed = [row for row in found.data["installed"] if row["name"] == name]
    assert installed, found.data
    flags = installed[0]["watch_flags"].split()
    slug = f"harness-{name}"
    r = run(
        ops, env, "watch", str(wiki), "add",
        "--slug", slug, "--description", f"harness: {name}", "--url", url,
        "--dest", f"sources/scrapes/{slug}", *flags,
    )
    assert r.returncode == 0, r.stderr
    shown = run(ops, env, "watch", str(wiki), "show", slug)
    assert shown.returncode == 0, shown.stderr
    assert name in shown.stdout


@pytest.mark.parametrize("name", TACTICS)
def test_tactic_installs_or_is_already_seeded_and_lists_undiverged(ops, env, wiki, name):
    r = run(ops, env, "tactics", str(wiki), "install", name) if name != "_TEMPLATE" else None
    if r is not None:
        assert r.returncode == 0, r.stderr
        assert r.data["status"] in ("installed", "skipped"), r.data  # init may have seeded it
    rows = run(ops, env, "tactics", str(wiki), "list", "--json").data["tactics"]
    row = next(t for t in rows if t["name"] == name)
    assert row["source"]["type"] == "package" and not row["diverged"], row
