"""`youtube_note.py` builds the source note from a yt-dlp capture.

Both cases here are silent when broken: the note is written, exits 0, and
reports success — it just has no transcript, or sits in a directory holding
none of its own assets.

The script now lives in the `channel-youtube` skill unit (wiki-owned, copied by
`skills install`) rather than in `plugin/scripts/`, because exactly one unit
calls it. It reaches the plugin's generic transcript formatter by INVOCATION —
`llm-wiki-ops run skills/process/scripts/format_transcript.py` — never by
import, so most tests pass `--format-transcript` to skip a front door a tmp
wiki is not behind; the front-door cases put a recording stub on PATH.
"""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "skills" / "channel-youtube" / "scripts" / "youtube_note.py")
# The address the unit itself runs, read off the unit: the formatter's place
# in the plugin is the plugin's to move, and a second spelling here is how
# the move went unseen.
FORMATTER_REL = re.search(r'^FORMATTER = "([^"]+)"$', SCRIPT.read_text(), re.M).group(1)
# `format_transcript.py` is HOST code, not part of this package:
# `LLM_WIKI_OPS_PLUGIN` names the ops plugin's root — the tree `run` serves
# that address from — where a checkout is at hand; without it the cases that
# need the real formatter skip.
_PLUGIN = os.environ.get("LLM_WIKI_OPS_PLUGIN")
FORMATTER = Path(_PLUGIN) / FORMATTER_REL if _PLUGIN else None


def _need_formatter():
    if FORMATTER is None:
        pytest.skip("set LLM_WIKI_OPS_PLUGIN to the ops plugin's root — format_transcript.py is host code")
    # Named and absent is a FAILURE, not a skip: a formatter that moved is
    # what this suite skipped over, green, while every real capture aborted.
    assert FORMATTER.is_file(), f"{FORMATTER_REL} is not under LLM_WIKI_OPS_PLUGIN={_PLUGIN} — did the plugin move it?"


VTT = """WEBVTT

00:00:00.080 --> 00:00:02.629
At<00:00:00.320><c> its</c><00:00:00.560><c> peak,</c><00:00:01.120><c> it</c><00:00:01.600><c> grew</c>

00:00:02.639 --> 00:00:05.190
At its peak, it grew
fast<00:00:02.960><c> and</c><00:00:03.439><c> loudly</c>
"""

META = {"id": "abc123", "title": "A Video About Things", "duration": 327,
        "channel": "Some Channel", "description": "why it matters"}


DEST = "sources/youtube/yt-somechannel"


def _capture(tmp_path, slug="yt-somechannel", captions_subdir=True,
             dest=DEST):
    """A capture laid out the way harvest lays one out: inside the WATCH's
    `_raw/<slug>` slice, with the `dest` the host wrote onto it."""
    cap = tmp_path / "_raw" / slug / "a-video-about-things--4cf2bd5f"
    cap.mkdir(parents=True)
    (cap / "metadata.json").write_text(json.dumps(META))
    if dest is not None:
        (cap / "capture.json").write_text(json.dumps({"dest": dest}))
    dest = cap / "captions" if captions_subdir else cap
    dest.mkdir(exist_ok=True)
    (dest / "abc123.en.vtt").write_text(VTT)
    (dest / "abc123.en-orig.vtt").write_text("WEBVTT\n\n" + VTT.split("\n\n", 1)[1])
    (tmp_path / ".llm-wiki.toml").write_text(
        'schema_version = 1\nquery_backends = ["grep"]\n\n[ops]\nmajor = 1\n'
        'requires = ">=1.0"\n')
    return cap


_DEFAULT = object()


def _run(tmp_path, cap, formatter=_DEFAULT, check=True, extra_env=None,
         extra_argv=None):
    if formatter is _DEFAULT:  # the host formatter, where a checkout names it
        _need_formatter()
        formatter = FORMATTER
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    cp = subprocess.run(
        ["uv", "run", str(SCRIPT), str(tmp_path),
         "--capture-dir", str(cap.relative_to(tmp_path)), "--force",
         *(extra_argv or []),
         *(["--format-transcript", str(formatter)] if formatter else [])],
        check=check, capture_output=True, text=True, env=env)
    return json.loads(cp.stdout) if check else cp


def test_captions_are_found_in_the_captions_subdirectory(tmp_path):
    """Harvest files captions under `captions/`. A glob that only looks at the
    capture root finds nothing and the note ships with no transcript at all."""
    cap = _capture(tmp_path)
    res = _run(tmp_path, cap)
    assert res["has_transcript"] is True
    body = (tmp_path / res["note"]).read_text()
    assert "## Transcript" in body
    assert "At its peak, it grew fast and loudly" in body


def test_a_bare_capture_directory_still_works(tmp_path):
    """Captions beside the metadata, not under `captions/`."""
    cap = _capture(tmp_path, captions_subdir=False)
    assert _run(tmp_path, cap)["has_transcript"] is True


def test_the_note_lands_where_the_watch_says_and_nowhere_else(tmp_path):
    """`<dest>/pages/<slug>.md` — the watch's own `dirs.sources`, read off
    the capture, with NO component composed under it.

    It used to insert `cap_dir.parent.name`, the netloc. That parent is the
    watch's slug now, so keeping it would bury every note under a name that
    is already the bundle's — and `scaffold.py` writes `<dest>/pages/` for
    the same captures. Two builders disagreeing about where a note lands is
    the thing `scaffold.py`'s own comment warns against.
    """
    cap = _capture(tmp_path)
    res = _run(tmp_path, cap)
    assert res["note"].startswith(f"{DEST}/pages/"), res["note"]
    assert "yt-somechannel/pages" not in res["note"].removeprefix(DEST)


