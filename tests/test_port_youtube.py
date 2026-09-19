"""`channel-youtube` on the rebuilt worker contract: harvest leaves the bytes
and a capture record, and this unit's OWN process step writes the page.

Three layers, cheapest first: the pure logic imported in-process (runs
everywhere), the two scripts run as the subprocesses a worker runs (needs only
`uv`, with a stub front door for the page verbs), and end-to-end cases through
the real `page create` (needs the ops CLI, and the plugin's transcript
formatter named by `LLM_WIKI_OPS_PLUGIN`).

The scripts are run from the WORKING TREE, never through `run ops/skills/…`:
the session wiki installs units from git HEAD.
"""

import importlib.util
import json
import os
import re
import shlex
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

# Where a tmp-path case's process arm writes. A real job's is the ticket's.
DEST = "sources/youtube/yt-job"

# Keys a host verb owns on the page: never in the facts this unit computes.
# `resource` is the unit's to set at process — the page verb takes it — and
# `extracted` is added on the command line, not by `frontmatter_for`.
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
    front = builder.frontmatter_for({"id": "abc_123-XYZ", "title": "T"})
    assert front == {"type": "video", "video_id": "abc_123-XYZ", "source_host": ["www.youtube.com", "youtube.com"]}
    # …while a true zero is a fact, not an absence.
    assert builder.frontmatter_for({"id": "abc_123-XYZ", "like_count": 0})["likes"] == 0


def test_the_facts_block_repeats_the_frontmatter_in_the_body(builder):
    """A reader of the page sees the facts without opening its frontmatter."""
    block = builder.facts_block(builder.frontmatter_for(META), ITEM)
    assert block.splitlines() == [
        "- **Channel**: [Example Strength](https://www.youtube.com/channel/UCexample000000000000000)",
        "- **Published**: 2026-06-18",
        "- **Duration**: 3:07",
        "- **Views**: 517273 · **Likes**: 16124",
        "- **Video ID**: `dQw4fixture`",
        f"- **Source**: <{ITEM}>",
    ]
    assert builder.facts_block(builder.frontmatter_for({"id": "abc_123-XYZ"}), None) == "- **Video ID**: `abc_123-XYZ`"


def test_the_body_never_opens_with_a_frontmatter_fence_and_has_no_summary_placeholder(builder):
    """The fixture description OPENS with `---`. The body opens with the H1
    (CHANGED by the review fix: the true title is the body's H1 now), and the
    description is a blockquote, so its `---` is no line of the page's own —
    the page verb's frontmatter is the page's only fence, and there is no rule."""
    bare = {"title": "T", "description": META["description"]}
    body, has_desc = builder.build_body(bare, builder.frontmatter_for(bare), None, "")
    assert has_desc and body.startswith("# T\n\n## Description\n\n> \\---\n> Why adding")
    full, _ = builder.build_body(META, builder.frontmatter_for(META), ITEM, "#### [00:00] x\n\nwords\n")
    assert full.startswith("# Progressive Overload, Explained\n")
    assert not re.search(r"^\s*---\s*$", full, re.M)
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


def _stub_ops(tmp_path):
    """A stand-in for the front door's page verbs, for the cases whose wiki is
    a bare tmp directory. It writes what the real `page create`/`page edit`
    would, and refuses a `create` over a title that is already a page."""
    stub = tmp_path / "ops_stub.py"
    stub.write_text(
        "import json, os, pathlib, sys\n"
        "argv = sys.argv[1:]\n"
        "verb = [a for a in argv if not a.startswith('--')]\n"
        "pairs = dict(a.split('=', 1) for a in verb if '=' in a)\n"
        "rel = pairs['dest'].rstrip('/') + '/' + pairs['title'] + '.md' if verb[1] == 'create' else verb[2]\n"
        "out = pathlib.Path(os.getcwd()) / rel\n"
        "if verb[1] == 'create' and out.exists():\n"
        "    print(json.dumps({'error': rel + ' already exists \\u2014 the filename is the title'}))\n"
        "    sys.exit(2)\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(sys.stdin.read())\n"
        "pathlib.Path(os.getcwd(), 'page-calls.jsonl').open('a').write(json.dumps(argv) + '\\n')\n"
        "print(json.dumps({'path': rel, 'status': 'draft'}))\n"
    )
    return shlex.join(["uv", "run", "-q", str(stub)])


