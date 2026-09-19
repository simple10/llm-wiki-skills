"""`youtube_note.py` has two arms: `--record` writes the capture record at
harvest, `--dest` builds the body and writes the page at process.

The cases here are silent when broken: the page is written, exits 0, and
reports success — it just has no transcript, or landed somewhere the slice
cannot write.

The script lives in the `channel-youtube` skill unit (wiki-owned, copied by
`skills install`) rather than in `plugin/scripts/`, because exactly one unit
calls it. It reaches the plugin's generic transcript formatter, and the page
verbs, by INVOCATION — never by import — so most cases pass
`--format-transcript` to skip a front door a tmp wiki is not behind, and the
process arm always runs against a recording front-door stub.
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

DEST = "sources/youtube/yt-somechannel"
PAGE = f"{DEST}/A Video About Things.md"


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


ITEM = "https://www.youtube.com/watch?v=abc123"


def _capture(tmp_path, slug="yt-somechannel", captions_subdir=True, ticket=True):
    """A capture laid out the way a slice finds one: inside the JOB's
    `_raw/<slug>` slice, with the `ticket.json` the spawner wrote beside it."""
    cap = tmp_path / "_raw" / slug / "a-video-about-things--4cf2bd5f"
    cap.mkdir(parents=True)
    (cap / "metadata.json").write_text(json.dumps(META))
    if ticket:
        (cap / "ticket.json").write_text(json.dumps({
            "v": 1, "ticket": "0123456789ab", "unit": "channel-youtube", "slug": slug, "item": ITEM,
            "target": ITEM, "capture_dir": str(cap.relative_to(tmp_path)), "dest": None}))
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
         extra_argv=None, mode=_DEFAULT):
    """One arm of the script. `mode` defaults to the PROCESS arm, which is the
    one that builds a body; pass `["--record"]` for harvest."""
    if mode is _DEFAULT:
        mode = ["--dest", DEST]
    if formatter is _DEFAULT:  # the host formatter, where a checkout names it
        _need_formatter()
        formatter = FORMATTER
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    cp = subprocess.run(
        ["uv", "run", str(SCRIPT), str(tmp_path),
         "--capture-dir", str(cap.relative_to(tmp_path)),
         *mode,
         *(extra_argv or []),
         *(["--format-transcript", str(formatter)] if formatter else [])],
        check=check, capture_output=True, text=True, env=env)
    return json.loads(cp.stdout) if check else cp


def _stub_front_door(tmp_path, body=None):
    """A recording `llm-wiki-ops` first on PATH.

    It appends every call — argv, cwd, the re-entry guard, whatever arrived on
    stdin — to `seen.jsonl`, then answers `page create`/`page edit` by writing
    the page and printing what the real verb prints. `body` is python run
    before that, for a case that wants `run` answered its own way.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    seen = tmp_path / "seen.jsonl"
    seen.unlink(missing_ok=True)
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, pathlib\n"
        "argv = sys.argv[1:]\n"
        "stdin = '' if sys.stdin.isatty() else sys.stdin.read()\n"
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': argv, 'cwd': os.getcwd(), 'stdin': stdin,\n"
        "    'guard': os.environ.get('LLM_WIKI_OPS_DISPATCHED'),\n"
        "    'project_dir': os.environ.get('CLAUDE_PROJECT_DIR')}) + '\\n')\n"
        f"{body or 'pass'}\n"
        "verb = [a for a in argv if not a.startswith('--')]\n"
        "if verb[:1] == ['page']:\n"
        "    pairs = dict(a.split('=', 1) for a in argv if '=' in a and not a.startswith('--'))\n"
        "    rel = pairs['dest'].rstrip('/') + '/' + pairs['title'] + '.md' if verb[1] == 'create' else verb[2]\n"
        "    out = pathlib.Path(os.getcwd()) / rel\n"
        "    if verb[1] == 'create' and out.exists():\n"
        "        print(json.dumps({'error': rel + ' already exists \\u2014 the filename is the title'}))\n"
        "        sys.exit(2)\n"
        "    out.parent.mkdir(parents=True, exist_ok=True)\n"
        "    out.write_text(stdin)\n"
        "    print(json.dumps({'path': rel, 'status': 'draft'}))\n"
        "    sys.exit(0)\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, seen


def _calls(seen):
    return [json.loads(line) for line in seen.read_text().splitlines() if line.strip()]


def _page_calls(seen):
    return [c for c in _calls(seen) if "page" in c["argv"]]


def _processed(tmp_path, cap, **kw):
    """The process arm against a stubbed front door, with the real formatter."""
    _need_formatter()
    path, seen = _stub_front_door(tmp_path)
    res = _run(tmp_path, cap, extra_env=path, **kw)
    return res, seen


def test_captions_are_found_in_the_captions_subdirectory(tmp_path):
    """Harvest files captions under `captions/`. A glob that only looks at the
    capture root finds nothing and the page ships with no transcript at all."""
    cap = _capture(tmp_path)
    res, _seen = _processed(tmp_path, cap)
    assert res["has_transcript"] is True
    body = (tmp_path / res["page"]).read_text()
    assert "## Transcript" in body
    assert "At its peak, it grew fast and loudly" in body