def test_a_capture_with_no_dest_is_refused_rather_than_guessed(tmp_path):
    """No third fallback: a capture carrying neither is one the host never
    produced, and a guessed bundle would silently disagree with wherever
    the watch's notes actually go."""
    cap = _capture(tmp_path, dest=None)

    cp = _run(tmp_path, cap, check=False)

    assert cp.returncode != 0
    assert "dest" in cp.stderr and "--notes-dir" in cp.stderr
    # The refusal NAMES the file the operator has to go and look at. Asserted
    # on the path because the two words above both survive an f-string whose
    # braces are doubled — which is how the message shipped reading
    # `{cap_dir / 'capture.json'} carries no dest` verbatim.
    assert str(cap / "capture.json") in cp.stderr, cp.stderr


def test_notes_dir_still_overrides_for_a_hand_run_capture(tmp_path):
    cap = _capture(tmp_path, dest=None)

    res = json.loads(_run(tmp_path, cap, check=False,
                          extra_argv=["--notes-dir", "sources/hand"]).stdout)

    assert res["note"].startswith("sources/hand/pages/")


def test_per_word_cue_markup_never_reaches_the_note(tmp_path):
    cap = _capture(tmp_path)
    body = (tmp_path / _run(tmp_path, cap)["note"]).read_text()
    assert "<c>" not in body and "<00:00:" not in body
    # the rolling repeat is collapsed, not emitted twice
    assert body.count("At its peak") == 1


def test_the_unit_script_imports_nothing_from_the_plugin(tmp_path):
    """A unit's scripts must run on any machine that clones the wiki, with
    nothing but `uv` and the unit itself. An import would couple the unit to a
    plugin layout it does not control; an invocation survives every update."""
    src = SCRIPT.read_text()
    assert "import paths" not in src
    assert "from format_transcript" not in src
    assert "llm-wiki-ops-v1" not in src.split('"""')[0]   # no PEP 723 dep


def test_a_failing_formatter_aborts_instead_of_shipping_a_bare_note(tmp_path):
    """The whole point of the subprocess boundary's exit check. Before it, a
    broken formatter produced a note with no transcript, exit 0, reporting
    success — the exact silent failure this file was written to guard."""
    cap = _capture(tmp_path)
    boom = tmp_path / "boom.py"
    boom.write_text("import sys; sys.exit(9)\n")
    cp = _run(tmp_path, cap, formatter=boom, check=False)
    assert cp.returncode != 0
    assert "transcript formatting failed" in cp.stderr
    assert not list((tmp_path / "sources").rglob("*.md")), "note was written"


def _stub_front_door(tmp_path, body):
    """A recording `llm-wiki-ops` first on PATH. It writes what it was handed
    — argv, cwd, the re-entry guard — to `seen.json`, then runs `body`."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen.json"
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        f"           'guard': os.environ.get('LLM_WIKI_OPS_DISPATCHED')}}, open({str(seen)!r}, 'w'))\n"
        f"{body}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, seen


def test_the_formatter_is_reached_by_the_bare_front_door_from_the_wiki_root(tmp_path):
    """No `--format-transcript`: the bare name on PATH, `run`, the plugin's
    address, with the wiki root as cwd — that cwd is all that binds the front
    door to this wiki, now that no path into the wiki names a shim.

    And WITHOUT the re-entry guard. This script only ever runs as a
    grandchild of the front door, which exports the guard and refuses (127)
    any call that arrives carrying it — so the env below is the real one, and
    a call that passed it through would abort every capture."""
    cap = _capture(tmp_path)
    path, seen = _stub_front_door(tmp_path, "print('#### [00:00]\\n\\nstubbed transcript')")
    res = _run(tmp_path, cap, formatter=None, extra_env={**path, "LLM_WIKI_OPS_DISPATCHED": "1"})
    got = json.loads(seen.read_text())
    assert got["argv"][:2] == ["run", FORMATTER_REL], got
    assert got["argv"][2] == str(next((cap / "captions").glob("abc123.en.vtt"))), got
    assert Path(got["cwd"]) == tmp_path.resolve(), got
    assert got["guard"] is None, got
    assert "stubbed transcript" in (tmp_path / res["note"]).read_text()


def test_a_front_door_refusal_aborts_and_says_why(tmp_path):
    """The front door answering non-zero — a formatter it no longer serves at
    that address, say — aborts with its own words, and writes no note."""
    cap = _capture(tmp_path)
    path, _ = _stub_front_door(tmp_path, "sys.exit('run: no such script')")
    cp = _run(tmp_path, cap, formatter=None, check=False, extra_env=path)
    assert cp.returncode != 0
    assert "transcript formatting failed" in cp.stderr and "no such script" in cp.stderr
    assert not list((tmp_path / "sources").rglob("*.md")), "note was written"


def test_a_machine_without_the_front_door_is_told_so(tmp_path, monkeypatch):
    """No `llm-wiki-ops` on PATH: the failure names what is missing instead
    of a traceback out of a subprocess call. In-process, because a PATH with
    nothing on it cannot also start the `uv` the other cases run through."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("youtube_note", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    with pytest.raises(SystemExit) as exc:
        mod.format_transcript(tmp_path / "a.vtt", None, tmp_path, None)
    assert "`llm-wiki-ops` is not on PATH" in str(exc.value) and "front door" in str(exc.value)
