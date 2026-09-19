"""The frameio unit's PROCESS step actually runs.

This file exists because it once did not: a rename left two guaranteed crashes
in it and every suite stayed green, because nothing had ever executed the
script. It is the unit's own process step again — it reads the bytes harvest
left under `capture_dir` and writes ONE page under `dest`, through the front
door — so the cheapest honest coverage is: run it, and read back the page it
handed over, the body beside the capture and the report.

The front door is stubbed by a recording `llm-wiki-ops` first on PATH: this
file is about the argv the unit hands the CLI, not about the CLI.
`tests/test_port_frameio.py` runs the real one.

Imported rather than subprocessed: the module's `pypdf`/`pptx`/`openpyxl`
imports are lazy (inside the extractor functions), so the suite stays
hermetic.
"""
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "channel-frameio" / "scripts"
SCRIPT = SCRIPTS / "frameio_doc_note.py"

URL = "https://next.frame.io/share/11111111-1111-1111-1111-111111111111/view/def"
CAP_REL = "_raw/next-frame-io/deck-one--0001abcd"
DEST = "sources/scrapes/decks"
PAGE = f"{DEST}/Deck One - Big Share.md"


def _module():
    spec = importlib.util.spec_from_file_location("frameio_doc_note", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))          # the sibling import, as `uv run` does
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


def _capture(root: Path, ext="pdf", *, title="Deck One - Big Share", capture=None, **meta):
    """What HARVEST leaves for a document leaf: the bytes it downloaded, the
    flat `capture.json` naming them, and `meta.json` carrying what the harvest
    knew and the file itself does not."""
    cap = root / CAP_REL
    cap.mkdir(parents=True)
    (cap / f"document.{ext}").write_bytes(b"%PDF-1.4 fake")
    base = {
        "url": URL, "title": title, "name": f"Deck One.{ext}", "kind": "document", "bytes": 13,
        "path": [], "crumb_skip": 0, "title_strip": None, "author": None, "group": None, "group_type": None,
    }
    base.update(meta)
    (cap / "meta.json").write_text(json.dumps(base))
    record = {"v": 1, "slug": "next-frame-io", "item": URL, "title": "Deck One - Big Share",
              "body": f"document.{ext}", "content_type": "application/pdf", "fetched_at": "2026-09-19T00:00:00Z"}
    record.update(capture or {})
    (cap / "capture.json").write_text(json.dumps(record))
    return cap


def _front_door(root: Path, monkeypatch, body="print(json.dumps({'ok': True}))"):
    """A recording `llm-wiki-ops` first on PATH. Every call appends its argv,
    cwd, stdin and the re-entry guard to `seen.jsonl`, then runs `body`."""
    bin_dir = root.parent / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    seen = root.parent / "seen.jsonl"
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "    'stdin': sys.stdin.read(),\n"
        "    'guard': sorted(k for k in ('LLM_WIKI_OPS_DISPATCHED', 'CLAUDE_PROJECT_DIR') if k in os.environ)}) + '\\n')\n"
        f"{body}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return seen


def _calls(seen: Path):
    return [json.loads(line) for line in seen.read_text().splitlines()] if seen.exists() else []


def _run(monkeypatch, root: Path, *extra, dest=DEST):
    argv = ["frameio_doc_note.py", str(root), f"--capture-dir={CAP_REL}"]
    if dest is not None:
        argv.append(f"--dest={dest}")
    monkeypatch.setattr(sys, "argv", [*argv, *extra])
    return _module().main()


def _read(root: Path):
    cap = root / CAP_REL
    return (cap / "page.md").read_text(), json.loads((cap / "report.json").read_text())


# ----------------------------------------------------------------- the seam