def _record(root, cap, *argv, check=True):
    """The HARVEST arm: the capture record for the bytes already on disk."""
    return _script(BUILDER, root, cap, "--record", *argv, check=check)


def _build(root, cap, *argv, dest=DEST, ops=None, check=True):
    """The PROCESS arm: the body, and the page under `dest`."""
    front_door = ops if ops is not None else _stub_ops(root)
    return _script(BUILDER, root, cap, "--dest", dest, "--ops", front_door, *argv, check=check)


def _page_calls(root):
    path = root / "page-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _ticketed(tmp_path):
    cap = tmp_path / "_raw" / "yt-job" / "watch--1a2b3c4d"
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps({
        "v": 1, "ticket": "8c1d2e3f4a5b", "unit": UNIT, "slug": "yt-job", "item": ITEM, "target": ITEM,
        "capture_dir": "_raw/yt-job/watch--1a2b3c4d", "dest": None, "hosts": ["www.youtube.com"]}))
    _fill(cap)
    return cap


def test_harvest_leaves_the_bytes_and_a_flat_capture_record(tmp_path):
    """Harvest is bytes. The record names the payload as it arrived, and
    carries no `frontmatter` object and no key a page verb owns — the facts
    reach the page because this unit writes the page."""
    cap = _ticketed(tmp_path)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    _record(tmp_path, cap)
    record = json.loads((cap / "capture.json").read_text())
    assert record == {
        "slug": "yt-job", "item": ITEM, "title": META["title"], "body": "metadata.json",
        "content_type": "application/json", "fetched_at": record["fetched_at"]}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["fetched_at"])
    assert {p for p in tmp_path.rglob("*") if p.is_file()} - before == {cap / "capture.json"}


def test_process_writes_the_page_under_dest_through_the_page_verbs(tmp_path):
    cap = _ticketed(tmp_path)
    out = json.loads(_build(tmp_path, cap, "--format-transcript", str(_stub_formatter(tmp_path))).stdout)
    assert out["written"] == [f"{DEST}/{META['title']}.md"]
    assert "stubbed words" in (tmp_path / out["written"][0]).read_text()
    assert "stubbed words" in (cap / "page.md").read_text()
    (create,) = _page_calls(tmp_path)
    assert create[:3] == ["--json", "page", "create"] and "--stdin" in create
    assert f"title={META['title']}" in create and f"dest={DEST}" in create
    assert "extracted=true" in create and f"resource={ITEM}" in create
    assert "type=video" in create and "published=2026-06-18" in create
    assert not [a for a in create if a.split("=")[0] in HOST_OWNED - {"extracted", "resource", "title"}]


def test_a_second_pull_edits_the_page_dest_already_holds(tmp_path):
    """`page create` refuses a title that is already a page, and that refusal
    is the signal to replace it — not to fail the ticket."""
    cap = _ticketed(tmp_path)
    fmt = str(_stub_formatter(tmp_path))
    _build(tmp_path, cap, "--format-transcript", fmt)
    _build(tmp_path, cap, "--format-transcript", fmt)
    verbs = [[a for a in call if not a.startswith("--")][:3] for call in _page_calls(tmp_path)]
    assert verbs[0][:2] == ["page", "create"], verbs
    assert verbs[1][:2] == ["page", "edit"] and verbs[1][2] == f"{DEST}/{META['title']}.md", verbs


def test_a_rerun_over_the_same_capture_is_byte_identical(tmp_path):
    """A widen respawns the worker into the SAME directory: the second build
    replaces the first, and `fetched_at` is when yt-dlp wrote the metadata,
    not when the builder happened to run."""
    cap = _ticketed(tmp_path)
    fmt = str(_stub_formatter(tmp_path))
    _record(tmp_path, cap)
    _build(tmp_path, cap, "--format-transcript", fmt)
    first = ((cap / "page.md").read_bytes(), (cap / "capture.json").read_bytes())
    _record(tmp_path, cap)
    _build(tmp_path, cap, "--format-transcript", fmt)
    assert first == ((cap / "page.md").read_bytes(), (cap / "capture.json").read_bytes())


