"""The frameio unit's PROCESS step actually runs.

This file exists because it once did not: a rename left two guaranteed crashes
in it and every suite stayed green, because nothing had ever executed the
script. It is the unit's own process step again — it reads the bytes harvest
left under `capture_dir` and writes ONE page under `dest`, through the front
door — so the cheapest honest coverage is: run it, and read back the page it
handed over, the body beside the capture and the update it posted.

The front door is stubbed by a recording `llm-wiki-ops` first on PATH: this
file is about the argv the unit hands the CLI, not about the CLI.
`tests/test_frameio_harness.py` runs the real one.

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

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "frameio_doc_note.py"

URL = "https://next.frame.io/share/11111111-1111-1111-1111-111111111111/view/def"
CAP_REL = "_raw/next-frame-io/deck-one--0001abcd"
DEST = "sources/scrapes/decks"
PAGE = f"{DEST}/Deck One - Big Share.md"
TICKET = "0123456789ab"


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


def _front_door(root: Path, monkeypatch, body="print(json.dumps({'ok': True}))", *, ticket=None):
    """A recording `llm-wiki-ops` first on PATH. Every call appends its argv,
    cwd, stdin (a `page` call's alone — `tickets open`/`update` pass none) and
    the binding it inherited to `seen.jsonl`. `tickets open` answers `ticket`
    (default: a bare ticket with no `process` section); `tickets update`
    answers a bare 0; `body` runs for everything else (`page create`/`edit`)."""
    bin_dir = root.parent / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    seen = root.parent / "seen.jsonl"
    stub = bin_dir / "llm-wiki-ops"
    ticket_json = json.dumps(ticket if ticket is not None else {})
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "argv = [a for a in sys.argv[1:] if a != '--json']\n"
        "stdin = sys.stdin.read() if argv[:1] == ['page'] else ''\n"
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "    'stdin': stdin,\n"
        "    'inherited': sorted(k for k in ('CLAUDE_PROJECT_DIR',) if k in os.environ)}) + '\\n')\n"
        "if argv[:3] == ['pipeline', 'tickets', 'open']:\n"
        f"    print(json.dumps({{'ticket': {ticket_json}}}))\n"
        "    sys.exit(0)\n"
        "if argv[:3] == ['pipeline', 'tickets', 'update']:\n"
        "    sys.exit(0)\n"
        f"{body}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("LLM_WIKI_OPS", str(stub))
    return seen


def _calls(seen: Path) -> list:
    return [json.loads(line) for line in seen.read_text().splitlines() if line.strip()] if seen.exists() else []


def _page_calls(seen: Path) -> list:
    return [c for c in _calls(seen) if c["argv"][1:2] == ["page"]]


def _update_calls(seen: Path) -> list:
    return [c for c in _calls(seen) if c["argv"][1:4] == ["pipeline", "tickets", "update"]]


def _kv(argv: list) -> dict:
    return dict(a.split("=", 1) for a in argv if "=" in a and not a.startswith("--"))


def _run(monkeypatch, root: Path, *extra, dest=DEST, ticket=TICKET):
    argv = ["frameio_doc_note.py", str(root), f"--capture-dir={CAP_REL}"]
    if dest is not None:
        argv.append(f"--dest={dest}")
    if ticket is not None:
        argv.append(f"--ticket={ticket}")
    monkeypatch.setattr(sys, "argv", [*argv, *extra])
    return _module().main()


def _read(root: Path, seen: Path):
    cap = root / CAP_REL
    return (cap / "page.md").read_text(), _kv(_update_calls(seen)[-1]["argv"])


# ----------------------------------------------------------------- the seam


def test_the_page_is_written_by_the_front_door_from_the_wiki_root(tmp_path, monkeypatch, capsys):
    """`page create` with an argv LIST — every value on it is venue text — from
    the wiki root, which is all that binds the front door to THIS wiki, and
    WITHOUT the project directory the harness set on this script."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(root, monkeypatch)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "another-wiki"))
    assert _run(monkeypatch, root) == 0

    (call,) = _page_calls(seen)
    assert call["argv"][:4] == ["--json", "page", "create", "title=Deck One - Big Share"]
    assert f"dest={DEST}" in call["argv"] and f"resource={URL}" in call["argv"]
    assert "extracted=true" in call["argv"], "the string, not a boolean: `pages.py` reads `true`"
    assert call["argv"][-1] == "--stdin" and call["stdin"].startswith("# Deck One - Big Share\n")
    assert Path(call["cwd"]) == root.resolve() and call["inherited"] == []
    assert json.loads(capsys.readouterr().out)["written"] == [PAGE]


def test_the_update_is_posted_last_and_names_the_page_in_written_from(tmp_path, monkeypatch, capsys):
    """A process ticket captures nothing: `written_from=` names the page and
    no `captured=` rides along. Posted after the page, so it never claims one
    nobody wrote."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    call = _update_calls(seen)[-1]
    assert call["argv"][4] == TICKET and "stage=process" in call["argv"] and "status=ok" in call["argv"]
    kv = _kv(call["argv"])
    assert "written_from" in kv and "captured" not in kv and "missing" not in kv
    written = json.loads((root / CAP_REL / kv["written_from"]).read_text(encoding="utf-8"))
    assert written == [PAGE]


def test_an_existing_page_is_edited_not_refused(tmp_path, monkeypatch, capsys):
    """`page create` refuses an existing title (exit 2, `already exists — the
    filename is the title`). This job pulled the asset before: replace it."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(
        root, monkeypatch,
        "sys.exit(0) if argv[1] == 'edit' else "
        f"sys.exit({PAGE + ' already exists — the filename is the title'!r})",
    )
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    create, edit = _page_calls(seen)
    assert create["argv"][2] == "create"
    assert edit["argv"][:4] == ["--json", "page", "edit", PAGE]
    assert f"resource={URL}" in edit["argv"] and edit["argv"][-1] == "--stdin"
    assert edit["stdin"] == create["stdin"]
    _, kv = _read(root, seen)
    assert kv["status"] == "ok"


