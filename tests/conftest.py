"""The harness fixtures: the CLI, the environment and the session wiki every
install-tier case runs against. The code is `harness.py`'s.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness import ROOT, SOURCE, _cli, _ops_argv, run


@pytest.fixture(scope="session")
def ops() -> list:
    argv = _ops_argv()
    if not argv:
        pytest.skip("no ops CLI: set LLM_WIKI_OPS or put llm-wiki-ops on PATH")
    return argv


@pytest.fixture(scope="session")
def env(tmp_path_factory, ops) -> dict:
    home = tmp_path_factory.mktemp("packages-home")
    mp = home / "marketplaces" / SOURCE
    mp.parent.mkdir(parents=True)
    mp.symlink_to(ROOT, target_is_directory=True)
    e = dict(os.environ)
    # A suite started from inside a wiki session must not act on THAT wiki:
    # `LLM_WIKI_ROOT` binds one ahead of the `cwd=` a `run` case uses, and
    # `CLAUDE_PROJECT_DIR` — the harness's project dir, never a wiki root, and
    # read by no CLI — goes too, so nothing downstream mistakes it for one.
    for ambient in ("LLM_WIKI_ROOT", "CLAUDE_PROJECT_DIR"):
        e.pop(ambient, None)
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


@pytest.fixture(scope="session")
def wiki(tmp_path_factory, ops, env) -> Path:
    """One `init`ed wiki for the session — installs accumulate in it, which
    is what a real wiki does."""
    w = tmp_path_factory.mktemp("wiki") / "w"
    # `key=` names the wiki in the machine registry, which the env above points
    # at a throwaway config, so nothing on the developer's machine is touched.
    r = run(_cli(ops), env, "init", str(w), "key=harness", "preset=general")  # values are key=value; init commits on its own
    assert r.returncode == 0, r.stderr
    return w