def test_no_captions_is_a_page_without_a_transcript_and_says_so(tmp_path):
    cap = _ticketed(tmp_path)
    shutil.rmtree(cap / "captions")
    out = json.loads(_build(tmp_path, cap).stdout)  # no formatter needed: nothing to format
    assert out["has_transcript"] is False
    assert "## Transcript" not in (cap / "page.md").read_text()
    _script(REPORTER, tmp_path, cap, "--outcome", "partial", "--reason", "no_captions",
            "--written-from", "written.json")
    report = json.loads((cap / "report.json").read_text())
    assert (report["outcome"], report["reason"], report["captured"]) == ("partial", "no_captions", [])
    assert report["written"] == out["written"]


def test_no_metadata_is_refused_by_name_and_reported_failed(tmp_path):
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").unlink()
    cp = _record(tmp_path, cap, check=False)
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
    _record(tmp_path, cap)
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


# ------------------------------------------------------------------ end to end, through the real page verbs


def test_a_harvested_video_becomes_the_staged_page(ops, env, wiki):
    """The whole point of the rework. A ticketed capture dir holding what
    yt-dlp leaves (fixtures; no network) → harvest's capture record → this
    unit's own process step, through the REAL `page create` → one staged page
    under the job's `dest`, carrying the venue-specific body and the venue's
    own facts."""
    formatter = _formatter()
    job = declared_job(ops, env, wiki, UNIT, JOB_TARGET)
    cap = ticket_in(wiki, job, "watch--5e2e0001", unit=UNIT, item=ITEM)
    _fill(cap)

    _record(wiki, cap)
    _script(REPORTER, wiki, cap, "--outcome", "ok")
    harvest_report = json.loads((cap / "report.json").read_text())
    assert harvest_report["ticket"] == "0123456789ab" and harvest_report["outcome"] == "ok"
    assert harvest_report["captured"] == [{"item": ITEM, "dir": f"_raw/{job.slug}/watch--5e2e0001", "title": META["title"]}]
    record = json.loads((cap / "capture.json").read_text())
    assert record["slug"] == job.slug and record["item"] == ITEM and "frontmatter" not in record

    out = json.loads(_build(wiki, cap, "--format-transcript", str(formatter), dest=job.dest, ops=shlex.join(ops)).stdout)
    assert out["has_transcript"] is True and out["chapters"] == 2
    _script(REPORTER, wiki, cap, "--outcome", "ok", "--written-from", "written.json")
    process_report = json.loads((cap / "report.json").read_text())
    assert process_report["written"] == out["written"] and process_report["captured"] == []

    page = wiki / out["written"][0]
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest), page

    # ONE frontmatter block — the page verb's — and the body after it is ours, verbatim.
    assert text.startswith("---\n")
    _, front, body = text.split("---\n", 2)
    assert "status: draft" in front and ITEM in front and "Progressive Overload, Explained" in front
    # the facts the unit knows are ON THE PAGE, not re-guessed from the body
    assert "type: video" in front and "published: '2026-06-18'" in front and "extracted: 'true'" in front
    assert body.strip() == (cap / "page.md").read_text(encoding="utf-8").strip()
    # The description is a blockquote, so the page verb's two fences are all there are.
    assert len(re.findall(r"^---$", text, re.M)) == 2, "the two fences of the one frontmatter block"
    assert body.lstrip().startswith("# Progressive Overload, Explained\n")

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


# ================================================================== review fixes
# Rule 1 — the page's FILE is named from `capture.json`'s title, and the host
# refuses a title its filename rule cannot hold.

# `llm_wiki_ops/commands/page/note.py`: ILLEGAL, and what `filename_for` refuses.
ILLEGAL = '/\\:*?"<>|'


def _host_would_name(title):
    """`filename_for`'s own refusals, restated — plus the filesystem's byte cap,
    which the host does not check and the write then fails on."""
    name = title.strip()
    return (
        bool(name) and name == title and not name.startswith(".")
        and not any(ch in ILLEGAL or ord(ch) < 32 for ch in name)
        and len(f"{name}.md".encode()) <= 255
    )


