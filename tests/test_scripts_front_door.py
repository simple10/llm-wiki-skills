"""The unit scripts that ask the front door a question read its ANSWER, not
just its exit code.

`llm-wiki-ops credential profile-dir` and `credential get` print prose for a
person unless asked for `--json`, and `profile-dir` exits 0 for a profile that
does not exist. A caller that trusts the exit code and takes stdout for a
path launches a browser on a directory named after three lines of prose —
silently logged out. Each stub below answers in the real CLI's own shapes
(captured from ops 1.88.3), prose included, so a caller that forgets `--json`
fails here instead of on a wiki.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import stat
import sys
import types
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"

PROFILE = "/home/u/.config/llm-wiki/credentials/realm/profiles/community.example"
PROFILE_PROSE = f"domain: community.example\npath: {PROFILE}\nexists: no\n"
# NOT the CLI's own refusal shape — outside a wiki it prints the prose on
# stderr, nothing on stdout even under `--json`, and exits 2. The scripts'
# no-wiki arm is read off the exit status, so this stub only has to refuse.
NO_WIKI = {"error": "no wiki here — run `llm-wiki-cli wiki <key> ...` to reach one, or `llm-wiki-cli init <dir>` to make one"}
ABSENT = {"error": "no credential 'spotify' on this machine"}
STORED = {"client_id": "cid", "client_secret": "sec"}


def _load(unit: str, script: str, *stubbed: str):
    """Import a unit script in-process, satisfying the PEP 723 dependencies
    the test venv does not carry. Removes exactly the stubs it inserted."""
    inserted = [n for n in stubbed if n not in sys.modules]
    for name in inserted:
        sys.modules[name] = types.ModuleType(name)
    try:
        spec = importlib.util.spec_from_file_location(f"_front_door_{unit}_{script}", SKILLS / unit / "scripts" / script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name in inserted:
            sys.modules.pop(name, None)


@pytest.fixture
def front_door(tmp_path, monkeypatch):
    """`answer(rc, json_answer, prose)` puts a recording `llm-wiki-ops` first
    on PATH. It answers `json_answer` when asked `--json` and `prose`
    otherwise — the real CLI's two voices; `honors_json=False` is a CLI that
    only has the one — and records what it was handed at `answer.seen`. The
    ambient env carries what a hosted script really inherits."""
    bin_dir, seen = tmp_path / "stub-bin", tmp_path / "seen.json"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    # What a hosted run exports: the front door it was itself reached by. A
    # script that honors it must reach THIS stub, not whatever the suite runs.
    monkeypatch.setenv("LLM_WIKI_OPS", str(bin_dir / "llm-wiki-ops"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "some-other-wiki"))

    def answer(rc: int, json_answer: dict, prose: str = "prose for a person\n", honors_json: bool = True):
        stub = bin_dir / "llm-wiki-ops"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': sys.stdin.read() if not sys.stdin.isatty() else '',\n"
            f"           'inherited': sorted(k for k in ('CLAUDE_PROJECT_DIR',) if k in os.environ)}},\n"
            f"          open({str(seen)!r}, 'w'))\n"
            f"sys.stdout.write({json.dumps(json_answer)!r} if {honors_json!r} and '--json' in sys.argv else {prose!r})\n"
            f"sys.exit({rc})\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        return lambda: json.loads(seen.read_text())

    answer.seen = seen
    return answer


# --- channel-circle: both scripts carry the same `profile_dir` -----------------


@pytest.fixture(params=["capture_lesson.py", "outage_probe.py"])
def circle(request):
    return _load("channel-circle", request.param)


def test_a_minted_profile_is_the_path_the_cli_named(circle, front_door, tmp_path):
    seen = front_door(0, {"domain": "community.example", "path": PROFILE, "exists": True}, PROFILE_PROSE)
    path, refused = circle.profile_dir(tmp_path, "community.example")
    assert (path, refused) == (Path(PROFILE), None)
    got = seen()
    assert got["argv"] == ["--json", "credential", "profile-dir", "community.example"], got
    assert Path(got["cwd"]) == tmp_path.resolve(), got
    # not the variable the front door binds to AHEAD of the cwd (another
    # wiki's credential realm)
    assert got["inherited"] == [], got


def test_a_profile_no_login_has_minted_is_absent_though_the_cli_exits_zero(circle, front_door, tmp_path):
    """Exit 0, `exists: false`. Reading the exit code alone — or stdout as a
    path — launches a persistent context that CREATES the directory and runs
    the whole capture logged out, reporting nothing."""
    front_door(0, {"domain": "community.example", "path": PROFILE, "exists": False}, PROFILE_PROSE)
    path, refused = circle.profile_dir(tmp_path, "community.example")
    assert path is None and refused[0] == "absent", (path, refused)


def test_a_cli_that_could_not_answer_is_unreachable_not_absent(circle, front_door, tmp_path):
    """Exit 1 is every "could not answer". It used to be read as "no auth
    profile — run login.py", which sends an operator to log in to fix a wiki
    that is not there."""
    front_door(1, NO_WIKI)
    path, refused = circle.profile_dir(tmp_path, "community.example")
    assert path is None and refused[0] == "unreachable" and "no wiki here" in refused[1], refused


def test_prose_on_stdout_is_never_taken_for_a_path(circle, front_door, tmp_path):
    """A CLI that answers prose even to `--json` — an older one — is an
    unreachable store, never `Path("domain: …\\npath: …")`."""
    front_door(0, {}, PROFILE_PROSE, honors_json=False)
    path, refused = circle.profile_dir(tmp_path, "community.example")
    assert path is None and refused[0] == "unreachable", (path, refused)


# --- channel-spotify: `credential get|set spotify` ------------------------------


@pytest.fixture
def spotify(monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    return _load("channel-spotify", "spotify.py", "requests")


def test_a_stored_credential_is_the_payload_inside_the_answer(spotify, front_door, tmp_path):
    """`get --json` answers `{name, value, store}` and `value` is the stored
    TEXT. Parsing the answer itself as the payload yields a dict with no
    `client_id`, and the capture goes keyless with credentials on the box."""
    seen = front_door(0, {"name": "spotify", "value": json.dumps(STORED, indent=1), "store": "/s"}, "name: spotify\nvalue: {\n")
    assert spotify.load_auth(tmp_path) == STORED
    got = seen()
    assert got["argv"] == ["--json", "credential", "get", "spotify"] and got["inherited"] == [], got
    assert Path(got["cwd"]) == tmp_path.resolve(), got


def test_an_absent_credential_degrades_keyless(spotify, front_door, tmp_path):
    front_door(1, ABSENT, "no credential 'spotify' on this machine\n")
    assert spotify.load_auth(tmp_path) == {}


def test_any_other_failure_is_an_error_not_a_quiet_keyless_run(spotify, front_door, tmp_path, capsys):
    """Same exit code as absent. Degrading here ships a truncated, keyless
    capture from a box that HAS credentials and could not read them."""
    front_door(1, NO_WIKI)
    with pytest.raises(SystemExit):
        spotify.load_auth(tmp_path)
    assert "no wiki here" in capsys.readouterr().err


def test_auth_stores_the_payload_on_stdin_never_on_the_command_line(spotify, front_door, tmp_path, monkeypatch):
    seen = front_door(0, {"name": "spotify", "store": "/s", "bytes": 60})
    monkeypatch.setattr(spotify, "wiki_root", lambda: tmp_path)
    monkeypatch.setattr(spotify, "load_auth", lambda root: {})
    monkeypatch.setattr(spotify, "get_token", lambda root: None)
    spotify.cmd_auth(types.SimpleNamespace(client_id="cid", client_secret="sec"))
    got = seen()
    assert got["argv"] == ["--json", "credential", "set", "spotify"], got
    assert json.loads(got["stdin"]) == STORED and "sec" not in " ".join(got["argv"])


def test_outside_a_wiki_there_is_no_store_to_ask(spotify, front_door):
    front_door(0, {"name": "spotify", "value": json.dumps(STORED), "store": "/s"})
    assert spotify.load_auth(None) == {}
    assert not front_door.seen.exists(), "the front door was run with no wiki to bind it to"


# --- every unit reaches the front door a hosted run NAMES ----------------------


def _named_only(tmp_path, monkeypatch):
    """A recording `llm-wiki-ops` reachable ONLY through `LLM_WIKI_OPS`: the
    bare name is on no PATH the script can see. Returns a reader of the calls."""
    bin_dir, seen = tmp_path / "named-bin", tmp_path / "named.jsonl"
    bin_dir.mkdir()
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}) + '\\n')\n"
        "print('{}')\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path / "no-bare-name-here"))
    monkeypatch.setenv("LLM_WIKI_OPS", str(stub))

    def calls():
        return [json.loads(line) for line in seen.read_text().splitlines()] if seen.exists() else []

    return calls


def _load_spawner(unit: str, script: str):
    """`_load`, with the unit's own `scripts/` on `sys.path`: a unit ships
    siblings that import each other."""
    here = str(SKILLS / unit / "scripts")
    sys.path.insert(0, here)
    try:
        return _load(unit, script, "requests")
    finally:
        sys.path.remove(here)


def _reach_ops(mod, tmp_path, monkeypatch):
    mod._ops(tmp_path, "credential", "profile-dir", "example.test")


def _reach_section_plan(mod, tmp_path, monkeypatch):
    leaf = {"url": "https://example.invalid/lesson", "dir": "d"}
    monkeypatch.setattr(mod, "_plan_and_leaf", lambda args: (tmp_path, {}, leaf))
    monkeypatch.setattr(mod, "leaf_path", lambda capture_dir, one: tmp_path)
    mod.cmd_detect(types.SimpleNamespace())


def _reach_gmail(mod, tmp_path, monkeypatch):
    mod._ops(["--json", "page", "create"], "body")


def _reach_leaves(mod, tmp_path, monkeypatch):
    mod._spawn(tmp_path, "run", "skills/harvest/scripts/assets.py")


def _reach_notion(mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with contextlib.suppress(Exception):
        mod.write_page("research/channels/tasks", "2026-01-01", {}, "body")


def _reach_frameio(mod, tmp_path, monkeypatch):
    with contextlib.suppress(SystemExit):
        mod.write_page(tmp_path, "sources/scrapes/x", "Title", [], "body")


def _reach_youtube(mod, tmp_path, monkeypatch):
    with contextlib.suppress(SystemExit):
        mod.write_page(tmp_path, "sources/youtube/x", "Title", {}, "body")


SPAWNERS = [
    ("channel-circle", "capture_lesson.py", _reach_ops),
    ("channel-circle", "outage_probe.py", _reach_ops),
    ("channel-circle", "section_plan.py", _reach_section_plan),
    ("channel-frameio", "frameio_doc_note.py", _reach_frameio),
    ("channel-gmail", "write_items.py", _reach_gmail),
    ("channel-hubspot-video", "leaves.py", _reach_leaves),
    ("channel-notion-tasks", "write_items.py", _reach_notion),
    ("channel-spotify", "spotify.py", _reach_ops),
    ("channel-youtube", "youtube_note.py", _reach_youtube),
]


@pytest.mark.parametrize(("unit", "script", "reach"), SPAWNERS, ids=[f"{u}-{s}" for u, s, _ in SPAWNERS])
def test_every_unit_reaches_the_front_door_a_hosted_run_names(unit, script, reach, tmp_path, monkeypatch):
    """`llm-wiki-ops run` exports `LLM_WIKI_OPS`, naming the CLI it was itself
    reached by. A jail is not promised the `~/.local/bin` entry the bare name
    is, so a unit that reads PATH alone is the one that dies in a slice."""
    calls = _named_only(tmp_path, monkeypatch)
    mod = _load_spawner(unit, script)
    reach(mod, tmp_path, monkeypatch)
    assert calls(), f"{unit}/{script} never reached the front door `LLM_WIKI_OPS` names"
