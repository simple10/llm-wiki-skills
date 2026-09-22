"""What has to hold across EVERY unit, so one unit's port cannot quietly
drift from the rest."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from conftest import MANIFEST, ROOT, SKILLS, unit_manifest

CHANNELS = [n for n in SKILLS if unit_manifest(n).get("kind") == "channel"]
SHIPPED = sorted(p for p in (ROOT / "skills").rglob("*") if p.is_file() and p.suffix in {".md", ".py", ".json"} and "__pycache__" not in p.parts)

# Things the pre-cut-over host had and the rebuilt one does not. A unit that
# names one is telling a worker to look for something that is not there. Each
# is a whole token: `capture_job.py` is fine, `job.py` is not.
GONE = {
    r"(?<![\w.-])harvest_apply\.py": "the old host loop — `pipeline intake/dispatch/apply` replaced it",
    r"(?<![\w.-])scaffold\.py": "the old note scaffolder — the extractor writes the page",
    r"(?<![\w.-])(intake|job|watch|drain_pending|credentials)\.py": "old host queue scripts",
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
def test_a_channel_unit_declares_both_stages_it_documents_and_is_invoked_by_ticket(name):
    """A channel unit renders its own venue's page, so it holds the pen at
    process too: the manifest declares both stages, the body documents both,
    and the one invocation for either is `/<unit> ticket=<id>`."""
    assert unit_manifest(name)["stages"] == ["harvest", "process"], unit_manifest(name)["stages"]
    skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    front = skill.split("---", 2)[1]
    assert re.search(r'^argument-hint:\s*"ticket=<id> stage=harvest\|process"\s*$', front, re.M), front
    assert unit_manifest(name)["usage"] == f"/{name} ticket=<id> stage=harvest|process"
    assert "allowed-tools" not in front and "disable-model-invocation" not in front
    assert re.search(r"^## Stages", skill, re.M) and "report.json" in skill and "ticket.json" in skill
    assert re.search(r"^### harvest\b", skill, re.M) and re.search(r"^### process\b", skill, re.M), skill[:400]


@pytest.mark.parametrize("name", CHANNELS)
def test_either_step_of_a_unit_opens_with_the_policy_read(name):
    """A wiki steers a unit through its overlays, never by editing the unit:
    `reference skill-authoring` has either step open with `policy get <stage>
    <unit>` — the stage's overlay, then the unit's own, in one call. That
    two-name form is ops 1.95's, and the package's `min_ops_version` is past
    it: a unit's own floor must not fall under the package's, or a unit
    spelling the read would claim to run on a CLI that refuses it."""
    skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    stages = skill.split("\n## Stages", 1)[1].split("\n### harvest", 1)[0]
    assert f"\n```sh\nllm-wiki-ops policy get <stage> {name}\n```\n" in stages, f"{name}: the Stages intro does not open with the policy read"
    version = lambda text: tuple(int(b) for b in text.removeprefix(">=").split("."))  # noqa: E731
    floor = unit_manifest(name)["requires"]["ops"]
    assert version(floor) >= version(MANIFEST["min_ops_version"]), f"{name}: requires.ops {floor} is under the package's min_ops_version"


# A skill is a command sequence: what to run, in what order, what the output
# means, what to do when it is wrong. The reasoning has one home, the plugin's
# references, which a unit names and does not restate. Every unit tripled in
# the port (#12); this is 1.5x each unit's length at `99aac80`, the `main`
# before the port, and a unit that grows past it is restating something. The
# quirks log is the one section meant to grow, one line per venue fact, and
# is not counted.
BUDGET = {
    "channel-circle": 312,
    "channel-frameio": 345,
    "channel-gmail": 183,
    "channel-hubspot-video": 204,
    "channel-notion-tasks": 139,
    "channel-spotify": 246,
    "channel-substack": 288,
    "channel-youtube": 198,
}


@pytest.mark.parametrize("name", CHANNELS)
def test_a_unit_stays_under_its_budget(name):
    skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    lines = len(skill.split("# Quirks log\n", 1)[0].splitlines())
    assert lines <= BUDGET[name], f"{name}: {lines} lines before the quirks log, budget {BUDGET[name]} — the reasoning belongs in a reference"


@pytest.mark.parametrize("name", CHANNELS)
def test_a_quirks_log_is_dated_one_liners(name):
    """One dated line per venue fact, in one shape across the units, never a
    narrative of the port: `- YYYY-MM-DD — <the fact>`. Read to the end of
    the file, so the log stays the last section."""
    skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    log = skill.split("# Quirks log\n", 1)[1] if "# Quirks log\n" in skill else ""
    entries = [line for line in log.splitlines() if line.startswith("- ")]
    undated = [line for line in entries if not re.match(r"^- 20\d\d-\d\d-\d\d — \S", line)]
    assert not undated, f"{name}: {undated}"


@pytest.mark.parametrize("name", CHANNELS)
def test_the_step_a_unit_is_in_is_an_argument_not_a_file(name):
    """One discriminator: the `stage=` the host composes into the prompt. A
    process ticket's capture dir IS the download ticket's for a single-item
    job, and `ticket.json` lands there under one name — so a step read off
    that file can be the other step's, and the prompt cannot be clobbered."""
    skill = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"stage=harvest\|process", skill), name
    assert re.search(r"\$ARGUMENTS", skill), name


