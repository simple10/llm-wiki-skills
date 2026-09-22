"""The harness's own seams, where a wrong answer is an opaque fixture error
rather than a red test."""

from __future__ import annotations

from conftest import NEUTRAL_CWD, ROOT, _cli


def test_the_machine_cli_is_found_beside_a_path_spelled_ops():
    """`LLM_WIKI_OPS` is the console script by absolute path (the dispatch's
    contract, and `which`'s answer): its directory need not be on PATH, so the
    machine CLI is the sibling file, not a bare name."""
    assert _cli(["/opt/ops/bin/llm-wiki-ops"]) == ["/opt/ops/bin/llm-wiki-cli"]
    assert _cli(["llm-wiki-ops"]) == ["llm-wiki-cli"]
    assert _cli(["uv", "run", "--project", "/p", "llm-wiki-ops"]) == ["uv", "run", "--project", "/p", "llm-wiki-cli"]


def test_the_harness_runs_nothing_from_the_checkout():
    """A checkout inside a wiki would bind every call to that wiki."""
    assert NEUTRAL_CWD.is_dir() and not NEUTRAL_CWD.is_relative_to(ROOT) and not ROOT.is_relative_to(NEUTRAL_CWD)
