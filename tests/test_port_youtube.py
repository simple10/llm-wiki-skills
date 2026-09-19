"""`channel-youtube` on the rebuilt worker contract: what the unit leaves in a
capture directory at HARVEST is what the host's real extractor turns into the
page — because no process ticket ever reaches a unit.

Three layers, cheapest first: the pure logic imported in-process (runs
everywhere), the two scripts run as the subprocesses a worker runs (needs only
`uv`), and ONE end-to-end case through the real `pipeline extract` (needs the
ops CLI, and the plugin's transcript formatter named by `LLM_WIKI_OPS_PLUGIN`).

The scripts are run from the WORKING TREE, never through `run ops/skills/…`:
the session wiki installs units from git HEAD.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import declared_job, extracted, ticket_in

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "channel-youtube" / "scripts"
BUILDER = SCRIPTS / "youtube_note.py"
REPORTER = SCRIPTS / "write_report.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "youtube"
META = json.loads((FIXTURES / "metadata.json").read_text(encoding="utf-8"))
ITEM = META["webpage_url"]

UNIT = "channel-youtube"
# The target `tests/test_port_smoke.py` declares this same job with. The slug
# `declared_job` derives is per UNIT and a slug names one source for good, so
# a second target here would be refused whenever the smoke case ran first. The
# ITEM a ticket carries is its own, and is what this file varies.
JOB_TARGET = "https://www.youtube.com/watch?v=smoke"

# Keys a host verb owns on the page (the port brief's list): never in `frontmatter`.
HOST_OWNED = {"status", "document_id", "document_revision", "harvested", "extracted", "title", "resource"}


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def builder():
    return _load(BUILDER)


@pytest.fixture(scope="module")
def reporter():
    return _load(REPORTER)


# ------------------------------------------------------------------ pure logic


def test_frontmatter_carries_the_facts_and_no_key_the_host_owns(builder):
    front = builder.frontmatter_for(META)
    assert front == {
        "type": "video",
        "channel": "Example Strength",
        "channel_url": "https://www.youtube.com/channel/UCexample000000000000000",
        "published": "2026-06-18",
        "duration": "3:07",
        "views": 517273,
        "likes": 16124,
        "video_id": "dQw4fixture",
        "thumbnail": "https://i.ytimg.com/vi/dQw4fixture/maxresdefault.jpg",
        "source_host": ["www.youtube.com", "youtube.com"],
    }
    assert not HOST_OWNED & set(front)


def test_frontmatter_is_scalars_and_flat_lists_only(builder):
    front = builder.frontmatter_for(META, tags=["You Tube", "video", "video"], areas=["Fitness", ""])
    assert front["tags"] == ["you-tube", "video"] and front["areas"] == ["[[Fitness]]"]
    for value in front.values():
        assert isinstance(value, (str, int, list)), value
        if isinstance(value, list):
            assert all(isinstance(one, str) for one in value), value


@pytest.mark.parametrize("upload_date", [None, "", "2026", "2026-06-18", "junk"])
def test_published_is_omitted_when_the_upload_date_is_not_known(builder, upload_date):
    """Absent means unknown; an empty `published` is a malformed value."""
    front = builder.frontmatter_for({**META, "upload_date": upload_date})
    assert "published" not in front


def test_an_unknown_fact_is_omitted_never_emitted_empty(builder):
    front = builder.frontmatter_for({"id": "x", "title": "T"})
    assert front == {"type": "video", "video_id": "x", "source_host": ["www.youtube.com", "youtube.com"]}
    # …while a true zero is a fact, not an absence.
    assert builder.frontmatter_for({"id": "x", "like_count": 0})["likes"] == 0


def test_the_facts_block_repeats_the_frontmatter_in_the_body(builder):
    """The extractor ignores `frontmatter` today, so the body is where the
    facts survive."""
    block = builder.facts_block(builder.frontmatter_for(META), ITEM)
    assert block.splitlines() == [
        "- **Channel**: [Example Strength](https://www.youtube.com/channel/UCexample000000000000000)",
        "- **Published**: 2026-06-18",
        "- **Duration**: 3:07",
        "- **Views**: 517273 · **Likes**: 16124",
        "- **Video ID**: `dQw4fixture`",
        f"- **Source**: <{ITEM}>",
    ]
    assert builder.facts_block(builder.frontmatter_for({"id": "x"}), None) == "- **Video ID**: `x`"


def test_the_body_never_opens_with_a_frontmatter_fence_and_has_no_summary_placeholder(builder):
    """The fixture description OPENS with `---`. With no thumbnail and no id
    above it, the description is the first block — and it still sits under its
    heading, so the extractor's own frontmatter is the page's only one."""
    bare = {"title": "T", "description": META["description"]}
    body, has_desc = builder.build_body(bare, builder.frontmatter_for(bare), None, "")
    assert has_desc and body.startswith("- **") is False and body.startswith("## Description\n")
    full, _ = builder.build_body(META, builder.frontmatter_for(META), ITEM, "#### [00:00] x\n\nwords\n")
    assert not full.lstrip().startswith("---")
    assert "[!summary]" not in full and "TODO-SUMMARY" not in full
    assert full.index("![thumbnail]") < full.index("<iframe") < full.index("- **Channel**") < full.index("## Description") < full.index("## Transcript")
    # the description's own conversions survived the port
    assert "<https://example.com/programme>" in full and "- `1:30` How to progress" in full and "#strength" not in full


def test_a_report_derives_captured_from_the_capture_and_never_claims_it(reporter, tmp_path):
    (tmp_path / "page.md").write_text("body\n")
    (tmp_path / "capture.json").write_text(json.dumps({"item": ITEM, "title": "T", "body": "page.md"}))
    report = reporter.build_report(tmp_path, "_raw/s/leaf--00000000", ticket="abc", outcome="ok")
    assert report == {
        "v": 1, "ticket": "abc", "outcome": "ok", "reason": None,
        "captured": [{"item": ITEM, "dir": "_raw/s/leaf--00000000", "title": "T"}],
        "written": [], "missing": [], "discovered": [],
    }


@pytest.mark.parametrize("outcome", ["ok", "partial", "unchanged"])
def test_a_report_cannot_say_a_capture_landed_when_none_did(reporter, tmp_path, outcome):
    """An aborted build leaves no `capture.json`; a record naming a body that
    is not there is the same lie."""
    with pytest.raises(ValueError, match="names no body file"):
        reporter.build_report(tmp_path, "_raw/s/l", ticket="abc", outcome=outcome, reason="r")
    (tmp_path / "capture.json").write_text(json.dumps({"item": ITEM, "body": "page.md"}))
    with pytest.raises(ValueError, match="names no body file"):
        reporter.build_report(tmp_path, "_raw/s/l", ticket="abc", outcome=outcome, reason="r")


def test_a_failed_report_carries_its_reason_and_its_missing_hosts(reporter, tmp_path):
    report = reporter.build_report(
        tmp_path, "_raw/s/l", ticket="abc", outcome="failed", reason="proxy refused the host",
        missing=[("rr1.googlevideo.com", "https://rr1.googlevideo.com/x", "denied")],
    )
    assert report["captured"] == [] and report["reason"] == "proxy refused the host"
    assert report["missing"] == [{"host": "rr1.googlevideo.com", "url": "https://rr1.googlevideo.com/x", "why": "denied"}]
    with pytest.raises(ValueError, match="must say why"):
        reporter.build_report(tmp_path, "_raw/s/l", ticket="abc", outcome="failed")
    with pytest.raises(ValueError, match="why must be one of"):
        reporter.build_report(tmp_path, "_raw/s/l", ticket="abc", outcome="failed", reason="r", missing=[("h", "u", "blocked")])


# ------------------------------------------------------------------ the scripts, as a worker runs them


def _formatter():
    """The plugin's real formatter, or a skip with the true reason."""
    plugin = os.environ.get("LLM_WIKI_OPS_PLUGIN")
    if not plugin:
        pytest.skip("set LLM_WIKI_OPS_PLUGIN to the ops plugin's root — format_transcript.py is host code, not this package's")
    rel = re.search(r'^FORMATTER = "([^"]+)"$', BUILDER.read_text(encoding="utf-8"), re.M).group(1)
    path = Path(plugin) / rel
    assert path.is_file(), f"{rel} is not under LLM_WIKI_OPS_PLUGIN={plugin} — did the plugin move it?"
    return path