@pytest.mark.parametrize("venue, safe", [
    ("Lesson 3: Pricing", "Lesson 3 - Pricing"),
    ("What is X?", "What is X"),
    ("A/B testing", "A-B testing"),
    ('He said "no" <twice> | a\\b * c', "He said ’no’ (twice) - a-b c"),
    (".hidden: what?", "hidden - what"),
    ("  ...  trailing dots and spaces . . ", "trailing dots and spaces"),
    ("line one\n---\n# Forged\ttabbed\x00nul\x7fdel", "line one --- # Forged tabbed nul del"),
    ("Plain, legal title", "Plain, legal title"),
])
def test_safe_title_makes_a_title_the_host_will_name_a_file_from(builder, venue, safe):
    assert builder.safe_title(venue) == safe
    assert _host_would_name(builder.safe_title(venue))


@pytest.mark.parametrize("venue", [None, "", "   ", "...", "?*", "\n\t"])
def test_safe_title_falls_back_when_nothing_legal_is_left(builder, venue):
    assert builder.safe_title(venue) == "Untitled"
    assert builder.safe_title(venue, fallback="YouTube video abc") == "YouTube video abc"


def test_safe_title_caps_characters_and_bytes(builder):
    long = builder.safe_title("word " * 60)
    assert len(long) <= builder.TITLE_MAX + 1 and long.endswith("…") and not long[:-1].endswith(" ")
    # 100 CJK characters is a legal YouTube title and 300 bytes: under the character cap, over a filename's.
    cjk = builder.safe_title("漢" * 100)
    assert cjk.endswith("…") and _host_would_name(cjk), len(f"{cjk}.md".encode())
    assert builder.safe_title("漢" * 60) == "漢" * 60, "a title that fits is not cut"


def test_the_record_title_is_safe_and_the_true_title_stays_on_the_page(builder):
    meta = {**META, "title": 'Lesson 3: Pricing? A/B "testing"'}
    assert builder.page_title(meta) == "Lesson 3 - Pricing A-B ’testing’"
    front = builder.frontmatter_for(meta)
    assert front["source_title"] == 'Lesson 3: Pricing? A/B "testing"'
    body, _ = builder.build_body(meta, front, ITEM, "")
    assert body.startswith('# Lesson 3: Pricing? A/B "testing"\n')
    # a title that was legal already says so by carrying no `source_title`
    assert "source_title" not in builder.frontmatter_for(META)
    # no title at all: named after the video, never after `page.md`
    assert builder.page_title({"id": "dQw4fixture"}) == "YouTube video dQw4fixture"
    assert builder.page_title({"title": 7}) == "Untitled video"


# Rule 2 — venue text never forges structure in `page.md`.

HOSTILE = {
    **META,
    "id": 'x" onload="alert(1)',
    "title": 'Real title\n---\n# Forged\n<script>alert(1)</script> "quoted" [[Secret]]',
    "uploader": "Chan]nel\n# Forged channel [[Link]]",
    "channel_url": "javascript:alert(1)",
    "thumbnail": "https://i.ytimg.com/x.jpg) ![x](https://evil.example/y.png",
    "upload_date": "20269999",
    "duration_string": "3:07\n# Forged duration",
    "view_count": "12\n# Forged views",
    "like_count": True,
    "description": (
        "# Forged heading\n```\nfence that would swallow the page\n~~~\n\n---\nSetext forged\n===\n"
        "> [!danger] callout\n[!note] callout\n<iframe src=https://evil.example></iframe> ![[Secret page]] %% hidden\n"
        "[click](javascript:alert(1)) and [fine](https://example.com/ok)\r\n"
        "TIMESTAMPS\n0:00 Intro <b>bold</b> [[Link]]\n1:30 Next\n\nafter https://example.com/a<b>x"
    ),
    "chapters": [{"start_time": 0, "end_time": 9, "title": "One\n# Forged chapter"}, {"start_time": "x", "title": "dropped"}],
}


def test_a_hostile_id_never_reaches_the_embed_or_the_facts(builder):
    front = builder.frontmatter_for(HOSTILE)
    assert "video_id" not in front
    body, _ = builder.build_body(HOSTILE, front, ITEM, "")
    assert "<iframe" not in body and "onload" not in body and "Video ID" not in body
    for bad in ("short", "a" * 21, "has space1", "semi;colon", 12345678901, None):
        assert "video_id" not in builder.frontmatter_for({"id": bad}), bad
    assert builder.frontmatter_for({"id": "dQw4w9WgXcQ"})["video_id"] == "dQw4w9WgXcQ"