def test_the_page_is_written_by_the_front_door_from_the_wiki_root(tmp_path, monkeypatch, capsys):
    """`page create` with an argv LIST — every value on it is venue text — from
    the wiki root, which is all that binds the front door to THIS wiki, and
    WITHOUT the re-entry guard the dispatcher sets on its own grandchildren."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(root, monkeypatch)
    monkeypatch.setenv("LLM_WIKI_OPS_DISPATCHED", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "another-wiki"))
    assert _run(monkeypatch, root) == 0

    (call,) = _calls(seen)
    assert call["argv"][:4] == ["--json", "page", "create", "title=Deck One - Big Share"]
    assert f"dest={DEST}" in call["argv"] and f"resource={URL}" in call["argv"]
    assert "extracted=true" in call["argv"], "the string, not a boolean: `pages.py` reads `true`"
    assert call["argv"][-1] == "--stdin" and call["stdin"].startswith("# Deck One - Big Share\n")
    assert Path(call["cwd"]) == root.resolve() and call["guard"] == []
    assert json.loads(capsys.readouterr().out)["written"] == [PAGE]


def test_the_report_is_written_last_and_names_the_page_in_written(tmp_path, monkeypatch, capsys):
    """A process ticket captures nothing: `written[]` is the pages, `captured[]`
    is empty. Written after the page, so it never claims one nobody wrote."""
    root = tmp_path / "wiki"
    cap = _capture(root)
    (cap / "report.json").write_text(json.dumps({"v": 1, "outcome": "ok", "captured": [{"dir": "stale"}]}))
    _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    _body, report = _read(root)
    assert list(report) == ["v", "ticket", "outcome", "reason", "captured", "written", "missing", "discovered"]
    assert report["outcome"] == "ok" and report["reason"] is None
    assert report["written"] == [PAGE]
    assert report["captured"] == [] and report["missing"] == [] and report["discovered"] == []


def test_the_ticket_id_rides_on_the_report_when_there_is_a_ticket(tmp_path, monkeypatch, capsys):
    root = tmp_path / "wiki"
    cap = _capture(root)
    (cap / "ticket.json").write_text(json.dumps({"ticket": "abc123", "capture_dir": CAP_REL, "dest": DEST}))
    _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()
    assert _read(root)[1]["ticket"] == "abc123"


def test_an_existing_page_is_edited_not_refused(tmp_path, monkeypatch, capsys):
    """`page create` refuses an existing title (exit 2, `already exists — the
    filename is the title`). This job pulled the asset before: replace it."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(
        root, monkeypatch,
        "sys.exit(0) if sys.argv[3] == 'edit' else "
        f"sys.exit({PAGE + ' already exists — the filename is the title'!r})",
    )
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    create, edit = _calls(seen)
    assert create["argv"][2] == "create"
    assert edit["argv"][:4] == ["--json", "page", "edit", PAGE]
    assert f"resource={URL}" in edit["argv"] and edit["argv"][-1] == "--stdin"
    assert edit["stdin"] == create["stdin"]
    assert _read(root)[1]["written"] == [PAGE]


@pytest.mark.parametrize("verb", ["create", "edit"])
def test_a_refusal_aborts_before_any_report_is_written(tmp_path, monkeypatch, verb):
    """A report naming a page nobody wrote reads as work done. The refusal's
    own words come out, and `report.json` — stale or fresh — is not there."""
    root = tmp_path / "wiki"
    cap = _capture(root)
    (cap / "report.json").write_text(json.dumps({"v": 1, "outcome": "ok"}))
    exists = f"{PAGE} already exists — the filename is the title"
    body = (
        "sys.exit('dest is outside the content trees')" if verb == "create" else
        f"sys.exit('the note format refuses that') if sys.argv[3] == 'edit' else sys.exit({exists!r})"
    )
    _front_door(root, monkeypatch, body)
    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, root)
    assert f"`page {verb}` refused" in str(excinfo.value) and "no page was written" in str(excinfo.value)
    assert not (cap / "report.json").exists(), "the stale report went with the failed run"


def test_a_missing_front_door_says_so_and_writes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    cap = _capture(root)
    mod = _module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(root), f"--capture-dir={CAP_REL}", f"--dest={DEST}"])
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert "llm-wiki-ops" in str(excinfo.value) and not (cap / "report.json").exists()


# -------------------------------------------------------- what earns no page