def _fill(cap):
    """What the two yt-dlp commands in SKILL.md leave — from fixtures, no network."""
    shutil.copy(FIXTURES / "metadata.json", cap / "metadata.json")
    (cap / "captions").mkdir(exist_ok=True)
    shutil.copy(FIXTURES / "dQw4fixture.en.vtt", cap / "captions" / "dQw4fixture.en.vtt")


def _script(script, root, cap, *argv, check=True):
    cp = subprocess.run(
        ["uv", "run", "-q", str(script), str(root), "--capture-dir", str(cap.relative_to(root)), *argv],
        capture_output=True, text=True, check=False,
    )
    if check:
        assert cp.returncode == 0, cp.stderr
    return cp


def _stub_formatter(tmp_path):
    stub = tmp_path / "fmt.py"
    stub.write_text("print('#### [00:00] stub\\n\\nstubbed words')\n")
    return stub


def _ticketed(tmp_path):
    cap = tmp_path / "_raw" / "yt-job" / "watch--1a2b3c4d"
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps({
        "v": 1, "ticket": "8c1d2e3f4a5b", "unit": UNIT, "slug": "yt-job", "item": ITEM, "target": ITEM,
        "capture_dir": "_raw/yt-job/watch--1a2b3c4d", "dest": None, "hosts": ["www.youtube.com"]}))
    _fill(cap)
    return cap


