"""Every ops command a unit's docs tell an agent to run is one the real CLI
has: the group, the verb and each `--flag`, read off `--help`, and each dotted
`section.key=` of a job, read off a real job record.

The docs are the interface. An agent follows SKILL.md and its references to
the letter, so `watch add --slug …` after the CLI became `pipeline add
slug=…` is a unit that does not work, however green its scripts are — and
nothing else here reads prose.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from harness import ROOT, _cli, rooted, run, unit_manifest

DOCS = sorted([*ROOT.glob("skills/*/*.md"), *ROOT.glob("skills/*/references/*.md"), ROOT / "README.md"])

# Groups the docs still name that the CLI at hand does not have. Each is
# ASSERTED absent below, so the day one is ported this file says to drop it
# from here — and its verbs start being checked like any other.
UNPORTED: dict = {}

# Groups the rebuild retired outright: a span that opens with one is stale
# wherever it appears, prefixed by `llm-wiki-ops` or not.
RETIRED = {"watch", "tactics"}

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
_BY_PATH = re.compile(r"\S*/bin/llm-wiki-(?:ops|cli)\b")  # a CLI, named by a path instead of on PATH
# A verb the CLI kept as a stub purely to name where it moved (`ls Retired:
# moved to \`pipeline jobs ls\`.`), read off the stub's own `--help`. Caught
# generically, so a group retired this way needs no entry of its own here.
_RETIRED_STUB = re.compile(r"Retired: moved to `[^`]+`\.")
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
    """`(machine, group, second token or None, third token or None, flags,
    dotted keys, span)` for every span that is a CLI command: prefixed
    `llm-wiki-ops`, prefixed `llm-wiki-cli` (`machine` is then True — the
    other console script, whose groups are its own), or opening with an ops
    group by bare name. The second and third tokens are handed over
    WHATEVER they are: under a group that has verbs the second has to be
    one, `pipeline jobs`/`pipeline tickets` nest a third, and `skills <wiki>
    list` — the retired shape — is caught by exactly that."""
    for span in _spans(text):
        tokens = span.split()
        machine = "llm-wiki-cli" in tokens
        if machine:
            tokens = tokens[tokens.index("llm-wiki-cli") + 1 :]
            if tokens[:1] == ["wiki"]:
                # `wiki [--read-only] <key|--here> <ops argv...>`: the machine
                # CLI's one scope that is not a command of its own — what
                # follows the key is an ops argv, checked as one.
                machine = False
                tokens = tokens[3 if tokens[1:2] == ["--read-only"] else 2 :]
        elif "llm-wiki-ops" in tokens:
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
        second = tokens[1] if len(tokens) > 1 else None
        third = tokens[2] if len(tokens) > 2 else None
        yield machine, tokens[0], second, third, _FLAG.findall(rest), keys, span


@pytest.fixture(scope="session")
def cli(ops, env, wiki):
    """`help_of(*path, machine=False)` — the CLI's own `--help` for a command
    path, or None where it has no such command; `machine=True` asks the
    machine CLI (`llm-wiki-cli`) instead. Asked once per path, inside the
    wiki: the ops CLI is root-bound and refuses an argv with no wiki behind
    it, `--help` among them."""
    seen: dict = {}

    def help_of(*path: str, machine: bool = False):
        if (machine, path) not in seen:
            r = run(_cli(ops) if machine else ops, rooted(env, wiki), *path, "--help")
            seen[machine, path] = r.stdout if r.returncode == 0 else None
        return seen[machine, path]

    return help_of


@pytest.fixture(scope="session")
def job_record(ops, env, wiki) -> dict:
    """One real job's record — the sections and keys a dotted `section.key=`
    may name. Asked of the CLI, so a key the schema drops goes red here."""
    slug = "docs-probe"
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "add", "https://example.invalid/docs", f"slug={slug}", f"dest=sources/scrapes/{slug}", "every=once")
    assert r.returncode == 0, r.stdout + r.stderr
    return run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "show", slug).data["job"]


def _subcommands(help_text: str) -> set:
    block = help_text.split("\nCommands:\n", 1)
    return {line.split()[0] for line in block[1].splitlines() if line.startswith("  ") and line.split()} if len(block) > 1 else set()


def _wrong(cli, job_record, text: str, unit: str | None) -> list:
    """Every stale command in one doc's text, each as a line saying why."""
    groups = _subcommands(cli())
    machine_groups = _subcommands(cli(machine=True))
    inputs = set((unit_manifest(unit).get("watch") or {}).get("inputs") or {}) if unit else set()
    wrong = [f"`{hit}` — the CLI run by path; it is the bare name, on PATH" for hit in _BY_PATH.findall(text)]
    for machine, group, second, third, flags, keys, span in _commands(text):
        if group in RETIRED:
            wrong.append(f"`{span}` — the `{group}` group is retired")
            continue
        if machine:
            # The prefix is explicit, so a group the machine CLI lacks is stale, not
            # prose. A bare `machine doctor` is left alone: `machine` is no ops group,
            # and an author writing the machine CLI writes its name.
            if group not in machine_groups:
                wrong.append(f"`{span}` — `llm-wiki-cli` has no `{group}` (it has: {', '.join(sorted(machine_groups))})")
                continue
        elif group in UNPORTED or group not in groups:
            continue  # not an ops command at all (`yt-dlp …`, `uv run …`), or checked below
        verbs = _subcommands(cli(group, machine=machine))
        if verbs and second is not None and second not in verbs:
            wrong.append(f"`{span}` — `{group}` has no `{second}` (it has: {', '.join(sorted(verbs))})")
            continue
        if group == "run":
            continue  # everything after the path is the child's own argv
        path = (group, second) if verbs and second else (group,)
        # A verb may itself be a group nesting a verb of its own — `pipeline
        # jobs`, `pipeline tickets`, `pipeline queue` — found generically by
        # asking whether IT has subcommands, rather than a fixed list of
        # names; the flag and dotted-key checks below then run against the
        # nested verb's own `--help`.
        subverbs = _subcommands(cli(*path, machine=machine)) if len(path) == 2 else set()
        if subverbs:
            if third is not None and third not in subverbs:
                wrong.append(f"`{span}` — `{' '.join(path)}` has no `{third}` (it has: {', '.join(sorted(subverbs))})")
                continue
            if third is not None:
                path = (*path, third)
        usage = cli(*path, machine=machine)
        if usage is None:
            wrong.append(f"`{span}` — `{' '.join(path)} --help` failed, so nothing about it could be checked")
            continue
        retired = _RETIRED_STUB.search(usage)
        if retired:
            wrong.append(f"`{span}` — {retired.group(0).strip()}")
            continue
        wrong += [f"`{span}` — `{' '.join(path)}` takes no `{flag}`" for flag in flags if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", usage)]
        if path in (("pipeline", "jobs", "add"), ("pipeline", "jobs", "edit")):
            for section, key in keys:
                if section == "options":  # free-form in the record: the unit's own `watch.inputs` are its keys
                    known = key.startswith("<") or key in inputs
                else:
                    known = isinstance(job_record.get(section), dict) and key in job_record[section]
                if not known:
                    wrong.append(f"`{span}` — a job record has no `{section}.{key}`")
    return wrong


# Verbs the docs already name ahead of plugins PR 2 (#2486) landing them —
# `run`/`close` under `pipeline tickets`. A doc's ONLY wrongness being one of
# these is that PR's, not this one's; anything else in the same doc still
# fails normally.
_PR2_PENDING = ("`pipeline tickets` has no `run`", "`pipeline tickets` has no `close`")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_command_the_doc_names_is_one_the_cli_has(cli, job_record, doc):
    # `skills/<unit>/SKILL.md` and `skills/<unit>/references/*.md` both name a unit.
    parts = doc.relative_to(ROOT).parts
    unit = parts[1] if parts[0] == "skills" else None
    wrong = _wrong(cli, job_record, doc.read_text(encoding="utf-8"), unit)
    other = [line for line in wrong if not any(p in line for p in _PR2_PENDING)]
    assert not other, f"{doc.relative_to(ROOT)}:\n  " + "\n  ".join(other)
    if wrong:
        pytest.skip(f"{len(wrong)} command(s) are plugins PR 2 (#2486): `pipeline tickets run`/`close`")


@pytest.mark.parametrize(
    ("text", "caught"),
    [
        ("`llm-wiki-ops watch add --slug x`", "retired"),
        ("`watch add --slug x --dest y`", "retired"),
        ("`llm-wiki-ops skills <wiki> list --json`", "has no `<wiki>`"),
        ("`skills list` reporting it", "has no `list`"),
        ("`llm-wiki-ops skills install x --repo a/b`", "takes no `--repo`"),
        ("`llm-wiki-ops skills enable x --conf`", "takes no `--conf`"),
        ("`llm-wiki-ops pipeline jobs add u slug=s harvest.maxage=3m`", "no `harvest.maxage`"),
        ("`llm-wiki-ops pipeline jobs add u slug=s option.mailbox=m`", "no `option.mailbox`"),
        ("`llm-wiki-ops pipeline add u slug=s`", "Retired: moved to `pipeline jobs add`"),
        ("`llm-wiki-ops pipeline queue show x`", "Retired: moved to `pipeline tickets show`"),
        pytest.param(
            "`llm-wiki-ops pipeline apply x`", "retired",
            marks=pytest.mark.skip(reason="`apply` folds into `close` in plugins PR 2 (#2486); still real on main"),
        ),
        pytest.param(
            "`llm-wiki-ops pipeline extract x`", "retired",
            marks=pytest.mark.skip(reason="`extract` folds into `run` in plugins PR 2 (#2486); still real on main"),
        ),
        ("`<ops dir>/bin/llm-wiki-ops skills ls`", "run by path"),  # no wiki carries a bin/
        ("```\ncd w && llm-wiki-ops skills find x\n```", "has no `find`"),
        ('```\nllm-wiki-ops skills search "ep #400" --bogus\n```', "takes no `--bogus`"),
        ("`llm-wiki-cli machine sweep`", "has no `sweep`"),  # the other console script's verbs are checked too
        ("`llm-wiki-cli skills ls`", "`llm-wiki-cli` has no `skills`"),  # a wiki verb under the machine prefix
        ("`llm-wiki-cli init <dir> --preset x`", "takes no `--preset`"),
        ("`llm-wiki-cli wiki <key> skills list`", "has no `list`"),  # the wiki scope's argv is an ops argv
        ("`<ops dir>/bin/llm-wiki-cli machine doctor`", "run by path"),
    ],
)
def test_the_check_itself_catches_each_stale_shape(cli, job_record, text, caught):
    """The guard, pointed at the mistakes it exists for — every one of these
    shipped in this package or was a hole a review found in this file."""
    wrong = _wrong(cli, job_record, text, "channel-gmail")
    assert any(caught in line for line in wrong), wrong


def test_the_check_passes_the_shapes_that_are_right(cli, job_record):
    fine = (
        "`llm-wiki-ops pipeline jobs add gmail slug=s skill=channel-gmail options.mailbox=a@b.c harvest.max_age=3m`\n"
        "`llm-wiki-ops --json skills ls channel-gmail` then a `git pull`, and `yt-dlp --dump-json <url>`\n"
        '```\nllm-wiki-ops run ops/skills/channel-spotify/scripts/spotify.py search "ep #400" --type episode\n```\n'
        "`llm-wiki-cli init <dir> key=<key>` once, then `llm-wiki-cli machine doctor`\n"
        "`llm-wiki-cli wiki <key> skills ls` and `llm-wiki-cli wiki --read-only --here --json pipeline tickets show <slug>`\n"
        "`llm-wiki-ops pipeline tickets open <id>` then `llm-wiki-ops pipeline tickets update <id> stage=harvest status=ok`\n"
    )
    assert _wrong(cli, job_record, fine, "channel-gmail") == []


@pytest.mark.parametrize("group", sorted(UNPORTED))
def test_an_unported_group_is_still_unported(cli, group):
    assert cli(group) is None, f"the CLI has a `{group}` group now — drop it from UNPORTED so the docs naming it are checked"


# G3: `assets.py`, `published_date.py`, `toolcheck.py` and
# `format_transcript.py` move to the plugin's `scripts/` in plugins PR 4.
# The docs already address them there, ahead of the move (G3), so a miss
# that is only one of these four is that PR's, not this one's.
_G3_HELPERS = {"scripts/assets.py", "scripts/published_date.py", "scripts/toolcheck.py", "scripts/format_transcript.py"}


def test_every_plugin_script_a_doc_runs_is_in_the_plugin():
    """`llm-wiki-ops run scripts/assets.py` named a file the plugin had moved
    to `skills/harvest/scripts/` — the same break as the transcript formatter,
    in prose instead of code. Checked against the tree `run` serves from."""
    plugin = os.environ.get("LLM_WIKI_OPS_PLUGIN")
    if not plugin:
        pytest.skip("set LLM_WIKI_OPS_PLUGIN to the ops plugin's root — the addresses are paths under it")
    missing = sorted(
        {
            (f"{doc.relative_to(ROOT)}: run {rel}", rel)
            for doc in DOCS
            for rel in _PLUGIN_ADDRESS.findall(" ".join(doc.read_text(encoding="utf-8").split()))
            if not (Path(plugin) / rel).is_file()
        }
    )
    other = [line for line, rel in missing if rel not in _G3_HELPERS]
    assert not other, "\n  ".join(["a doc runs a plugin script that is not there:", *other])
    if missing:
        pytest.skip(f"{len(missing)} address(es) move to the plugin's scripts/ in plugins PR 4 (G3)")


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