def test_a_hostile_title_is_one_line_and_escaped_in_the_attribute(builder):
    meta = {**HOSTILE, "id": "dQw4fixture"}
    front = builder.frontmatter_for(meta)
    body, _ = builder.build_body(meta, front, ITEM, "")
    lines = body.split("\n")
    assert lines[0] == '# Real title --- # Forged &lt;script>alert(1)&lt;/script> "quoted" \\[\\[Secret]]'
    assert "# Forged" not in lines[1:] and not any(re.fullmatch(r"\s*---\s*", line) for line in lines)
    (iframe,) = [line for line in lines if line.startswith("<iframe")]
    assert 'title="Real title --- # Forged &lt;script&gt;alert(1)&lt;/script&gt; &quot;quoted&quot; [[Secret]]"' in iframe
    assert iframe.count('"') == 10, "five quoted attributes, and not one quote more"
    assert "\n" not in front["source_title"] and "\n" not in builder.page_title(meta)


def test_hostile_fact_values_are_folded_validated_or_dropped(builder):
    front = builder.frontmatter_for(HOSTILE)
    assert front["channel"] == "Chan]nel # Forged channel [[Link]]"
    for dropped in ("channel_url", "thumbnail", "published", "views", "likes"):
        assert dropped not in front, dropped
    assert front["duration"] == "03:07", "the numeric `duration` stands in for a `duration_string` that is not one"
    body, _ = builder.build_body(HOSTILE, front, "https://x.example/a> <script>", "")
    assert "- **Channel**: Chan\\]nel # Forged channel \\[\\[Link\\]\\]" in body
    assert "![thumbnail]" not in body and "evil.example/y.png" not in body and "**Source**" not in body
    assert not [line for line in body.split("\n") if line.startswith("# ")][1:], "one H1, the title's"


@pytest.mark.parametrize("upload_date, published", [
    ("20260618", "2026-06-18"), ("20260230", None), ("20269999", None), (20260618, None), ("2026-06-18", None),
])
def test_published_is_a_day_that_exists_or_absent(builder, upload_date, published):
    assert builder.frontmatter_for({"upload_date": upload_date}).get("published") == published


def test_a_hostile_description_cannot_open_a_block_of_the_pages_own(builder):
    md = builder.description_to_md(HOSTILE["description"])
    lines = md.split("\n")
    # THE guarantee: every line is inside the quote, so none is a top-level heading, fence or rule.
    assert all(line == ">" or line.startswith("> ") for line in lines), md
    inner = [line[2:] for line in lines]
    # …and inside it nothing opens a block either, so a fence cannot swallow the rest of the description.
    assert not [x for x in inner if re.match(r"\s*(#{1,6}\s|```|~~~|>|\[!|=+\s*$|-+\s*$)", x)], inner
    assert "\\# Forged heading" in inner and "\\```" in inner and "\\~~~" in inner and "\\---" in inner and "\\===" in inner
    assert "\\> [!danger] callout" in inner and "\\[!note] callout" in inner
    assert "<iframe" not in md and "![[" not in md and "[[" not in md.replace("\\[\\[", "") and "%%" not in md.replace("\\%\\%", "")
    assert "[click]\\(javascript:alert(1))" in md and "[fine](https://example.com/ok)" in md
    # the creator's TIMESTAMPS list still works — `\r\n` and all — and its labels are neutralised too
    assert "- `0:00` Intro &lt;b>bold&lt;/b> \\[\\[Link]]" in inner and "- `1:30` Next" in inner
    assert "**Timestamps**" in inner
    assert "after <https://example.com/a>&lt;b>x" in inner, "a url stops at `<`: nothing rides inside the autolink"
    assert builder.description_to_md(None) == "" and builder.description_to_md(" \n ") == "" and builder.description_to_md(7) == ""


def test_chapter_titles_are_folded_before_the_formatter_prints_them_as_headings(builder):
    assert builder.safe_chapters(HOSTILE) == [{"start_time": 0, "title": "One # Forged chapter", "end_time": 9}]
    assert builder.safe_chapters({"chapters": "junk"}) == [] and builder.safe_chapters({}) == []


