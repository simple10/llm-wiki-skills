"""The frameio unit's note builder actually runs.

This file exists because it did not. The `domains` -> `areas`/`source_host`
rename left two guaranteed crashes in it — a read of `args.course` after the
flag became `--group`, and a `list.append(a, b)` — and every suite stayed
green, because nothing had ever executed the script. A note builder is a
frontmatter writer, so the cheapest honest coverage is: run it, read the
frontmatter back.

Imported rather than subprocessed: the module's `pptx`/`openpyxl` imports are
lazy (inside the two extractor functions), so the PDF path needs neither, and
the suite stays hermetic.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1] / "skills"
          / "channel-frameio" / "scripts" / "frameio_doc_note.py")


def _module():
    spec = importlib.util.spec_from_file_location("frameio_doc_note", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DEST = "sources/decks/next-frame-io"


def _capture(root: Path, **record):
    """Inside the WATCH's `_raw/<slug>` slice, carrying the `dest` the host
    wrote onto it — the shape `job_from_watch` actually produces."""
    cap = root / "_raw" / "next-frame-io" / "share--0001"
    cap.mkdir(parents=True)
    (cap / "document.pdf").write_bytes(b"%PDF-1.4 fake")
    (cap / "meta.json").write_text(json.dumps({"name": "Deck One.pdf"}))
    base = {"url": "https://next.frame.io/share/abc/view/def",
            "title": "Deck One", "dest": DEST,
            "fetched_at": "2026-07-15T00:00:00Z", "path": ["Share", "Decks"],
            "tags": ["decks"], "areas": ["Marketing"],
            "assets": [{"local_path": "document.pdf"}]}
    base.update(record)
    (cap / "capture.json").write_text(json.dumps(base))
    return cap


def _run(monkeypatch, root: Path, cap: Path, *extra):
    mod = _module()
    # No `--notes-dir`: the documented call site passes `--capture-dir`
    # alone, and the bundle root comes off the capture's own `dest`.
    monkeypatch.setattr(sys, "argv", [
        "frameio_doc_note.py", str(root),
        "--capture-dir", str(cap.relative_to(root)), *extra])
    mod.main()
    notes = list((root / DEST / "pages").glob("*.md"))
    assert len(notes) == 1, notes
    return notes[0].read_text()


def _fm(note, key):
    for line in note.splitlines():
        if line.startswith(f"{key}:"):
            return line.partition(":")[2].strip()
    return None


def test_the_note_builder_runs_at_all(tmp_path, monkeypatch):
    """The regression: `args.course` raised AttributeError and the two-arg
    `fm.append` raised TypeError, on every single invocation."""
    note = _run(monkeypatch, tmp_path, _capture(tmp_path))
    assert note.startswith("---\n")
    # This asserted `captured: 2026-07-15` until the date collapse retired
    # that key. The builder mints no provenance of its own — `handoff
    # complete` stamps `generated` (stage: process) downstream, pinned in
    # test_handoff_events — so what it owns is the frontmatter SHAPE, and
    # that is what is asserted: every key it writes, and nothing else.
    keys = [ln.partition(":")[0] for ln in
            note.split("---\n", 2)[1].splitlines() if ":" in ln]
    assert keys == ["title", "resource", "type", "source_host", "areas",
                    "tags", "status"], keys


def test_scoping_frontmatter_matches_the_generic_scaffold_shape(tmp_path,
                                                                monkeypatch):
    note = _run(monkeypatch, tmp_path, _capture(tmp_path),
                "--group", "Speaker Decks", "--group-type", "course",
                "--author", "Ada Lovelace")
    assert _fm(note, "group") == "Speaker Decks"
    assert _fm(note, "group_type") == "course"
    assert _fm(note, "author") == "Ada Lovelace"
    assert _fm(note, "source_host") == "[next.frame.io, frame.io]"
    assert _fm(note, "areas") == '["[[Marketing]]"]'
    assert _fm(note, "tags") == "[decks]"


def test_no_course_key_survives_the_rename(tmp_path, monkeypatch):
    note = _run(monkeypatch, tmp_path, _capture(tmp_path),
                "--group", "Speaker Decks")
    assert "course:" not in note


def test_scoping_keys_are_omitted_when_undeclared(tmp_path, monkeypatch):
    note = _run(monkeypatch, tmp_path, _capture(tmp_path))
    assert _fm(note, "group") is None
    assert _fm(note, "author") is None


def test_scoping_values_default_from_the_capture_record(tmp_path, monkeypatch):
    """A watch declares these once; the capture record carries them, so the
    worker does not have to repeat them on the command line."""
    note = _run(monkeypatch, tmp_path,
                _capture(tmp_path, author="Ada Lovelace", group="Decks",
                         group_type="course"))
    assert _fm(note, "author") == "Ada Lovelace"
    assert _fm(note, "group") == "Decks"
    assert _fm(note, "group_type") == "course"


def test_a_capture_with_no_dest_is_refused_rather_than_guessed(tmp_path,
                                                               monkeypatch):
    """The twin of `test_youtube_note.py`'s. `--notes-dir` stopped being
    required so the documented call site would run at all, which makes the
    capture's own `dest` the only source of the bundle root — and no third
    fallback, because a guessed bundle silently disagrees with wherever the
    watch's notes actually land.

    Asserted on the PATH the refusal names, not just on the two words: both
    of those survive an f-string whose braces are doubled, which is how the
    message shipped printing `{cap_dir / 'capture.json'}` verbatim.
    """
    cap = _capture(tmp_path, dest=None)
    mod = _module()
    monkeypatch.setattr(sys, "argv", [
        "frameio_doc_note.py", str(tmp_path),
        "--capture-dir", str(cap.relative_to(tmp_path))])

    with pytest.raises(SystemExit) as excinfo:
        mod.main()

    msg = str(excinfo.value)
    assert "dest" in msg and "--notes-dir" in msg, msg
    assert str(cap / "capture.json") in msg, msg
