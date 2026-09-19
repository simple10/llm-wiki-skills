"""What has to hold across EVERY unit, so one unit's port cannot quietly
drift from the rest."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from conftest import ROOT, SKILLS, unit_manifest

CHANNELS = [n for n in SKILLS if unit_manifest(n).get("kind") == "channel"]
SHIPPED = sorted(p for p in (ROOT / "skills").rglob("*") if p.is_file() and p.suffix in {".md", ".py", ".json"} and "__pycache__" not in p.parts)

# Things the pre-cut-over host had and the rebuilt one does not. A unit that
# names one is telling a worker to look for something that is not there. Each
# is a whole token: `capture_job.py` is fine, `job.py` is not.
GONE = {
    r"(?<![\w.-])harvest_apply\.py": "the old host loop — `pipeline intake/dispatch/apply` replaced it",
    r"(?<![\w.-])scaffold\.py": "the old note scaffolder — the extractor writes the page",
    r"(?<![\w.-])(intake|job|watch|drain_pending|credentials)\.py": "old host queue scripts",
    r"(?<![\w-])--stage\b": "a unit is invoked `/<unit> ticket=<id>`, in every mode",
    r"<job\.[a-z_.]+>": "there is no job object — a worker reads `ticket.json`",
    r"\bassignment\.json\b(?! or)": "the old slice handoff — `ticket.json` replaced it",
}
# A dated changelog line may name what it removed; that is history, not an instruction.
_HISTORY = re.compile(r"^\s*[-*#>]?\s*`?20\d\d-\d\d-\d\d")


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_shipped_file_names_a_host_that_is_gone(path):
    if path.name == "to_markdown.py":  # restored unchanged; its docstring is its own history
        return
    wrong = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, 1):
        # a changelog entry runs on past its dated first line
        history = any(_HISTORY.match(prev) for prev in lines[max(0, n - 4) : n]) or "ported" in line.lower() or "gone" in line.lower() or "no longer" in line.lower() or "used to" in line.lower() or "WAS " in line
        for pattern, why in GONE.items():
            if re.search(pattern, line) and not history:
                wrong.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:110]} — {why}")
    assert not wrong, "\n".join(wrong)


def test_every_copy_of_the_converter_is_the_same_file():
    """`to_markdown.py` left the plugin at the cut-over, and a unit is
    installed on its own, so each unit that needs it carries a copy. One
    fixed in one unit and not the others is three converters."""
    copies = sorted((ROOT / "skills").glob("*/scripts/to_markdown.py"))
    assert len(copies) >= 2, copies
    digests = {hashlib.sha256(p.read_bytes()).hexdigest() for p in copies}
    assert len(digests) == 1, [str(p.relative_to(ROOT)) for p in copies]


@pytest.mark.parametrize("name", CHANNELS)
def test_a_channel_unit_declares_the_stage_it_documents_and_is_invoked_by_ticket(name):
    """No process ticket ever reaches a unit — every one is the plugin's
    extractor's — so `process` on a manifest stamps a name onto the job that
    nothing runs. And the one invocation is `/<unit> ticket=<id>`."""
    assert unit_manifest(name)["stages"] == ["harvest"], unit_manifest(name)["stages"]
    skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    front = skill.split("---", 2)[1]
    assert re.search(r'^argument-hint:\s*"?ticket=<id>"?\s*$', front, re.M), front
    assert "allowed-tools" not in front and "disable-model-invocation" not in front
    assert re.search(r"^## Stages", skill, re.M) and "report.json" in skill and "ticket.json" in skill


@pytest.mark.parametrize("name", CHANNELS)
def test_a_manifest_carries_no_key_the_contract_does_not_name(name):
    """`skills doctor` FAILS a unit on an unknown manifest key, which is how
    hubspot's `extract` block stopped being a place to put selectors."""
    known = {"v", "name", "version", "kind", "venue", "stages", "uses", "keywords", "usage", "requires", "watch"}
    assert set(json.loads((ROOT / "skills" / name / "manifest.json").read_text())) <= known