@pytest.mark.parametrize("verb", ["create", "edit"])
def test_a_refusal_aborts_before_anything_is_posted(tmp_path, monkeypatch, verb):
    """An update naming a page nobody wrote reads as work done. The refusal's
    own words come out, and nothing is posted."""
    root = tmp_path / "wiki"
    _capture(root)
    exists = f"{PAGE} already exists — the filename is the title"
    body = (
        "sys.exit('dest is outside the content trees')" if verb == "create" else
        f"sys.exit('the note format refuses that') if argv[1] == 'edit' else sys.exit({exists!r})"
    )
    seen = _front_door(root, monkeypatch, body)
    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, root)
    assert f"`page {verb}` refused" in str(excinfo.value) and "no page was written" in str(excinfo.value)
    assert not _update_calls(seen), "the failed run posts nothing"


def test_a_missing_front_door_says_so_and_posts_nothing(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    _capture(root)
    mod = _module()
    monkeypatch.delenv("LLM_WIKI_OPS", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(root), f"--capture-dir={CAP_REL}", f"--dest={DEST}", f"--ticket={TICKET}"])
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert "llm-wiki-ops" in str(excinfo.value)


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

    (call,) = _page_calls(seen)
    assert call["argv"][:4] == ["--json", "page", "create", "title=Deck One - Big Share"]
    assert "extracted=queued" in call["argv"], "the string `queued`, which is what the walker matches"
    assert f"media={CAP_REL}/video.mp4" in call["argv"], "wiki-relative: the queue is committed and read on any box"
    assert f"resource={URL}" in call["argv"] and call["argv"][-1] == "--stdin"
    assert call["stdin"] == "", "the transcriber fills the body; a page about the video would never be filled"
    assert not (root / CAP_REL / "page.md").exists(), "there is no body to keep beside the capture"

    kv = _kv(_update_calls(seen)[-1]["argv"])
    assert kv["status"] == "ok"
    written = json.loads((root / CAP_REL / kv["written_from"]).read_text(encoding="utf-8"))
    assert written == [PAGE]
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
    (call,) = _page_calls(seen)
    assert "extracted=true" not in call["argv"], call["argv"]


def test_skip_posts_ok_with_the_reason_and_no_page(tmp_path, monkeypatch, capsys):
    """`process.exclude_rules`, `options` and `min_date` are the agent's to
    apply; this is how a capture they ruled out is recorded. P-4: `ok`, never
    a worker's `skipped`."""
    root = tmp_path / "wiki"
    _capture(root)
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root, "--skip=exclude_rules: draft decks\nare out") == 0

    assert not _page_calls(seen) and not (root / CAP_REL / "page.md").exists()
    kv = _kv(_update_calls(seen)[-1]["argv"])
    assert kv["status"] == "ok" and "written_from" not in kv
    assert kv["reason"] == "exclude_rules: draft decks are out", "one line: a reason is data, never markup"
    capsys.readouterr()


# ----------------------------------------------------------------- the body


def _rendered(tmp_path, monkeypatch, *extra, **capture_kw):
    root = tmp_path / "wiki"
    _capture(root, **capture_kw)
    _front_door(root, monkeypatch)
    assert _run(monkeypatch, root, *extra) == 0
    return (root / CAP_REL / "page.md").read_text()


def test_the_body_is_the_page_and_is_kept_beside_the_capture(tmp_path, monkeypatch, capsys):
    root = tmp_path / "wiki"
    cap = _capture(root)
    seen = _front_door(root, monkeypatch)
    assert _run(monkeypatch, root) == 0
    capsys.readouterr()

    assert sorted(p.name for p in cap.iterdir()) == ["capture.json", "document.pdf", "meta.json", "page.md", "written.json"]
    assert sorted(p.name for p in root.iterdir()) == ["_raw"], "the page went through the front door, not by hand"
    assert (cap / "page.md").read_text() == _page_calls(seen)[0]["stdin"]


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

    call = _page_calls(seen)[0]
    assert "title=Lesson 3 - 'Pricing' A-B (Client B)" in call["argv"]
    assert call["stdin"].startswith('# .Lesson 3: "Pricing"? A/B second line\n')
    kv = _kv(_update_calls(seen)[-1]["argv"])
    written = json.loads((root / CAP_REL / kv["written_from"]).read_text(encoding="utf-8"))
    assert written == [f"{DEST}/Lesson 3 - 'Pricing' A-B (Client B).md"]


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
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(root), f"--capture-dir={CAP_REL}", f"--dest={DEST}", f"--ticket={TICKET}"])
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
    _front_door(other, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(other), f"--capture-dir={CAP_REL}", f"--dest={DEST}", f"--ticket={TICKET}"])
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


def test_no_ticket_is_refused(tmp_path, monkeypatch, capsys):
    """`--ticket` is required: it is what the update posts to."""
    root = tmp_path / "wiki"
    _capture(root)
    with pytest.raises(SystemExit):
        _run(monkeypatch, root, ticket=None)
    assert "--ticket" in capsys.readouterr().err
