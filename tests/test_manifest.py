"""The manifest agrees with the tree, and every unit's own manifest carries
the keys the package rail needs."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from harness import MANIFEST, ROOT, SKILLS, unit_manifest

_FLOOR = re.compile(r"^>=\d+\.\d+(\.\d+)?$")


def test_check_manifest_passes_on_the_tree():
    cp = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-manifest.py")], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr


@pytest.mark.parametrize("name", [n for n in SKILLS if unit_manifest(n).get("kind") == "channel"])
def test_every_channel_unit_declares_its_ops_floor(name):
    floor = (unit_manifest(name).get("requires") or {}).get("ops")
    assert isinstance(floor, str) and _FLOOR.match(floor), f"{name}: requires.ops is {floor!r}"


def test_min_ops_version_is_a_bare_version():
    assert re.match(r"^\d+\.\d+\.\d+$", MANIFEST["min_ops_version"])


def _version(text: str) -> tuple:
    return tuple(int(b) for b in text.removeprefix(">=").split("."))


@pytest.mark.parametrize("name", SKILLS)
def test_every_units_floor_is_at_or_above_the_packages(name):
    """`test_either_step_of_a_unit_opens_with_the_policy_read` pins this for
    the eight channel units, from their own `## Stages` intro; a `script`
    unit like `web-page` opens neither step with a policy read, so nothing
    else checks its floor. A unit spelling a lower `requires.ops` would claim
    to run on an ops CLI that `check-manifest.py` never rejects it for."""
    floor = unit_manifest(name)["requires"]["ops"]
    assert _version(floor) >= _version(MANIFEST["min_ops_version"]), f"{name}: requires.ops {floor} is under the package's min_ops_version"
