"""Every ops command a unit's docs tell an agent to run is one the real CLI
has: the group, the verb and each `--flag`, read off `--help`, and each dotted
`section.key=` of a job, read off a real job record.

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

from conftest import ROOT, at, run, unit_manifest

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
_COMMENT = re.compile(r"(^|\s)#\s.*$")  # `# a comment`, never the `#400` inside an argument
_CHAINED = re.compile(r"\s(?:&&|\|\||;|\|)\s")
_DOTTED_KEY = re.compile(r"^([a-z_]+)\.([A-Za-z_<>-]+)=")
_BY_PATH = re.compile(r"\S*/bin/llm-wiki-ops\b")  # the CLI, named by a path instead of on PATH
_PLUGIN_ADDRESS = re.compile(r"\brun\s+((?:scripts|skills/[\w-]+/scripts)/[\w/.-]+\.py)")
# The other half of `run`'s namespace: a UNIT's own script, served out of this
# wiki's enabled copy. A doc naming one that is not in the package is the same
# break as a moved plugin script, one tree over.
_UNIT_ADDRESS = re.compile(r"\brun\s+ops/skills/([\w-]+)/scripts/([\w.-]+\.py)")


def _spans(text: str):
    """Each command-bearing span, as one line: inline code (which the docs
    wrap across lines) and each logical line of a fenced block."""
    for block in _FENCE.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            yield from _CHAINED.split(" ".join(_COMMENT.sub("", line).split()))
    for span in _INLINE.findall(_FENCE.sub("", text)):
        yield from _CHAINED.split(" ".join(span.split()))


def _commands(text: str):
    """`(group, second token or None, flags, dotted keys, span)` for every
    span that is an ops command: prefixed `llm-wiki-ops`, or opening with a
    group by bare name. The second token is handed over WHATEVER it is: under
    a group that has verbs it has to be one, and `skills <wiki> list` — the
    retired shape — is caught by exactly that."""
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
        rest = " ".join(tokens[1:])
        keys = [m.groups() for m in map(_DOTTED_KEY.match, tokens[1:]) if m]
        yield tokens[0], tokens[1] if len(tokens) > 1 else None, _FLAG.findall(rest), keys, span


@pytest.fixture(scope="session")
def cli(ops, env, wiki):
    """`help_of(*path)` — the CLI's own `--help` for a command path, or None
    where it has no such command. Asked once per path, inside the wiki: the
    CLI is root-bound and refuses an argv with no wiki behind it, `--help`
    among them."""
    seen: dict = {}

    def help_of(*path: str):
        if path not in seen:
            r = run(ops, at(env, wiki), *path, "--help")
            seen[path] = r.stdout if r.returncode == 0 else None
        return seen[path]

    return help_of


@pytest.fixture(scope="session")
def job_record(ops, env, wiki) -> dict:
    """One real job's record — the sections and keys a dotted `section.key=`
    may name. Asked of the CLI, so a key the schema drops goes red here."""
    slug = "docs-probe"
    r = run(ops, at(env, wiki), "--json", "pipeline", "add", "https://example.invalid/docs", f"slug={slug}", f"dest=sources/scrapes/{slug}", "every=once")
    assert r.returncode == 0, r.stdout + r.stderr
    return run(ops, at(env, wiki), "--json", "pipeline", "show", slug).data["job"]


def _subcommands(help_text: str) -> set:
    block = help_text.split("\nCommands:\n", 1)
    return {line.split()[0] for line in block[1].splitlines() if line.startswith("  ") and line.split()} if len(block) > 1 else set()


def _wrong(cli, job_record, text: str, unit: str | None) -> list:
    """Every stale command in one doc's text, each as a line saying why."""
    groups = _subcommands(cli())
    inputs = set((unit_manifest(unit).get("watch") or {}).get("inputs") or {}) if unit else set()
    wrong = [f"`{hit}` — the CLI run by path; it is the bare `llm-wiki-ops`, on PATH" for hit in _BY_PATH.findall(text)]
    for group, second, flags, keys, span in _commands(text):
        if group in RETIRED:
            wrong.append(f"`{span}` — the `{group}` group is retired")
            continue
        if group in UNPORTED or group not in groups:
            continue  # not an ops command at all (`yt-dlp …`, `uv run …`), or checked below
        verbs = _subcommands(cli(group))
        if verbs and second is not None and second not in verbs:
            wrong.append(f"`{span}` — `{group}` has no `{second}` (it has: {', '.join(sorted(verbs))})")
            continue
        if group == "run":
            continue  # everything after the path is the child's own argv
        path = (group, second) if verbs and second else (group,)
        usage = cli(*path)
        if usage is None:
            wrong.append(f"`{span}` — `{' '.join(path)} --help` failed, so nothing about it could be checked")
            continue
        wrong += [f"`{span}` — `{' '.join(path)}` takes no `{flag}`" for flag in flags if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", usage)]
        if path in (("pipeline", "add"), ("pipeline", "edit")):
            for section, key in keys:
                if section == "options":  # free-form in the record: the unit's own `watch.inputs` are its keys
                    known = key.startswith("<") or key in inputs
                else:
                    known = isinstance(job_record.get(section), dict) and key in job_record[section]
                if not known:
                    wrong.append(f"`{span}` — a job record has no `{section}.{key}`")
    return wrong


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_command_the_doc_names_is_one_the_cli_has(cli, job_record, doc):
    unit = doc.parent.name if doc.parent.parent.name == "skills" else None
    wrong = _wrong(cli, job_record, doc.read_text(encoding="utf-8"), unit)
    assert not wrong, f"{doc.relative_to(ROOT)}:\n  " + "\n  ".join(wrong)


@pytest.mark.parametrize(
    ("text", "caught"),
    [
        ("`llm-wiki-ops watch add --slug x`", "retired"),
        ("`watch add --slug x --dest y`", "retired"),
        ("`llm-wiki-ops skills <wiki> list --json`", "has no `<wiki>`"),
        ("`skills list` reporting it", "has no `list`"),
        ("`llm-wiki-ops skills install x --repo a/b`", "takes no `--repo`"),
        ("`llm-wiki-ops skills enable x --conf`", "takes no `--conf`"),
        ("`llm-wiki-ops pipeline add u slug=s harvest.maxage=3m`", "no `harvest.maxage`"),
        ("`llm-wiki-ops pipeline add u slug=s option.mailbox=m`", "no `option.mailbox`"),
        ("`<ops dir>/bin/llm-wiki-ops tactics install x`", "run by path"),  # no wiki carries a bin/
        ("```\ncd w && llm-wiki-ops skills find x\n```", "has no `find`"),
        ('```\nllm-wiki-ops skills search "ep #400" --bogus\n```', "takes no `--bogus`"),
    ],
)
def test_the_check_itself_catches_each_stale_shape(cli, job_record, text, caught):
    """The guard, pointed at the mistakes it exists for — every one of these
    shipped in this package or was a hole a review found in this file."""
    wrong = _wrong(cli, job_record, text, "channel-gmail")
    assert any(caught in line for line in wrong), wrong


def test_the_check_passes_the_shapes_that_are_right(cli, job_record):
    fine = (
        "`llm-wiki-ops pipeline add gmail slug=s skill=channel-gmail options.mailbox=a@b.c harvest.max_age=3m`\n"
        "`llm-wiki-ops --json skills ls channel-gmail` then a `git pull`, and `yt-dlp --dump-json <url>`\n"
        '```\nllm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py search "ep #400" --type episode\n```\n'
    )
    assert _wrong(cli, job_record, fine, "channel-gmail") == []


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


def test_every_unit_script_a_doc_runs_is_in_the_unit():
    """`run ops/skills/<unit>/scripts/<x>.py` is the unit's own tree. A doc
    naming a script the package does not ship fails at the first real run, and
    `_PLUGIN_ADDRESS` never looked at this half of the namespace."""
    missing = sorted(
        f"{doc.relative_to(ROOT)}: run ops/skills/{unit}/scripts/{name}"
        for doc in DOCS
        for unit, name in _UNIT_ADDRESS.findall(" ".join(doc.read_text(encoding="utf-8").split()))
        if not (ROOT / "skills" / unit / "scripts" / name).is_file()
    )
    assert not missing, "\n  ".join(["a doc runs a unit script that is not there:", *missing])


def test_the_unit_address_scan_finds_the_ones_it_was_written_for():
    """A scan that matches nothing parametrizes nothing and reports green."""
    found = {
        (unit, name)
        for doc in DOCS
        for unit, name in _UNIT_ADDRESS.findall(" ".join(doc.read_text(encoding="utf-8").split()))
    }
    assert ("channel-youtube", "youtube_note.py") in found and len(found) >= 10, sorted(found)