@pytest.mark.parametrize("name", SKILLS)
def test_a_unit_is_reachable_by_the_invocation_its_spawner_types(name):
    """The spawner's whole prompt for a slice is `/<unit> ticket=<id>`. With
    `user-invocable: false` the harness answers that with no model turn at
    all — exit 0, no report — so the key that would make a unit undispatchable
    is refused here, beside the two the authoring contract already names."""
    front = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    for refused in ("user-invocable:", "disable-model-invocation:", "allowed-tools:"):
        assert refused not in front, f"{name}: {refused} makes the unit unreachable by `/{name} ticket=<id>`"


@pytest.mark.parametrize("name", CHANNELS)
def test_a_manifest_carries_no_key_the_contract_does_not_name(name):
    """`skills doctor` FAILS a unit on an unknown manifest key, which is how
    hubspot's `extract` block stopped being a place to put selectors."""
    known = {"v", "name", "version", "kind", "venue", "stages", "uses", "keywords", "usage", "requires", "watch"}
    assert set(json.loads((ROOT / "skills" / name / "manifest.json").read_text())) <= known


@pytest.mark.parametrize("name", SKILLS)
def test_every_script_a_unit_ships_is_one_something_runs(name):
    """A unit's scripts are its whole executable surface, and `run` is the only
    door to one. A file no `run` line names and no sibling imports is code that
    ships, installs and enables while nothing can reach it."""
    unit = ROOT / "skills" / name
    scripts = sorted(p for p in unit.glob("scripts/*.py") if "__pycache__" not in p.parts)
    if not scripts:
        return
    skill = (unit / "SKILL.md").read_text(encoding="utf-8")
    run_lines = "\n".join(line for line in skill.splitlines() if "llm-wiki-ops run" in line or "run ops/skills/" in line)
    dead = []
    for script in scripts:
        if script.name in run_lines:
            continue
        # A CALL, not a mention: a sibling imports the module, or puts the
        # file name on an argv. A name in a comment or a docstring reaches
        # nothing, and that is exactly how dead code reads as alive.
        call = re.compile(
            rf"^\s*(?:import|from)\s+{re.escape(script.stem)}(?![\w-])|[\"']{re.escape(script.name)}[\"']",
            re.M,
        )
        if not any(call.search(_code(other)) for other in scripts if other != script):
            dead.append(str(script.relative_to(ROOT)))
    assert not dead, "no `run` line names these and no sibling calls them: " + ", ".join(dead)


