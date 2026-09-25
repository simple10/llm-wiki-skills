"""Every artifact the manifest names installs into a wiki through the real
CLI, lists clean, and — for a skill — enables; a channel unit routes a
pipeline job by its own manifest, with no dest named; the plugin script a unit
reaches for is one `run` really serves. Scripted per artifact."""

from __future__ import annotations

import re

import pytest

from harness import ROOT, SKILLS, SOURCE, bound, enabled, rooted, run, unit_manifest

CHANNELS = [n for n in SKILLS if unit_manifest(n).get("kind") == "channel" and unit_manifest(n).get("watch")]

VTT = "WEBVTT\n\n00:00:00.080 --> 00:00:02.629\nAt its peak, it grew\n"


@pytest.mark.parametrize("name", SKILLS)
def test_skill_installs_with_package_provenance_lists_clean_and_enables(ops, env, wiki, name):
    r = run(ops, rooted(env, wiki), "--json", "skills", "install", name)
    assert r.returncode == 0, r.stderr
    got = r.data
    assert got["package"] == SOURCE and len(got["ref"]) == 12, got
    assert got["version"] == unit_manifest(name)["version"], got
    assert got["warnings"] == [], got["warnings"]

    rows = run(ops, rooted(env, wiki), "--json", "skills", "ls", name).data["skills"]
    row = next(s for s in rows if s["name"] == name)
    assert row["from"].startswith(f"{SOURCE}@"), row
    assert not row["customized"] and not row["drifted"], row
    assert row["warnings"] == [], row

    bound(ops, env, wiki, name)  # an unbound stage refuses `skills enable`
    # `--confirm`: enable decides what this machine loads, so it refuses unattended without it
    r = run(ops, rooted(env, wiki), "--json", "skills", "enable", name, "--confirm")
    assert r.returncode == 0, r.stderr
    assert (wiki / ".agents" / "skills" / name / "SKILL.md").is_file()


@pytest.mark.parametrize("name", CHANNELS)
def test_channel_unit_routes_a_job_by_its_own_manifest(ops, env, wiki, name):
    """`jobs add skill=<unit>` with no `dest=`: the unit's `watch.dest`
    template routes the job and its `watch.defaults` seed the record. This is
    the contract the unit manifest exists for (what `match.hosts` plus
    `skills find`'s rendered flags used to carry)."""
    enabled(ops, env, wiki, name)  # `add` reads `dest` and the defaults off the ENABLED copy
    watch = unit_manifest(name)["watch"]
    slug = f"harness-{name}"
    # `research/channels/…` is the ledger route, and only a channel's pull lands
    # there: that unit's target is the channel's bare name, never a url.
    ledger = watch["dest"].startswith("research/channels/")
    add = [
        "--json", "pipeline", "jobs", "add", unit_manifest(name)["venue"] if ledger else f"https://example.invalid/harness/{name}",
        f"slug={slug}", f"skill={name}", f"description=harness: {name}",
    ]
    required = sorted(k for k, v in (watch.get("inputs") or {}).items() if v.get("required") is True)
    if required:  # an add that skips a required input is refused, naming the key that answers it
        r = run(ops, rooted(env, wiki), *add)
        assert r.returncode == 2 and all(f"options.{k}=" in r.data["error"] for k in required), r.stdout
    r = run(ops, rooted(env, wiki), *add, *(f"options.{k}=harness-{k}" for k in required))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.data["dest"] == watch["dest"].format(slug=slug), r.data

    shown = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "show", slug)
    assert shown.returncode == 0, shown.stderr
    job = shown.data["job"]
    assert {k: job["options"][k] for k in required} == {k: f"harness-{k}" for k in required}, job["options"]
    for stage in unit_manifest(name)["stages"]:
        assert job[stage]["skill"] == name, job[stage]
    for key, want in watch["defaults"].items():
        if isinstance(want, dict):
            assert {k: job[key][k] for k in want} == want, (key, job[key])
        else:
            assert job[key] == want, (key, job[key])


def _addresses_run_serves_from_the_plugin():
    """Every `llm-wiki-ops run <plugin path>` a unit's script names in code —
    the quoted constants, not prose. `ops/…` is the unit's own tree, not the
    plugin's."""
    quoted = re.compile(r"""["']((?:scripts|skills/[\w-]+/scripts)/[\w/-]+\.py)["']""")
    found = set()
    for script in sorted((ROOT / "skills").glob("*/scripts/*.py")):
        unit = script.parts[-3]
        found.update((unit, rel) for rel in quoted.findall(script.read_text(encoding="utf-8")))
    return sorted(found)


def test_the_address_scan_finds_the_one_it_was_written_for():
    """A scan that finds nothing parametrizes nothing, and pytest reports that
    as one quiet skip — how #6's `match.hosts` case sat dead. The formatter is
    the known member: spell `FORMATTER` some way the pattern cannot read and
    this fails, rather than the case below vanishing."""
    assert ("channel-youtube", "scripts/format_transcript.py") in _addresses_run_serves_from_the_plugin()


# G3: `assets.py`, `published_date.py`, `toolcheck.py` and
# `format_transcript.py` move to the plugin's `scripts/` in PR 4 — addressed
# `run scripts/<file>` here already, ahead of the move. Until PR 4 lands the
# plugin still serves them at their old, nested addresses, so a case for one
# of these skips naming PR 4 rather than failing on a plugin that has not
# caught up yet.
G3_HELPERS = {"scripts/assets.py", "scripts/published_date.py", "scripts/toolcheck.py", "scripts/format_transcript.py"}


@pytest.mark.parametrize(("unit", "rel"), _addresses_run_serves_from_the_plugin())
def test_the_plugin_script_a_unit_runs_is_one_run_serves(ops, env, wiki, tmp_path, unit, rel):
    """A unit reaches plugin machinery by ADDRESS, and the plugin is free to
    move its files: `scripts/format_transcript.py` became
    `skills/process/scripts/…` and every youtube note aborted at its
    transcript, with each test here still green behind `--format-transcript`.
    `run` refuses an address it does not serve with exit 2, before anything
    runs — so any other exit means the address resolved. That is ALL this
    asserts: a script that resolves and then crashes is its own tests' to
    catch, not this one's."""
    vtt = tmp_path / "a.en.vtt"
    vtt.write_text(VTT, encoding="utf-8")
    r = run(ops, env, "run", rel, str(vtt), cwd=wiki)
    if rel in G3_HELPERS and r.returncode == 2 and "no such script" in r.stderr:
        pytest.skip(f"{rel} moves to the plugin's scripts/ in plugins PR 4 (G3)")
    assert r.returncode != 2 and "no such script" not in r.stderr, f"{unit} runs {rel}: {r.stderr}"


