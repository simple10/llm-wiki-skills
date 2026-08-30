"""The manifest agrees with the tree, and every unit's own manifest carries
the keys the package rail needs."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from conftest import MANIFEST, ROOT, SKILLS, unit_manifest

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