def _code(path) -> str:
    """One script with its module docstring and its comments removed: what a
    name has to survive in to count as a call."""
    text = re.sub(r'^\s*"""[\s\S]*?"""', "", path.read_text(encoding="utf-8"), count=1)
    return "\n".join(re.sub(r"(?<!:)#.*$", "", line) for line in text.splitlines())


# The module-level names a shared function reads: part of the piece of code,
# outside the `def` — `_TITLE_SWAPS` changed in the port and the scan that
# compared from `def safe_title(` on could not see it.
READS = {"safe_title": ("_TITLE_SWAPS", "TITLE_MAX", "TITLE_MAX_BYTES")}


def _function(path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    found = re.search(rf"^def {name}\(.*?(?=^\S)", text, re.M | re.S)
    if not found:
        return ""
    reads = [re.search(rf"^{constant} = (.*?)(?:\s+#.*)?$", text, re.M) for constant in READS.get(name, ())]  # the value, not its comment
    return "\n".join([*(f"{c} = {m.group(1).strip()}" if m else f"{c} = <absent>" for m, c in zip(reads, READS.get(name, ()))), found.group(0).strip()])


def _holders(name: str) -> list:
    return sorted(p for p in (ROOT / "skills").glob("*/scripts/*.py") if _function(p, name))


@pytest.mark.parametrize(("name", "at_least"), [("safe_title", 6), ("page_key", 4), ("qualifier", 4), ("unique_title", 4), ("front_door", 9)])
def test_code_the_units_share_by_copying_is_one_piece_of_code(name, at_least):
    """A unit is installed on its own, so what several need is COPIED into
    each — and a copy fixed in one unit and not the rest is a wiki whose pages
    are named by two rules. `safe_title` is what makes a venue's title a name
    the host's filename rule will hold; the next three are the pass that keeps
    two leaves with one title from being one page; `front_door` is how every
    unit finds the CLI, and a copy that reads PATH alone is the one that dies
    in a slice."""
    holders = _holders(name)
    assert len(holders) >= at_least, [str(p.relative_to(ROOT)) for p in holders]
    bodies = {_function(p, name) for p in holders}
    assert len(bodies) == 1, f"`{name}` differs between: " + ", ".join(str(p.relative_to(ROOT)) for p in holders)


def test_the_shared_title_rule_holds_what_the_host_refuses():
    """The rule itself, once, against everything found the hard way: the
    host's ILLEGAL set and leading dot (it refuses the whole process ticket),
    a name too long in BYTES (the extractor dies on the filesystem's limit),
    its one reserved page name, and venue text that would forge a line."""
    import importlib.util

    path = ROOT / "skills" / "channel-substack" / "scripts" / "capture_posts.py"
    spec = importlib.util.spec_from_file_location("_shared_title_rule", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    safe = mod.safe_title
    for title in ("Lesson 3: Pricing", "What is X?", "A/B testing", ".hidden", 'He said "no" <once> | twice*', "a\\b"):
        got = safe(title)
        assert got and not got.startswith(".") and not set(got) & set('/\\:*?"<>|'), (title, got)
    assert safe("Line one\n---\n# Forged") == "Line one --- # Forged"
    # An ASCII apostrophe cannot survive: three units hand the title to a shell
    # line, where a value carrying one cannot be quoted and the page is lost.
    for title in ("Don't Panic", 'He said "no"', "Rock \u2019n\u2019 roll"):
        assert "'" not in safe(title), (title, safe(title))
    assert safe("Don't Panic") == "Don\u2019t Panic" and safe('He said "no"') == "He said \u2019no\u2019"
    assert len((safe("語" * 100) + ".md").encode("utf-8")) <= 255
    assert safe("index") != "index" and safe("Index").casefold() != "index"
    assert safe("") == "Untitled" and safe("   ", fallback="x") == "x" and safe(None) == "Untitled"

