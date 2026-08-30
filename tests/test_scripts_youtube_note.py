"""`youtube_note.py` builds the source note from a yt-dlp capture.

Both cases here are silent when broken: the note is written, exits 0, and
reports success — it just has no transcript, or sits in a directory holding
none of its own assets.

The script now lives in the `channel-youtube` skill unit (wiki-owned, copied by
`skills install`) rather than in `plugin/scripts/`, because exactly one unit
calls it. It reaches the plugin's generic transcript formatter by INVOCATION —
`llm-wiki-ops run scripts/format_transcript.py` — never by import, so these
tests pass `--format-transcript` to skip the shim a tmp wiki does not have.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "skills" / "channel-youtube" / "scripts" / "youtube_note.py")
# `format_transcript.py` is a HOST script (the ops plugin's `scripts/`), not
# part of this package: `LLM_WIKI_OPS_PLUGIN_SCRIPTS` names that directory
# where a checkout is at hand; without it the cases that need it skip.
_HOST_SCRIPTS = os.environ.get("LLM_WIKI_OPS_PLUGIN_SCRIPTS")
FORMATTER = Path(_HOST_SCRIPTS) / "format_transcript.py" if _HOST_SCRIPTS else None


def _need_formatter():
    if FORMATTER is None or not FORMATTER.is_file():
        pytest.skip("set LLM_WIKI_OPS_PLUGIN_SCRIPTS to the ops plugin's scripts/ dir — format_transcript.py is host code")


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
    # `LLM_WIKI_OPS_DIRNAME` is stripped by default so the tests are
    # deterministic whichever environment the suite runs in; the shim-path
    # tests set it the way the front door does.
    if formatter is _DEFAULT:  # the host formatter, where a checkout names it
        _need_formatter()
        formatter = FORMATTER
    env = dict(os.environ)
    env.pop("LLM_WIKI_OPS_DIRNAME", None)
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


def test_a_missing_shim_names_the_front_door(tmp_path):
    """No `--format-transcript` and no shim: the failure has to say what is
    missing, not traceback out of a subprocess call."""
    cap = _capture(tmp_path)
    cp = _run(tmp_path, cap, formatter=None, check=False,
              extra_env={"LLM_WIKI_OPS_DIRNAME": "ops"})
    assert cp.returncode != 0
    assert "ops/bin/llm-wiki-ops" in cp.stderr and "front door" in cp.stderr


def test_outside_the_front_door_the_failure_names_the_export(tmp_path):
    """Run bare — no `LLM_WIKI_OPS_DIRNAME` — and the script must not guess
    where the wiki keeps its tree: it names the export and the front door."""
    cap = _capture(tmp_path)
    cp = _run(tmp_path, cap, formatter=None, check=False)
    assert cp.returncode != 0
    assert "LLM_WIKI_OPS_DIRNAME" in cp.stderr and "front door" in cp.stderr