def test_the_capture_record_is_the_extractors_whole_agreement(tmp_path):
    cap = _ticketed(tmp_path)
    _script(BUILDER, tmp_path, cap, "--format-transcript", str(_stub_formatter(tmp_path)))
    record = json.loads((cap / "capture.json").read_text())
    assert {k: record[k] for k in ("slug", "item", "title", "body", "content_type")} == {
        "slug": "yt-job", "item": ITEM, "title": META["title"], "body": "page.md", "content_type": "text/markdown"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["fetched_at"])
    # every field the extractor reads as text IS text
    assert all(isinstance(record[k], str) for k in ("slug", "item", "title", "body", "fetched_at"))
    assert record["frontmatter"]["type"] == "video" and record["frontmatter"]["published"] == "2026-06-18"
    assert not HOST_OWNED & set(record["frontmatter"])
    assert "dest" not in record
    assert "stubbed words" in (cap / "page.md").read_text()


def test_a_rerun_over_the_same_capture_is_byte_identical(tmp_path):
    """A widen respawns the worker into the SAME directory: the second build
    replaces the first, and `fetched_at` is when yt-dlp wrote the metadata,
    not when the builder happened to run."""
    cap = _ticketed(tmp_path)
    fmt = str(_stub_formatter(tmp_path))
    _script(BUILDER, tmp_path, cap, "--format-transcript", fmt)
    first = ((cap / "page.md").read_bytes(), (cap / "capture.json").read_bytes())
    _script(BUILDER, tmp_path, cap, "--format-transcript", fmt)
    assert first == ((cap / "page.md").read_bytes(), (cap / "capture.json").read_bytes())


def test_no_captions_is_a_page_without_a_transcript_and_says_so(tmp_path):
    cap = _ticketed(tmp_path)
    shutil.rmtree(cap / "captions")
    out = json.loads(_script(BUILDER, tmp_path, cap).stdout)  # no formatter needed: nothing to format
    assert out["has_transcript"] is False
    assert "## Transcript" not in (cap / "page.md").read_text()
    _script(REPORTER, tmp_path, cap, "--outcome", "partial", "--reason", "no_captions")
    report = json.loads((cap / "report.json").read_text())
    assert (report["outcome"], report["reason"], len(report["captured"])) == ("partial", "no_captions", 1)


def test_no_metadata_is_refused_by_name_and_reported_failed(tmp_path):
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").unlink()
    cp = _script(BUILDER, tmp_path, cap, check=False)
    assert cp.returncode != 0 and "metadata.json" in cp.stderr and "Traceback" not in cp.stderr
    # `ok` over a capture that never landed is refused, and writes nothing…
    cp = _script(REPORTER, tmp_path, cap, "--outcome", "ok", check=False)
    assert cp.returncode != 0 and "names no body file" in cp.stderr and not (cap / "report.json").exists()
    # …and `failed` is what the worker reports instead.
    _script(REPORTER, tmp_path, cap, "--outcome", "failed", "--reason", "yt-dlp: Video unavailable",
            "--missing", "www.youtube.com", ITEM, "error")
    report = json.loads((cap / "report.json").read_text())
    assert report["ticket"] == "8c1d2e3f4a5b" and report["outcome"] == "failed" and report["captured"] == []
    assert report["missing"] == [{"host": "www.youtube.com", "url": ITEM, "why": "error"}]


