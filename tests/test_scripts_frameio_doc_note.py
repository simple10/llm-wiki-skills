"""The frameio unit's document renderer actually runs.

This file exists because it once did not: a rename left two guaranteed crashes
in it and every suite stayed green, because nothing had ever executed the
script. It renders at HARVEST time now — `page.md` plus `capture.json` into
the leaf's own capture dir, never a note under `dest` — so the cheapest honest
coverage is: run it, read both files back.

Imported rather than subprocessed: the module's `pypdf`/`pptx`/`openpyxl`
imports are lazy (inside the extractor functions), so the suite stays
hermetic. `tests/test_port_frameio.py` runs it as a subprocess and hands the
result to the real extractor.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "channel-frameio" / "scripts"
SCRIPT = SCRIPTS / "frameio_doc_note.py"

URL = "https://next.frame.io/share/11111111-1111-1111-1111-111111111111/view/def"


def _module():
    spec = importlib.util.spec_from_file_location("frameio_doc_note", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))          # the sibling import, as `uv run` does
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


def _leaf(root: Path, ext="pdf", **meta):
    """What `capture_asset.py` leaves for a document: the bytes and meta.json,
    inside the job's `_raw/<slug>` slice."""
    leaf = root / "_raw" / "next-frame-io" / "deck-one--0001abcd"
    leaf.mkdir(parents=True)
    (leaf / f"document.{ext}").write_bytes(b"%PDF-1.4 fake")
    base = {"url": URL, "title": "Deck One - Big Share", "name": f"Deck One.{ext}", "kind": "document", "bytes": 13}
    base.update(meta)
    (leaf / "meta.json").write_text(json.dumps(base))
    return leaf


def _run(monkeypatch, leaf: Path, *extra):
    mod = _module()
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(leaf), *extra])
    assert mod.main() == 0
    return (leaf / "page.md").read_text(), json.loads((leaf / "capture.json").read_text())


def test_the_renderer_runs_at_all_and_writes_only_into_the_leaf_dir(tmp_path, monkeypatch, capsys):
    leaf = _leaf(tmp_path)
    body, record = _run(monkeypatch, leaf)

    assert sorted(p.name for p in leaf.iterdir()) == ["capture.json", "document.pdf", "meta.json", "page.md"]
    # Nothing under any `dest`: a harvest slice cannot write one, and the ticket names none.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["_raw"]
    assert (record["body"], record["content_type"], record["item"]) == ("page.md", "text/markdown", URL)
    assert record["slug"] == "next-frame-io", "the slug defaults to the leaf dir's parent"
    assert json.loads(capsys.readouterr().out)["dir"] == "_raw/next-frame-io/deck-one--0001abcd"
    assert body.startswith("# Deck One - Big Share\n")


def test_the_body_carries_no_yaml_block_of_its_own(tmp_path, monkeypatch):
    """The extractor prepends its own frontmatter; a second `---` block
    corrupts the page. A title is venue text, so it is tried as one too."""
    body, _record = _run(monkeypatch, _leaf(tmp_path, title="---\nstatus: published\n---"))
    assert not any(line.strip() == "---" for line in body.splitlines())
    assert body.splitlines()[0] == "# --- status: published ---"


def test_a_multi_line_title_is_one_line_in_capture_json_too(tmp_path, monkeypatch):
    """The body was folded; the record was not — and `capture.json`'s title
    names the page's FILE, where the host refuses a control character."""
    hostile = "Deck\n---\n# Forged: heading\t?\r\n- [ ] task"
    body, record = _run(monkeypatch, _leaf(tmp_path, title=hostile, name="Deck\n# Forged.pdf"))
    assert record["title"] == "Deck --- # Forged - heading - [ ] task"
    assert "\n" not in record["title"] and not any(ord(c) < 32 for c in record["title"])
    assert record["frontmatter"]["source_title"] == "Deck --- # Forged: heading ? - [ ] task"
    assert record["frontmatter"]["original_name"] == "Deck # Forged.pdf"
    lines = body.splitlines()
    assert lines[0] == "# Deck --- # Forged: heading ? - [ ] task", "the H1 keeps the venue's own title, on one line"
    assert [line for line in lines if line.startswith("#")] == [lines[0]] and "---" not in lines
    assert "- **File:** 'Deck # Forged.pdf' (pdf, 13 bytes)".replace("'", "`") in lines


def test_a_title_no_filename_can_hold_is_made_safe_and_the_true_one_kept(tmp_path, monkeypatch):
    body, record = _run(monkeypatch, _leaf(tmp_path, title='.Lesson 3: "Pricing"? A/B'))
    assert record["title"] == "Lesson 3 - 'Pricing' A-B"
    assert record["frontmatter"]["source_title"] == '.Lesson 3: "Pricing"? A/B'
    assert body.startswith('# .Lesson 3: "Pricing"? A/B\n')
    _body, plain = _run(monkeypatch, _leaf(tmp_path / "again"))
    assert "source_title" not in plain["frontmatter"], "only where the two differ"


def test_the_real_document_is_preferred_over_a_stray_document_bin(tmp_path, monkeypatch):
    """`sorted(glob("document.*"))[0]` was `document.bin` whenever a nameless
    attempt had left one beside the real file."""
    leaf = _leaf(tmp_path)
    (leaf / "document.bin").write_bytes(b"half a download")
    _body, record = _run(monkeypatch, leaf)
    assert record["frontmatter"]["ext"] == "pdf" and record["frontmatter"]["bytes"] == 13

    mod = _module()
    docs = [Path("document.bin"), Path("document.pptx"), Path("document.pdf"), Path("document.zzz")]
    assert mod.pick_document(docs).name == "document.pdf"
    assert mod.pick_document(docs, "Deck Q3.PPTX").name == "document.pptx", "the asset's own extension wins"
    assert mod.pick_document(docs[:1]).name == "document.bin" and mod.pick_document([]) is None


