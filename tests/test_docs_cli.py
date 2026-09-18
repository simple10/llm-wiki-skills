"""Every ops command a unit's docs tell an agent to run is one the real CLI
has: the group, the verb, and each `--flag`, read off `--help`.

The docs are the interface. An agent follows INSTALL.md and SKILL.md to the
letter, so `watch add --slug …` after the CLI became `pipeline add slug=…` is
a unit that does not work, however green its scripts are — and nothing else
here reads prose.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from conftest import ROOT, run

DOCS = sorted([*ROOT.glob("skills/*/*.md"), *ROOT.glob("skills/*/references/*.md"), *ROOT.glob("tactics/*.md"), ROOT / "README.md"])

# Groups the docs still name that the CLI at hand does not have. Each is
# ASSERTED absent below, so the day one is ported this file says to drop it
# from here — and its verbs start being checked like any other.
UNPORTED = {"tactics": "unported on the plugins side"}

# Groups the rebuild retired outright: a span that opens with one is stale
# wherever it appears, prefixed by `llm-wiki-ops` or not.
RETIRED = {"watch"}

# A bare `git pull` is git's, not this CLI's `git` group: a name both own is
# only checked where the span says `llm-wiki-ops` out loud.
SHARED_WITH_THE_SHELL = {"git"}

_INLINE = re.compile(r"`([^`]+)`")
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.M | re.S)
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
_WORD = re.compile(r"^[a-z][a-z-]*$")
_PLUGIN_ADDRESS = re.compile(r"\brun\s+((?:scripts|skills/[\w-]+/scripts)/[\w/.-]+\.py)")


def _spans(text: str):
    """Each command-bearing span, as one line: inline code (which the docs
    wrap across lines) and each logical line of a fenced block."""
    fenced = _FENCE.findall(text)
    for block in fenced:
        for line in block.replace("\\\n", " ").splitlines():
            yield " ".join(line.split("#", 1)[0].split())
    for span in _INLINE.findall(_FENCE.sub("", text)):
        yield " ".join(span.split())


def _commands(text: str):
    """`(group, verb-or-None, flags, span)` for every span that is an ops
    command: prefixed `llm-wiki-ops`, or opening with a group by bare name."""
    for span in _spans(text):
        tokens = span.split()
        if "llm-wiki-ops" in tokens:
            tokens = tokens[tokens.index("llm-wiki-ops") + 1 :]
        elif not (len(tokens) > 1 and _WORD.match(tokens[0]) and _WORD.match(tokens[1])):
            continue  # prose, a path, a manifest key, a script's own argv
        elif tokens[0] in SHARED_WITH_THE_SHELL:
            continue
        tokens = [t for t in tokens if t != "--json"]
        if not tokens or not _WORD.match(tokens[0]):
            continue
        verb = tokens[1] if len(tokens) > 1 and _WORD.match(tokens[1]) else None
        yield tokens[0], verb, _FLAG.findall(" ".join(tokens[1:])), span


@pytest.fixture(scope="session")
def cli(ops, env):
    """`help_of(*path)` — the CLI's own `--help` for a command path, or None
    where it has no such command. Asked once per path."""
    seen: dict = {}

    def help_of(*path: str):
        if path not in seen:
            r = run(ops, env, *path, "--help")
            seen[path] = r.stdout if r.returncode == 0 else None
        return seen[path]

    return help_of


def _subcommands(help_text: str) -> set:
    block = help_text.split("\nCommands:\n", 1)
    return {line.split()[0] for line in block[1].splitlines() if line.startswith("  ") and line.split()} if len(block) > 1 else set()


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_command_the_doc_names_is_one_the_cli_has(cli, doc):
    groups = _subcommands(cli())
    wrong = []
    for group, verb, flags, span in _commands(doc.read_text(encoding="utf-8")):
        if group in RETIRED:
            wrong.append(f"`{span}` — the `{group}` group is retired")
            continue
        if group in UNPORTED or group not in groups:
            continue  # not an ops command at all (`yt-dlp …`, `uv run …`), or checked below
        verbs = _subcommands(cli(group))
        if verbs and verb is not None and verb not in verbs:
            wrong.append(f"`{span}` — `{group}` has no `{verb}` (it has: {', '.join(sorted(verbs))})")
            continue
        if group == "run":
            continue  # everything after the path is the child's own argv
        usage = cli(group, verb) if verbs and verb else cli(group)
        wrong += [f"`{span}` — `{group}{' ' + verb if verb else ''}` takes no `{flag}`" for flag in flags if usage and flag not in usage]
    assert not wrong, f"{doc.relative_to(ROOT)}:\n  " + "\n  ".join(wrong)


@pytest.mark.parametrize("group", sorted(UNPORTED))
def test_an_unported_group_is_still_unported(cli, group):
    assert cli(group) is None, f"the CLI has a `{group}` group now — drop it from UNPORTED so the docs naming it are checked"


def test_every_plugin_script_a_doc_runs_is_in_the_plugin():
    """`llm-wiki-ops run scripts/assets.py` named a file the plugin had moved
    to `skills/harvest/scripts/` — the same break as the transcript formatter,
    in prose instead of code. Checked against the tree `run` serves from."""
    plugin = os.environ.get("LLM_WIKI_OPS_PLUGIN")
    if not plugin:
        pytest.skip("set LLM_WIKI_OPS_PLUGIN to the ops plugin's root — the addresses are paths under it")
    missing = sorted(
        f"{doc.relative_to(ROOT)}: run {rel}"
        for doc in DOCS
        for rel in _PLUGIN_ADDRESS.findall(" ".join(doc.read_text(encoding="utf-8").split()))
        if not (Path(plugin) / rel).is_file()
    )
    assert not missing, "\n  ".join(["a doc runs a plugin script that is not there:", *missing])