def test_a_video_leaf_gets_the_transcribe_stages_queued_stub(tmp_path, monkeypatch, capsys):
    """An EMPTY body, `extracted=queued` and the media it waits on,
    wiki-relative: `pipeline/media.py::queued_under` asks for exactly those
    two. A page ABOUT the video would be a finished page, and the recording
    would never be transcribed behind it."""
    root = tmp_path / "wiki"
    _capture(root, capture={"body": "video.mp4", "content_type": "video/mp4"})
    (root / CAP_REL / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0

    (call,) = _calls(seen)
    assert call["argv"][:4] == ["--json", "page", "create", "title=Deck One - Big Share"]
    assert "extracted=queued" in call["argv"], "the string `queued`, which is what the walker matches"
    assert f"media={CAP_REL}/video.mp4" in call["argv"], "wiki-relative: the queue is committed and read on any box"
    assert f"resource={URL}" in call["argv"] and call["argv"][-1] == "--stdin"
    assert call["stdin"] == "", "the transcriber fills the body; a page about the video would never be filled"
    assert not (root / CAP_REL / "page.md").exists(), "there is no body to keep beside the capture"

    report = json.loads((root / CAP_REL / "report.json").read_text())
    assert report["outcome"] == "ok" and report["written"] == [PAGE]
    assert json.loads(capsys.readouterr().out)["queued"] == f"{CAP_REL}/video.mp4"


def test_a_video_stub_never_carries_the_flag_that_means_finished(tmp_path, monkeypatch, capsys):
    """`extracted=true` on a stub is a page no stage comes back to, and the
    recording is stranded with nothing saying so."""
    root = tmp_path / "wiki"
    _capture(root, capture={"body": "video.mp4", "content_type": "video/mp4"})
    (root / CAP_REL / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()
    (call,) = _calls(seen)
    assert "extracted=true" not in call["argv"], call["argv"]


def test_skip_writes_the_reason_and_no_page(tmp_path, monkeypatch, capsys):
    """`process.exclude_rules`, `options` and `min_date` are the agent's to
    apply; this is how a capture they ruled out is recorded."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root, "--skip=exclude_rules: draft decks\nare out") == 0

    assert _calls(seen) == [] and not (root / CAP_REL / "page.md").exists()
    report = json.loads((root / CAP_REL / "report.json").read_text())
    assert report["outcome"] == "skipped" and report["written"] == []
    assert report["reason"] == "exclude_rules: draft decks are out", "one line: a reason is data, never markup"
    capsys.readouterr()


def test_a_stale_report_goes_before_anything_else(tmp_path, monkeypatch):
    """The capture dir is the same one on every pull, and `apply` does not
    check whose ticket the report it reads answers."""
    root = tmp_path / "wiki"
    cap = _capture(root)
    (cap / "report.json").write_text(json.dumps({"v": 1, "outcome": "ok", "written": ["sources/old.md"]}))
    (cap / "capture.json").unlink()
    _front_door(root, monkeypatch)
    with pytest.raises(SystemExit):
        _run(monkeypatch, root)
    assert not (cap / "report.json").exists()


# ----------------------------------------------------------------- the body


def _rendered(tmp_path, monkeypatch, *extra, **capture_kw):
    root = tmp_path / "wiki"
    _capture(root, **capture_kw)
    _front_door(root, monkeypatch)
    assert _run(monkeypatch, root, *extra) == 0
    return _read(root)[0]


def test_the_body_is_the_page_and_is_kept_beside_the_capture(tmp_path, monkeypatch, capsys):
    root = tmp_path / "wiki"
    cap = _capture(root)
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    assert sorted(p.name for p in cap.iterdir()) == ["capture.json", "document.pdf", "meta.json", "page.md", "report.json"]
    assert sorted(p.name for p in root.iterdir()) == ["_raw"], "the page went through the front door, not by hand"
    assert (cap / "page.md").read_text() == _calls(seen)[0]["stdin"]


def test_the_body_carries_no_yaml_block_of_its_own(tmp_path, monkeypatch):
    """`page create` writes the frontmatter; a second `---` block corrupts the
    page. A title is venue text, so it is tried as one too."""
    body = _rendered(tmp_path, monkeypatch, title="---\nstatus: published\n---")
    assert not any(line.strip() == "---" for line in body.splitlines())
    assert body.splitlines()[0] == "# --- status: published ---"


def test_the_h1_is_the_venues_own_title_and_the_file_is_the_settled_one(tmp_path, monkeypatch, capsys):
    """Harvest made `capture.json`'s title filename-safe and settled it against
    its namesakes; the H1 keeps what the venue said, folded to one line."""
    root = tmp_path / "wiki"
    _capture(root, title='.Lesson 3: "Pricing"? A/B\nsecond line',
             capture={"title": "Lesson 3 - 'Pricing' A-B (Client B)"})
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    call = _calls(seen)[0]
    assert "title=Lesson 3 - 'Pricing' A-B (Client B)" in call["argv"]
    assert call["stdin"].startswith('# .Lesson 3: "Pricing"? A/B second line\n')
    assert _read(root)[1]["written"] == [f"{DEST}/Lesson 3 - 'Pricing' A-B (Client B).md"]


def test_the_body_never_points_into_raw(tmp_path, monkeypatch):
    """A committed page never links into `_raw/`: machine-local, prunable. The
    docs once promised a pointer there; the code rightly never emitted one."""
    body = _rendered(tmp_path, monkeypatch)
    assert "_raw" not in body and "document.pdf" not in body


def test_the_facts_default_off_what_harvest_recorded(tmp_path, monkeypatch):
    """A process ticket carries no breadcrumb and no scoping: harvest saw the
    manifest and the operator's flags, and wrote them to `meta.json`."""
    body = _rendered(
        tmp_path, monkeypatch,
        path=["Share", "Decks"], crumb_skip=1, author="Ada Lovelace", group="Speaker Decks", group_type="course",
    )
    for line in ("- **Type:** doc", f"- **Source:** <{URL}>", "- **File:** `Deck One.pdf` (pdf, 13 bytes)",
                 "- **Author:** Ada Lovelace", "- **Group:** Speaker Decks", "- **Group type:** course", "*Decks*"):
        assert line in body.splitlines(), line


def test_a_flag_overrides_what_harvest_recorded(tmp_path, monkeypatch):
    """A hand run over a capture harvest did not record, or the one fact a job
    cannot hold."""
    body = _rendered(tmp_path, monkeypatch, "--path=Client A", "--path=--drafts", "--crumb-skip=0",
                     "--author=-Ada", "--title-strip= - Big Share", path=["Share"], crumb_skip=1, author="Nobody")
    assert "*Client A / --drafts*" in body.splitlines()
    assert "- **Author:** -Ada" in body.splitlines()
    assert body.startswith("# Deck One\n"), "--title-strip trims the share's own suffix off the H1"


def test_title_strip_is_read_off_meta_when_no_flag_gives_one(tmp_path, monkeypatch):
    body = _rendered(tmp_path, monkeypatch, title_strip=" - Big Share")
    assert body.startswith("# Deck One\n")


def test_scoping_keys_are_omitted_when_undeclared(tmp_path, monkeypatch):
    body = _rendered(tmp_path, monkeypatch)
    assert "**Author:**" not in body and "**Group:**" not in body
    assert not any(line.startswith("*") for line in body.splitlines()), "no path: no breadcrumb line"


def test_a_capture_without_a_name_still_renders(tmp_path, monkeypatch):
    """A ticket whose target is a leaf viewer has no manifest: `meta.json`
    carries `name: null`, and the file is `document.<ext>`."""
    body = _rendered(tmp_path, monkeypatch, name=None, title="")
    assert body.startswith("# document\n") and "- **File:** `document.pdf`" in body


def test_a_failed_extraction_is_said_in_the_body_never_raised(tmp_path, monkeypatch):
    """`%PDF-1.4 fake` is no PDF (and pypdf may not even be importable here):
    the file is captured either way, so the render must not die on it."""
    body = _rendered(tmp_path, monkeypatch)
    assert "> [!note]- Extracted text\n> (extraction failed: " in body


def test_extracted_text_is_quoted_line_by_line_and_capped(tmp_path, monkeypatch, capsys):
    root = tmp_path / "wiki"
    _capture(root)
    _front_door(root, monkeypatch)
    mod = _module()
    monkeypatch.setitem(mod.EXTRACTORS, "pdf", lambda path: "one\n\n---\ntwo")
    monkeypatch.setattr(mod, "MAX_EXTRACT_CHARS", 1000)
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(root), f"--capture-dir={CAP_REL}", f"--dest={DEST}"])
    assert mod.main() == 0
    assert (root / CAP_REL / "page.md").read_text().endswith("> [!note]- Extracted text\n> one\n>\n> ---\n> two\n")

    monkeypatch.setitem(mod.EXTRACTORS, "pdf", lambda path: "x" * 5000)
    assert mod.main() == 0
    assert "(truncated at 1000 characters" in (root / CAP_REL / "page.md").read_text()
    capsys.readouterr()