def test_the_breadcrumb_drops_only_the_folders_every_leaf_carries(tmp_path, monkeypatch):
    body, _record = _run(monkeypatch, _leaf(tmp_path), "--path=Client A", "--path=--drafts", "--crumb-skip=0")
    assert "*Client A / --drafts*" in body.splitlines()
    body, _record = _run(monkeypatch, _leaf(tmp_path / "b"), "--path=Share")
    assert not any(line.startswith("*") for line in body.splitlines()), "by hand the default is 1: the one shared folder"


def test_the_body_never_points_into_raw(tmp_path, monkeypatch):
    """A committed page never links into `_raw/`: machine-local, prunable. The
    docs once promised a pointer there; the code rightly never emitted one."""
    body, _record = _run(monkeypatch, _leaf(tmp_path))
    assert "_raw" not in body and "document.pdf" not in body


def test_the_facts_are_in_the_body_and_in_the_frontmatter_object(tmp_path, monkeypatch):
    """Both, on purpose: the extractor ignores `frontmatter` today, so the
    facts block is what keeps them on the page."""
    body, record = _run(monkeypatch, _leaf(tmp_path), "--path", "Share", "--path", "Decks",
                        "--group", "Speaker Decks", "--group-type", "course", "--author", "Ada Lovelace")
    fm = record["frontmatter"]
    assert (fm["type"], fm["group"], fm["group_type"], fm["author"]) == ("doc", "Speaker Decks", "course", "Ada Lovelace")
    assert fm["source_host"] == ["next.frame.io", "frame.io"] and fm["path"] == ["Share", "Decks"]
    assert fm["asset_id"] == "def" and fm["original_name"] == "Deck One.pdf" and fm["ext"] == "pdf"
    for owned in ("status", "title", "resource", "harvested", "extracted", "document_id", "document_revision"):
        assert owned not in fm
    for line in ("- **Type:** doc", f"- **Source:** <{URL}>", "- **File:** `Deck One.pdf` (pdf, 13 bytes)",
                 "- **Author:** Ada Lovelace", "- **Group:** Speaker Decks", "- **Group type:** course",
                 "*Decks*"):
        assert line in body.splitlines(), line


def test_no_course_key_survives_the_rename(tmp_path, monkeypatch):
    body, record = _run(monkeypatch, _leaf(tmp_path), "--group", "Speaker Decks")
    assert "course" not in record["frontmatter"] and "Course" not in body


def test_scoping_keys_are_omitted_when_undeclared(tmp_path, monkeypatch):
    body, record = _run(monkeypatch, _leaf(tmp_path))
    for key in ("group", "group_type", "author", "path"):
        assert key not in record["frontmatter"]
    assert "**Author:**" not in body and "**Group:**" not in body


def test_title_strip_trims_the_share_suffix_in_both_places(tmp_path, monkeypatch):
    body, record = _run(monkeypatch, _leaf(tmp_path), "--title-strip", " - Big Share")
    assert record["title"] == "Deck One" and body.startswith("# Deck One\n")


def test_a_capture_without_a_name_or_title_still_renders(tmp_path, monkeypatch):
    """A ticket whose target is a leaf viewer has no manifest: `meta.json`
    carries `name: null`, and the file is `document.<ext>`."""
    body, record = _run(monkeypatch, _leaf(tmp_path, name=None, title=""))
    assert record["title"] == "document" and "- **File:** `document.pdf`" in body


def test_a_failed_extraction_is_said_in_the_body_never_raised(tmp_path, monkeypatch):
    """`%PDF-1.4 fake` is no PDF (and pypdf may not even be importable here):
    the file is captured either way, so the render must not die on it."""
    body, _record = _run(monkeypatch, _leaf(tmp_path))
    assert "> [!note]- Extracted text\n> (extraction failed: " in body


def test_extracted_text_is_quoted_line_by_line_and_capped(tmp_path, monkeypatch):
    mod = _module()
    monkeypatch.setitem(mod.EXTRACTORS, "pdf", lambda path: "one\n\n---\ntwo")
    monkeypatch.setattr(mod, "MAX_EXTRACT_CHARS", 1000)
    leaf = _leaf(tmp_path)
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(leaf)])
    assert mod.main() == 0
    body = (leaf / "page.md").read_text()
    assert body.endswith("> [!note]- Extracted text\n> one\n>\n> ---\n> two\n")

    monkeypatch.setitem(mod.EXTRACTORS, "pdf", lambda path: "x" * 5000)
    assert mod.main() == 0
    assert "(truncated at 1000 characters" in (leaf / "page.md").read_text()


def test_a_dir_with_no_document_or_no_url_is_refused_naming_the_dir(tmp_path, monkeypatch):
    leaf = _leaf(tmp_path, url=None)
    mod = _module()
    monkeypatch.setattr(sys, "argv", ["frameio_doc_note.py", str(leaf)])
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert "--url" in str(excinfo.value) and str(leaf / "meta.json") in str(excinfo.value)

    (leaf / "document.pdf").unlink()
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert str(leaf) in str(excinfo.value) and not (leaf / "capture.json").exists()