def test_caption_text_cannot_open_a_fence_in_the_transcript(builder):
    md = "#### [00:00] Chapter\n\n```\n# not a heading of ours\nwords\n"
    assert builder.guard_transcript(md) == "#### [00:00] Chapter\n\n\\```\n\\# not a heading of ours\nwords\n"


def test_a_hostile_video_builds_a_page_whose_structure_is_all_ours(tmp_path):
    """The whole script over the hostile metadata: exit 0, and the only headings
    on the page are the title's H1 and this unit's own two sections."""
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").write_text(json.dumps(HOSTILE))
    fmt = tmp_path / "echo_chapters.py"   # prints the chapter titles it was handed, the way the real one does
    fmt.write_text(
        "import json, sys\n"
        "for c in json.load(open(sys.argv[sys.argv.index('--chapters') + 1])):\n"
        "    print('#### [00:00] ' + c['title'] + '\\n\\nwords\\n')\n")
    _record(tmp_path, cap)
    _build(tmp_path, cap, "--format-transcript", str(fmt))
    body = (cap / "page.md").read_text()
    heads = [line for line in body.split("\n") if re.match(r"#{1,6}\s", line)]
    assert [h for h in heads if not h.startswith("# Real title")] == ["## Description", "## Transcript", "#### [00:00] One # Forged chapter"]
    assert not re.search(r"^\s*(---|```|~~~)\s*$", body, re.M) and "<script" not in body and "onload" not in body
    assert not (cap / "chapters.safe.json").exists(), "the formatter's scratch file is not part of a capture"
    record = json.loads((cap / "capture.json").read_text())
    assert record["title"] == "Real title --- # Forged (script)alert(1)(-script) ’quoted’ [[Secret]]"
    (create,) = _page_calls(tmp_path)
    assert all("\n" not in arg for arg in create), "no fact value carries a newline onto the command line"


# Rule 3 — `llm-wiki-ops run` starts a script at the WIKI ROOT, not in the capture dir.


def _as_run_does(script, root, *argv):
    """THE DOCUMENTED WAY: cwd is the wiki root, the wiki is `.`, and the
    capture dir is the ticket's wiki-relative value — no absolute path anywhere."""
    return subprocess.run(["uv", "run", "-q", str(script), ".", *argv], cwd=root, capture_output=True, text=True, check=False)


def test_both_scripts_run_from_the_wiki_root_with_the_tickets_relative_capture_dir(tmp_path):
    cap = _ticketed(tmp_path)
    rel = json.loads((cap / "ticket.json").read_text())["capture_dir"]
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", rel, "--record")
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["capture"] == f"{rel}/capture.json"
    cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", rel, "--dest", DEST,
                      "--ops", _stub_ops(tmp_path), "--format-transcript", str(_stub_formatter(tmp_path)))
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["page"] == f"{rel}/page.md"
    cp = _as_run_does(REPORTER, tmp_path, "--capture-dir", rel, "--outcome", "ok")
    assert cp.returncode == 0, cp.stderr
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before
    assert new == {cap / "page.md", cap / "capture.json", cap / "report.json", cap / "written.json",
                   tmp_path / "fmt.py", tmp_path / "ops_stub.py", tmp_path / "page-calls.jsonl",
                   tmp_path / DEST / f"{META['title']}.md"}, new
    assert json.loads((cap / "report.json").read_text())["captured"][0]["dir"] == rel


@pytest.mark.parametrize("script, tail", [(BUILDER, ["--record"]), (REPORTER, ["--outcome", "failed", "--reason", "r"])])
def test_a_capture_dir_that_is_not_wiki_relative_is_refused(tmp_path, script, tail):
    cap = _ticketed(tmp_path)
    for bad in (str(cap), "_raw/yt-job/../yt-job/watch--1a2b3c4d", "_raw/yt-job/nope"):
        cp = _as_run_does(script, tmp_path, "--capture-dir", bad, *tail)
        assert cp.returncode != 0 and "Traceback" not in cp.stderr, (bad, cp.stderr)
    assert not (cap / "report.json").exists() and not (tmp_path / "report.json").exists()


def test_the_builder_refuses_a_directory_no_spawner_wrote_a_ticket_into(tmp_path):
    """`.` — the wiki root itself, which is what a capture-dir default of `.`
    used to mean under `run` — holds no ticket.json: refused, nothing written."""
    cap = _ticketed(tmp_path)
    shutil.copy(cap / "metadata.json", tmp_path / "metadata.json")
    cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", ".", "--record")
    assert cp.returncode != 0 and "ticket.json" in cp.stderr and "--item" in cp.stderr
    assert not (tmp_path / "page.md").exists() and not (tmp_path / "capture.json").exists()