# ------------------------------------------------------------- the refusals


def test_a_capture_with_no_body_or_no_item_is_refused_naming_the_dir(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    cap = _capture(root)
    _front_door(root, monkeypatch)
    (cap / "document.pdf").unlink()
    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, root)
    assert str(cap.resolve()) in str(excinfo.value) and "capture.json" in str(excinfo.value)

    other = tmp_path / "b"
    _capture(other, capture={"item": None}, url=None)
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(other), f"--capture-dir={CAP_REL}", f"--dest={DEST}"])
    with pytest.raises(SystemExit) as excinfo:
        _module().main()
    assert "--item" in str(excinfo.value)


def test_a_capture_dir_that_is_not_there_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    root.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, root)
    assert "not a directory" in str(excinfo.value)


def test_dest_is_required_and_the_stage_is_only_ever_process(tmp_path, monkeypatch, capsys):
    """`--dest` is the ticket's, verbatim: there is no default to fall back on,
    and a page written somewhere else is a page in the wrong tree."""
    root = tmp_path / "wiki"
    _capture(root)
    with pytest.raises(SystemExit):
        _run(monkeypatch, root, dest=None)
    assert "--dest" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _run(monkeypatch, root, "--stage=harvest")
    assert "--stage" in capsys.readouterr().err