def test_a_bare_capture_directory_still_works(tmp_path):
    cap = _capture(tmp_path, captions_subdir=False)
    res, _seen = _processed(tmp_path, cap)
    assert res["has_transcript"] is True


def test_the_harvest_arm_writes_a_capture_record_and_nothing_else(tmp_path):
    """Harvest is BYTES. No page, no summary, and no `frontmatter` object on
    the record: the facts reach the page because this unit writes the page."""
    cap = _capture(tmp_path)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    res = _run(tmp_path, cap, mode=["--record"], formatter=None)
    assert res["capture"] == f"{cap.relative_to(tmp_path)}/capture.json"
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before
    assert new == {cap / "capture.json"}, new
    record = json.loads((cap / "capture.json").read_text())
    assert record["body"] == "metadata.json" and record["content_type"] == "application/json"
    assert "frontmatter" not in record
    assert not set(record) & {"status", "resource", "harvested", "extracted", "document_id"}
    assert not (cap / "page.md").exists()


def test_the_process_arm_writes_the_page_under_dest_and_the_body_beside_the_bytes(tmp_path):
    """The page goes through the front door into `dest`; `page.md` stays in the
    capture dir so a retried process ticket can see what the run produced."""
    cap = _capture(tmp_path)
    res, seen = _processed(tmp_path, cap)
    assert res["written"] == [PAGE]
    assert res["page"] == f"{cap.relative_to(tmp_path)}/page.md"
    assert (tmp_path / PAGE).is_file()
    create = _page_calls(seen)[0]["argv"]
    assert create[:3] == ["--json", "page", "create"], create
    assert "title=A Video About Things" in create and f"dest={DEST}" in create
    assert "extracted=true" in create and f"resource={ITEM}" in create
    assert "--stdin" in create
    assert not [a for a in create if a.split("=")[0] in {"status", "document_id", "document_revision", "harvested"}]


def test_a_title_dest_already_holds_is_edited_not_created_twice(tmp_path):
    """A job pulls the same video again. `page create` refuses the title, and
    the refusal is the signal to replace the page rather than to fail."""
    cap = _capture(tmp_path)
    _processed(tmp_path, cap)
    path, seen = _stub_front_door(tmp_path)
    res = _run(tmp_path, cap, extra_env=path)
    verbs = [[a for a in c["argv"] if not a.startswith("--")] for c in _page_calls(seen)]
    assert verbs[0][:2] == ["page", "create"] and verbs[1][:2] == ["page", "edit"], verbs
    assert verbs[1][2] == PAGE
    assert res["written"] == [PAGE]


def test_the_page_verbs_are_reached_without_the_re_entry_guard(tmp_path):
    """This script is the front door's grandchild, so it inherits the guard the
    dispatcher exports and refuses (127) any call arriving with it. Measured
    against the plugin's own dispatcher: without dropping it every page write
    dies. `CLAUDE_PROJECT_DIR` goes for the neighboring reason — the dispatcher
    seeds its walk from it, ahead of the cwd that binds this call to this wiki."""
    cap = _capture(tmp_path)
    path, seen = _stub_front_door(tmp_path)
    _run(tmp_path, cap, extra_env={**path, "LLM_WIKI_OPS_DISPATCHED": "1",
                                   "CLAUDE_PROJECT_DIR": str(tmp_path / "another-wiki")})
    assert _page_calls(seen), "no page call was made"
    for call in _page_calls(seen):
        assert call["guard"] is None and call["project_dir"] is None, call
        assert Path(call["cwd"]) == tmp_path.resolve(), call


def test_slug_and_item_are_the_tickets(tmp_path):
    """`ticket.json` is the worker's whole input: what the record names is read
    off it, never guessed."""
    cap = _capture(tmp_path)
    _run(tmp_path, cap, mode=["--record"], formatter=None)
    record = json.loads((cap / "capture.json").read_text())
    assert (record["slug"], record["item"]) == ("yt-somechannel", ITEM)


def test_flags_override_the_ticket_and_stand_in_for_it_on_a_hand_run(tmp_path):
    cap = _capture(tmp_path, ticket=False)
    _run(tmp_path, cap, mode=["--record"], formatter=None,
         extra_argv=["--slug", "by-hand", "--item", "https://youtu.be/abc123"])
    record = json.loads((cap / "capture.json").read_text())
    assert (record["slug"], record["item"]) == ("by-hand", "https://youtu.be/abc123")