# Rule 4 — a respawn must never be read as a success it did not have.


def _an_earlier_run(tmp_path):
    cap = _ticketed(tmp_path)
    _record(tmp_path, cap)
    _script(REPORTER, tmp_path, cap, "--outcome", "ok")
    assert json.loads((cap / "report.json").read_text())["outcome"] == "ok"
    return cap


def _respawn(cap):
    """What the spawner does on every dispatch: rewrite ticket.json."""
    ticket = json.loads((cap / "ticket.json").read_text())
    (cap / "ticket.json").write_text(json.dumps({**ticket, "ticket": "feedfacecafe"}))
    stamp = (cap / "ticket.json").stat().st_mtime_ns
    for name in ("capture.json", "page.md", "report.json"):   # make "earlier" unmistakable on a coarse clock
        if (cap / name).exists():
            os.utime(cap / name, ns=(stamp - 10**9, stamp - 10**9))


def test_a_failed_yt_dlp_on_a_respawn_is_not_reported_as_the_earlier_runs_capture(tmp_path):
    """`yt-dlp … > metadata.json` leaves an EMPTY file when yt-dlp fails. The
    builder used to traceback on it BEFORE removing the old capture.json, and
    `write_report --outcome ok` then said `captured: 1` for a video this run
    never captured."""
    cap = _an_earlier_run(tmp_path)
    _respawn(cap)
    (cap / "metadata.json").write_text("")
    cp = _record(tmp_path, cap, check=False)
    assert cp.returncode != 0 and "Traceback" not in cp.stderr
    assert "metadata.json" in cp.stderr and "yt-dlp failed" in cp.stderr
    for stale in ("capture.json", "page.md", "report.json"):
        assert not (cap / stale).exists(), f"{stale} survived a failed build"
    cp = _script(REPORTER, tmp_path, cap, "--outcome", "ok", check=False)
    assert cp.returncode != 0 and not (cap / "report.json").exists()


@pytest.mark.parametrize("junk", ["[1, 2]", '"text"', "{not json"])
def test_metadata_that_is_not_yt_dlps_object_is_refused_without_a_traceback(tmp_path, junk):
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").write_text(junk)
    cp = _record(tmp_path, cap, check=False)
    assert cp.returncode != 0 and "Traceback" not in cp.stderr and not (cap / "capture.json").exists()


def test_the_reporter_cannot_say_ok_off_a_capture_older_than_its_ticket(reporter, tmp_path):
    """The worker that never ran the builder at all (yt-dlp failed, it skipped
    to the report, and said `ok`): the capture on disk is the run before's."""
    cap = _an_earlier_run(tmp_path)
    _respawn(cap)
    assert reporter.is_stale(cap)
    for outcome in ("ok", "partial", "unchanged"):
        cp = _script(REPORTER, tmp_path, cap, "--outcome", outcome, "--reason", "r", check=False)
        assert cp.returncode != 0 and "EARLIER run" in cp.stderr, cp.stderr
        assert not (cap / "report.json").exists(), "a refusal leaves no report — not even the earlier run's"
    _script(REPORTER, tmp_path, cap, "--outcome", "failed", "--reason", "yt-dlp: Video unavailable")
    report = json.loads((cap / "report.json").read_text())
    assert (report["ticket"], report["outcome"], report["captured"]) == ("feedfacecafe", "failed", [])
    # …and a capture AFTER the respawn is this ticket's, and reportable.
    _record(tmp_path, cap)
    assert not reporter.is_stale(cap) and not (cap / "report.json").exists()
    _script(REPORTER, tmp_path, cap, "--outcome", "ok")
    assert len(json.loads((cap / "report.json").read_text())["captured"]) == 1