def test_the_report_names_the_tickets_own_capture_dir_and_a_hand_run_needs_a_ticket_id(tmp_path):
    cap = _ticketed(tmp_path)
    _script(BUILDER, tmp_path, cap, "--format-transcript", str(_stub_formatter(tmp_path)))
    _script(REPORTER, tmp_path, cap, "--outcome", "ok")
    report = json.loads((cap / "report.json").read_text())
    assert set(report) == {"v", "ticket", "outcome", "reason", "captured", "written", "missing", "discovered"}
    assert report["captured"] == [{"item": ITEM, "dir": "_raw/yt-job/watch--1a2b3c4d", "title": META["title"]}]
    # `spawn: none`: no ticket.json, so the id is handed over — or refused.
    (cap / "ticket.json").unlink()
    (cap / "report.json").unlink()
    cp = _script(REPORTER, tmp_path, cap, "--outcome", "ok", check=False)
    assert cp.returncode != 0 and "--ticket" in cp.stderr and not (cap / "report.json").exists()
    _script(REPORTER, tmp_path, cap, "--outcome", "ok", "--ticket", "feedfacecafe")
    assert json.loads((cap / "report.json").read_text())["ticket"] == "feedfacecafe"


# ------------------------------------------------------------------ end to end, through the real extractor


def test_a_harvested_video_becomes_the_staged_page(ops, env, wiki):
    """The whole point of the port. A ticketed capture dir holding what yt-dlp
    leaves (fixtures; no network) → this unit's builder and reporter → the REAL
    `pipeline extract` → one staged page under the job's `dest`, carrying the
    venue-specific body verbatim under the extractor's own frontmatter."""
    formatter = _formatter()
    job = declared_job(ops, env, wiki, UNIT, JOB_TARGET)
    cap = ticket_in(wiki, job, "watch--5e2e0001", unit=UNIT, item=ITEM)
    _fill(cap)

    out = json.loads(_script(BUILDER, wiki, cap, "--format-transcript", str(formatter)).stdout)
    assert out["has_transcript"] is True and out["chapters"] == 2
    _script(REPORTER, wiki, cap, "--outcome", "ok")
    # Read NOW: the extractor leaves its own `report.json` in this directory,
    # over the harvest worker's, once `apply` has read it.
    harvest_report = json.loads((cap / "report.json").read_text())
    assert harvest_report["ticket"] == "0123456789ab" and harvest_report["outcome"] == "ok"
    assert harvest_report["captured"] == [{"item": ITEM, "dir": f"_raw/{job.slug}/watch--5e2e0001", "title": META["title"]}]
    record = json.loads((cap / "capture.json").read_text())
    assert record["slug"] == job.slug and record["item"] == ITEM

    (page,) = extracted(ops, env, wiki, cap)
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest), page

    # ONE frontmatter block — the extractor's — and the body after it is ours, verbatim.
    assert text.startswith("---\n")
    _, front, body = text.split("---\n", 2)
    assert "status: draft" in front and ITEM in front and "Progressive Overload, Explained" in front
    assert body.strip() == (cap / "page.md").read_text(encoding="utf-8").strip()
    assert len(re.findall(r"^---$", text, re.M)) == 3, "the two fences of the one block, plus the description's own rule"

    # the venue-specific body survived
    assert "![thumbnail](https://i.ytimg.com/vi/dQw4fixture/maxresdefault.jpg)" in body
    assert 'src="https://www.youtube.com/embed/dQw4fixture"' in body
    assert "- **Published**: 2026-06-18" in body and "- **Views**: 517273 · **Likes**: 16124" in body
    assert "- `1:30` How to progress" in body and "#strength" not in body
    # the real formatter: chapter-headed, de-duplicated, no cue markup, no sound tags
    assert re.search(r"^#+ \[00:00\] What overload is$", body, re.M) and re.search(r"^#+ \[01:30\] How to progress$", body, re.M)
    assert body.count("Progressive overload means doing") == 1
    assert "<c>" not in body and "[Music]" not in body
    assert "[!summary]" not in body