def test_a_hand_run_with_no_ticket_and_no_item_is_refused(tmp_path):
    """`llm-wiki-ops run` starts this script at the WIKI ROOT, so a directory
    no spawner wrote a ticket into is as likely a mistyped `--capture-dir` as a
    hand run. A hand run says what the directory holds with `--item`; the slug
    may still be read off the layout, `_raw/<slug>/<leaf>` by definition."""
    cap = _capture(tmp_path, ticket=False)
    cp = _run(tmp_path, cap, mode=["--record"], formatter=None, check=False)
    assert cp.returncode != 0 and "ticket.json" in cp.stderr and "--item" in cp.stderr
    assert not (cap / "page.md").exists() and not (cap / "capture.json").exists()
    _run(tmp_path, cap, mode=["--record"], formatter=None, extra_argv=["--item", ITEM])
    record = json.loads((cap / "capture.json").read_text())
    assert (record["slug"], record["item"]) == ("yt-somechannel", ITEM)


def test_an_arm_must_be_named(tmp_path):
    """Neither arm is the default: the two write different files in different
    directories, and a run that named none would silently pick one."""
    cap = _capture(tmp_path)
    cp = _run(tmp_path, cap, mode=[], formatter=None, check=False)
    assert cp.returncode != 0 and "--record" in cp.stderr and "--dest" in cp.stderr


def test_per_word_cue_markup_never_reaches_the_note(tmp_path):
    cap = _capture(tmp_path)
    res, _seen = _processed(tmp_path, cap)
    body = (tmp_path / res["page"]).read_text()
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
    (cap / "report.json").write_text(json.dumps({"outcome": "ok", "stale": True}))
    boom = tmp_path / "boom.py"
    boom.write_text("import sys; sys.exit(9)\n")
    path, _seen = _stub_front_door(tmp_path)
    cp = _run(tmp_path, cap, formatter=boom, check=False, extra_env=path)
    assert cp.returncode != 0
    assert "transcript formatting failed" in cp.stderr
    assert not (cap / "page.md").exists(), "page was written"
    assert not (tmp_path / DEST).exists(), "a page landed under dest"
    assert not (cap / "report.json").exists(), "an earlier run's report survived a failed build"


def test_the_process_arm_leaves_harvests_capture_record_alone(tmp_path):
    """`capture.json` is harvest's answer to "this item landed". A process run
    that cleared it would throw away the record of the bytes it read."""
    cap = _capture(tmp_path)
    _run(tmp_path, cap, mode=["--record"], formatter=None)
    before = (cap / "capture.json").read_text()
    _processed(tmp_path, cap)
    assert (cap / "capture.json").read_text() == before


def test_the_formatter_is_reached_by_the_bare_front_door_from_the_wiki_root(tmp_path):
    """No `--format-transcript`: the bare name on PATH, `run`, the plugin's
    address, with the wiki root as cwd — that cwd is all that binds the front
    door to this wiki, now that no path into the wiki names a shim."""
    cap = _capture(tmp_path)
    path, seen = _stub_front_door(
        tmp_path, "\nif argv[:1] == ['run']:\n    print('#### [00:00]\\n\\nstubbed transcript')\n    sys.exit(0)\n")
    res = _run(tmp_path, cap, formatter=None, extra_env={**path, "LLM_WIKI_OPS_DISPATCHED": "1"})
    run_call = next(c for c in _calls(seen) if c["argv"][:1] == ["run"])
    assert run_call["argv"][:2] == ["run", FORMATTER_REL], run_call
    assert run_call["argv"][2] == str(next((cap / "captions").glob("abc123.en.vtt"))), run_call
    assert Path(run_call["cwd"]) == tmp_path.resolve(), run_call
    assert run_call["guard"] is None, run_call
    assert "stubbed transcript" in (tmp_path / res["page"]).read_text()


def test_a_front_door_refusal_aborts_and_says_why(tmp_path):
    """The front door answering non-zero — a formatter it no longer serves at
    that address, say — aborts with its own words, and writes no page."""
    cap = _capture(tmp_path)
    path, _seen = _stub_front_door(tmp_path, "\nif argv[:1] == ['run']:\n    sys.exit('run: no such script')\n")
    cp = _run(tmp_path, cap, formatter=None, check=False, extra_env=path)
    assert cp.returncode != 0
    assert "transcript formatting failed" in cp.stderr and "no such script" in cp.stderr
    assert not (cap / "page.md").exists() and not (tmp_path / DEST).exists(), "page was written"


def test_a_refused_page_write_is_a_failure_not_a_silent_success(tmp_path):
    """`page create` refusing for any reason but "already exists" means no page
    landed. Exit 0 here would have the report step claim one."""
    _need_formatter()
    cap = _capture(tmp_path)
    path, _seen = _stub_front_door(
        tmp_path, "\nif 'page' in argv:\n    sys.exit('page create: dest is outside this wiki')\n")
    cp = _run(tmp_path, cap, check=False, extra_env=path)
    assert cp.returncode != 0
    assert "`page create` refused" in cp.stderr and "outside this wiki" in cp.stderr


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
    with pytest.raises(SystemExit) as exc:
        mod.write_page(tmp_path, DEST, "T", {}, "body")
    assert "`llm-wiki-ops` is not on PATH" in str(exc.value) and "front door" in str(exc.value)