def test_a_written_report_exits_zero_whatever_it_says_and_a_refusal_does_not(tmp_path):
    """The status answers "was a report written", which is what a worker that
    must always leave one needs to know. `skipped` is step 1's `known[]` case."""
    cap = _ticketed(tmp_path)
    for outcome in ("failed", "skipped", "gone"):
        cp = _script(REPORTER, tmp_path, cap, "--outcome", outcome, "--reason", "known: already held", check=False)
        assert cp.returncode == 0 and json.loads(cp.stdout)["captured"] == 0
        report = json.loads((cap / "report.json").read_text())
        assert (report["outcome"], report["reason"], report["captured"]) == (outcome, "known: already held", [])
    cp = _script(REPORTER, tmp_path, cap, "--outcome", "skipped", check=False)
    assert cp.returncode != 0 and "must say why" in cp.stderr and not (cap / "report.json").exists()


# Rule 1, end to end — the page LANDS.


@pytest.mark.parametrize("leaf, title, safe", [
    ("hostile--5e2e0002", 'Lesson 3: Pricing? A/B "testing"', "Lesson 3 - Pricing A-B ’testing’"),
    ("hostile--5e2e0003", ".hidden: what is <X> | Y?\n---\n# Forged", "hidden - what is (X) - Y --- # Forged"),
    ("hostile--5e2e0004", "漢" * 100, None),
])
def test_a_title_no_filename_can_hold_still_lands_as_a_page(ops, env, wiki, tmp_path, leaf, title, safe):
    """Through the REAL `page create`, which names the page's file from the
    title and refuses `:` `?` `/` `"` or a leading dot outright."""
    job = declared_job(ops, env, wiki, UNIT, JOB_TARGET)
    item = f"https://www.youtube.com/watch?v={leaf[-8:]}xyz"
    cap = ticket_in(wiki, job, leaf, unit=UNIT, item=item)
    _fill(cap)
    (cap / "metadata.json").write_text(json.dumps({**META, "title": title}))
    _record(wiki, cap)
    record = json.loads((cap / "capture.json").read_text())

    # FIRST: the refusal this pins is `page create`'s own, not an assertion of ours.
    out = json.loads(_build(wiki, cap, "--format-transcript", str(_stub_formatter(tmp_path)),
                            dest=job.dest, ops=shlex.join(ops)).stdout)
    _script(REPORTER, wiki, cap, "--outcome", "ok", "--written", out["written"][0])
    report = json.loads((cap / "report.json").read_text())
    assert report["written"] == out["written"]
    page = wiki / out["written"][0]
    if safe:
        assert record["title"] == safe
    assert page.is_file() and page.name == f"{record['title']}.md"
    text = page.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    folded = " ".join(title.split())
    assert body.lstrip().startswith(f"# {folded.replace('<', '&lt;')}\n"), "the TRUE title is the H1"
    assert f"source_title: " in front and folded[:20] in front
    assert len(re.findall(r"^---$", text, re.M)) == 2 and "\n# Forged" not in text


def test_the_report_reads_the_pages_off_a_file_not_off_a_command_line(tmp_path):
    """A page's filename IS the video's title, and `safe_title` leaves `;`, `$`
    and a backtick in one — a filename may hold them. Typed onto the worker's
    Bash line that is the venue running a command, so the builder leaves the
    list under a fixed name and the report reads it."""
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").write_text(json.dumps({**META, "title": "Pricing;$(touch PWNED) `id`"}))
    _record(tmp_path, cap)
    out = json.loads(_build(tmp_path, cap, "--format-transcript", str(_stub_formatter(tmp_path))).stdout)
    assert json.loads((cap / "written.json").read_text()) == out["written"]
    assert ";" in out["written"][0] and "$(" in out["written"][0], out["written"]
    _script(REPORTER, tmp_path, cap, "--outcome", "ok", "--written-from", "written.json")
    assert json.loads((cap / "report.json").read_text())["written"] == out["written"]


@pytest.mark.parametrize("embeds, iframe", [(True, True), (False, False), (None, True)])
def test_process_embeds_false_takes_the_iframe_out(builder, embeds, iframe):
    """`process.embeds` is a key on the process ticket. No extractor sees this
    page any more, so the unit is the only thing that can honor it; absent, the
    embed stays, which is the record's own default."""
    front = builder.frontmatter_for(META)
    body, _has_desc = builder.build_body(META, front, ITEM, "", embeds=embeds)
    assert ("<iframe" in body) is iframe, body[:200]
    assert "![thumbnail]" in body, "only the embed goes; the thumbnail is a plain image"
