"""The package harness: every test runs the REAL ops CLI against a throwaway
wiki, with this checkout served as the package.

    LLM_WIKI_OPS   the CLI to run (a command line; default: `llm-wiki-ops-v1`
                   on PATH). Absent → the install tests skip, saying so.

The checkout is symlinked as `marketplaces/<owner>/<repo>` under a tmp
packages home, which the CLI treats as a developer's clone: `latest` is
this checkout's HEAD — commit before you run — and nothing fetches.
`LLM_WIKI_PACKAGES_OFFLINE` is set, so nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "llm-wiki-package.json").read_text(encoding="utf-8"))
SOURCE = MANIFEST["repository"]
SKILLS = [a["name"] for a in MANIFEST["artifacts"] if a["type"] == "skill"]
TACTICS = [a["name"] for a in MANIFEST["artifacts"] if a["type"] == "tactic"]


def unit_manifest(name: str) -> dict:
    art = next(a for a in MANIFEST["artifacts"] if a["type"] == "skill" and a["name"] == name)
    return json.loads((ROOT / art["path"] / "manifest.json").read_text(encoding="utf-8"))


def _ops_argv() -> list | None:
    spec = os.environ.get("LLM_WIKI_OPS")
    if spec:
        return shlex.split(spec)
    exe = shutil.which("llm-wiki-ops-v1")
    return [exe] if exe else None


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def data(self):
        return json.loads(self.stdout)


@pytest.fixture(scope="session")
def ops() -> list:
    argv = _ops_argv()
    if not argv:
        pytest.skip("no ops CLI: set LLM_WIKI_OPS or put llm-wiki-ops-v1 on PATH")
    return argv


@pytest.fixture(scope="session")
def env(tmp_path_factory, ops) -> dict:
    home = tmp_path_factory.mktemp("packages-home")
    mp = home / "marketplaces" / SOURCE
    mp.parent.mkdir(parents=True)
    mp.symlink_to(ROOT, target_is_directory=True)
    e = dict(os.environ)
    e.pop("LLM_WIKI_OPS_DIRNAME", None)
    e.update(
        LLM_WIKI_PACKAGES_HOME=str(home),
        LLM_WIKI_PACKAGES_OFFLINE="1",
        LLM_WIKI_MACHINE_CONFIG=str(home / "no-machine-config.toml"),
        GIT_AUTHOR_NAME="harness",
        GIT_AUTHOR_EMAIL="harness@example.invalid",
        GIT_COMMITTER_NAME="harness",
        GIT_COMMITTER_EMAIL="harness@example.invalid",
    )
    return e


def run(ops: list, env: dict, *args) -> Result:
    cp = subprocess.run([*ops, *args], env=env, capture_output=True, text=True, check=False)
    return Result(cp.returncode, cp.stdout, cp.stderr)


@pytest.fixture(scope="session")
def wiki(tmp_path_factory, ops, env) -> Path:
    """One `init`ed wiki for the session — installs accumulate in it, which
    is what a real wiki does."""
    w = tmp_path_factory.mktemp("wiki") / "w"
    r = run(ops, env, "init", str(w), "--preset", "general", "--commit")
    assert r.returncode == 0, r.stderr
    return w
