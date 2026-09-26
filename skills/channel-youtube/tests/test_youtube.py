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


UNIT_DIR = Path(__file__).resolve().parents[1]  # the unit, wherever its tree sits
SCRIPTS = UNIT_DIR / "scripts"
BUILDER = SCRIPTS / "youtube_note.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
META = json.loads((FIXTURES / "metadata.json").read_text(encoding="utf-8"))
ITEM = META["webpage_url"]

UNIT = "channel-youtube"
# A url job's ticket carries `item` off the job's own `target` at mint (a
# live ticket, never a hand fixture) — so the harness's default-slug job,
# which asserts `record["item"] == ITEM`, is declared with `target=ITEM`
# itself. A case wanting a job of its own passes `slug=` (`declared_job`).
JOB_TARGET = ITEM

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
    assert "<https://example.com/plan>" in full and "- `1:30` How to progress" in full and "#strength" not in full


# ------------------------------------------------------------------ the scripts, as a worker runs them


def _fill(cap):
    """What the two yt-dlp commands in SKILL.md leave — from fixtures, no network."""
    shutil.copy(FIXTURES / "metadata.json", cap / "metadata.json")
    (cap / "captions").mkdir(exist_ok=True)
    shutil.copy(FIXTURES / "dQw4fixture.en.vtt", cap / "captions" / "dQw4fixture.en.vtt")


TICKET_ID = "8c1d2e3f4a5b"


def _script(script, root, cap, *argv, check=True, env=None):
    run_env = {**os.environ, **(env or {})}
    cp = subprocess.run(
        ["uv", "run", "-q", str(script), str(root), "--capture-dir", str(cap.relative_to(root)), *argv],
        capture_output=True, text=True, check=False, env=run_env,
    )
    if check:
        assert cp.returncode == 0, cp.stderr
    return cp


def _stub_formatter(tmp_path):
    stub = tmp_path / "fmt.py"
    stub.write_text("print('#### [00:00] stub\\n\\nstubbed words')\n")
    return stub


def _default_ticket(cap, root, stage, **over):
    ticket = {
        "ticket": TICKET_ID, "stage": stage, "slug": "yt-job", "item": ITEM, "target": ITEM,
        "capture_dir": str(cap.relative_to(root)), "dest": None, "hosts": ["www.youtube.com"],
        "known": [], "options": {},
    }
    ticket.update(over)
    return ticket


def _stub_ops(tmp_path, ticket=None):
    """A stand-in for the front door: `pipeline tickets open` (answers
    `ticket`), `pipeline tickets update` (a bare 0, recorded), and the page
    verbs (`page create`/`page edit`) — the one `LLM_WIKI_OPS`/`--ops` this
    script's `open_ticket`, `post_update` and `write_page` all reach through.
    It writes what the real `page create`/`page edit` would, and refuses a
    `create` over a title that is already a page."""
    stub = tmp_path / "ops_stub.py"
    stub.write_text(
        "import json, os, pathlib, sys\n"
        "argv = sys.argv[1:]\n"
        "verb = [a for a in argv if not a.startswith('--')]\n"
        f"TICKET = json.loads({json.dumps(json.dumps(ticket))})\n"
        "if verb[:3] == ['pipeline', 'tickets', 'open']:\n"
        "    print(json.dumps({'ticket': TICKET}))\n"
        "    sys.exit(0)\n"
        "if verb[:3] == ['pipeline', 'tickets', 'update']:\n"
        "    pathlib.Path(os.getcwd(), 'update-calls.jsonl').open('a').write(json.dumps(argv) + '\\n')\n"
        "    sys.exit(0)\n"
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


def _record(root, cap, *argv, check=True, ticket=True, ops=None):
    """The HARVEST arm: the capture record for the bytes already on disk."""
    env, ticket_argv = None, []
    if ticket:
        front_door = ops if ops is not None else _stub_ops(root, ticket=_default_ticket(cap, root, "harvest"))
        env, ticket_argv = {"LLM_WIKI_OPS": front_door}, ["--ticket", TICKET_ID]
    return _script(BUILDER, root, cap, "--record", *ticket_argv, *argv, check=check, env=env)


def _build(root, cap, *argv, dest=DEST, ops=None, check=True, ticket=True):
    """The PROCESS arm: the body, and the page under `dest`."""
    front_door = ops if ops is not None else _stub_ops(root, ticket=_default_ticket(cap, root, "process") if ticket else None)
    ticket_argv = ["--ticket", TICKET_ID] if ticket else []
    return _script(BUILDER, root, cap, "--dest", dest, "--ops", front_door, *ticket_argv, *argv,
                    check=check, env={"LLM_WIKI_OPS": front_door})


def _page_calls(root):
    path = root / "page-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _ticketed(tmp_path):
    cap = tmp_path / "_raw" / "yt-job" / "watch--1a2b3c4d"
    cap.mkdir(parents=True)
    _fill(cap)
    return cap


def test_harvest_leaves_the_bytes_and_a_flat_capture_record(tmp_path):
    """Harvest is bytes. The record names the payload as it arrived, and
    carries no `frontmatter` object and no key a page verb owns — the facts
    reach the page because this unit writes the page."""
    cap = _ticketed(tmp_path)
    stub = _stub_ops(tmp_path, ticket=_default_ticket(cap, tmp_path, "harvest"))
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    _record(tmp_path, cap, ops=stub)
    record = json.loads((cap / "capture.json").read_text())
    assert record == {
        "slug": "yt-job", "item": ITEM, "title": META["title"], "body": "metadata.json",
        "content_type": "application/json", "fetched_at": record["fetched_at"]}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["fetched_at"])
    # `update-calls.jsonl` is the stub front door's OWN recording, not this script's.
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before - {tmp_path / "update-calls.jsonl"}
    assert new == {cap / "capture.json"}, new


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
    """`has_transcript` is the whole of what this script says about it — the
    session's own `tickets update` line (SKILL.md step 4) is what turns a
    false one into `reason=no_captions`; there is no report for this script
    to compose any more."""
    cap = _ticketed(tmp_path)
    shutil.rmtree(cap / "captions")
    out = json.loads(_build(tmp_path, cap).stdout)  # no formatter needed: nothing to format
    assert out["has_transcript"] is False
    assert "## Transcript" not in (cap / "page.md").read_text()


def test_no_metadata_is_refused_by_name(tmp_path):
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").unlink()
    cp = _record(tmp_path, cap, check=False)
    assert cp.returncode != 0 and "metadata.json" in cp.stderr and "Traceback" not in cp.stderr


# --- titles, hostile venue text, and the scripts as `run` starts them ---------


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


def _as_run_does(script, root, *argv, env=None):
    """THE DOCUMENTED WAY: cwd is the wiki root, the wiki is `.`, and the
    capture dir is the ticket's wiki-relative value — no absolute path anywhere."""
    run_env = {**os.environ, **(env or {})}
    return subprocess.run(["uv", "run", "-q", str(script), ".", *argv], cwd=root, capture_output=True, text=True, check=False, env=run_env)


def test_both_scripts_run_from_the_wiki_root_with_the_tickets_relative_capture_dir(tmp_path):
    cap = _ticketed(tmp_path)
    rel = str(cap.relative_to(tmp_path))
    stub = _stub_ops(tmp_path, ticket=_default_ticket(cap, tmp_path, "harvest"))
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", rel, "--record", "--ticket", TICKET_ID, env={"LLM_WIKI_OPS": stub})
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["capture"] == f"{rel}/capture.json"
    stub = _stub_ops(tmp_path, ticket=_default_ticket(cap, tmp_path, "process"))
    cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", rel, "--dest", DEST, "--ticket", TICKET_ID,
                       "--ops", stub, "--format-transcript", str(_stub_formatter(tmp_path)), env={"LLM_WIKI_OPS": stub})
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["page"] == f"{rel}/page.md"
    # `update-calls.jsonl`/`ops_stub.py` are the stub front door's own files.
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before - {tmp_path / "update-calls.jsonl", tmp_path / "ops_stub.py"}
    assert new == {cap / "page.md", cap / "capture.json", cap / "written.json",
                   tmp_path / "fmt.py", tmp_path / "page-calls.jsonl",
                   tmp_path / DEST / f"{META['title']}.md"}, new


@pytest.mark.parametrize("tail", [["--record"], ["--dest", DEST]])
def test_a_capture_dir_that_is_not_wiki_relative_is_refused(tmp_path, tail):
    cap = _ticketed(tmp_path)
    for bad in (str(cap), "_raw/yt-job/../yt-job/watch--1a2b3c4d", "_raw/yt-job/nope"):
        cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", bad, *tail, "--item", ITEM)
        assert cp.returncode != 0 and "Traceback" not in cp.stderr, (bad, cp.stderr)
    assert not (cap / "page.md").exists() and not (tmp_path / "page.md").exists()


def test_the_builder_refuses_a_directory_no_spawner_wrote_a_ticket_into(tmp_path):
    """`.` — the wiki root itself, which is what a capture-dir default of `.`
    used to mean under `run` — has neither `--ticket` nor `--item`: refused,
    nothing written."""
    cap = _ticketed(tmp_path)
    shutil.copy(cap / "metadata.json", tmp_path / "metadata.json")
    cp = _as_run_does(BUILDER, tmp_path, "--capture-dir", ".", "--record")
    assert cp.returncode != 0 and "--ticket" in cp.stderr and "--item" in cp.stderr
    assert not (tmp_path / "page.md").exists() and not (tmp_path / "capture.json").exists()


# Rule 4 — a respawn must never be read as a success it did not have.


def _an_earlier_run(tmp_path):
    cap = _ticketed(tmp_path)
    _record(tmp_path, cap)
    assert (cap / "capture.json").is_file()
    return cap


def test_a_failed_yt_dlp_on_a_respawn_is_not_reported_as_the_earlier_runs_capture(tmp_path):
    """`yt-dlp … > metadata.json` leaves an EMPTY file when yt-dlp fails. P-7:
    every harvest run clears `capture.json` FIRST, so a failed respawn can
    never be read as the run before's success — no ticket-mtime comparison
    needed, because there is nothing stale left to compare against."""
    cap = _an_earlier_run(tmp_path)
    (cap / "metadata.json").write_text("")
    cp = _record(tmp_path, cap, check=False)
    assert cp.returncode != 0 and "Traceback" not in cp.stderr
    assert "metadata.json" in cp.stderr and "yt-dlp failed" in cp.stderr
    assert not (cap / "capture.json").exists(), "capture.json survived a failed build"


@pytest.mark.parametrize("junk", ["[1, 2]", '"text"', "{not json"])
def test_metadata_that_is_not_yt_dlps_object_is_refused_without_a_traceback(tmp_path, junk):
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").write_text(junk)
    cp = _record(tmp_path, cap, check=False)
    assert cp.returncode != 0 and "Traceback" not in cp.stderr and not (cap / "capture.json").exists()


# Rule 1 — the ticket's `embeds` reaches the page.


def test_the_written_pages_land_in_a_file_not_on_a_command_line(tmp_path):
    """A page's filename IS the video's title, and `safe_title` leaves `;`, `$`
    and a backtick in one — a filename may hold them. Typed onto the SKILL's
    `tickets update written_from=` line that would be the venue running a
    command, so the builder leaves the list under a fixed name instead."""
    cap = _ticketed(tmp_path)
    (cap / "metadata.json").write_text(json.dumps({**META, "title": "Pricing;$(touch PWNED) `id`"}))
    _record(tmp_path, cap)
    out = json.loads(_build(tmp_path, cap, "--format-transcript", str(_stub_formatter(tmp_path))).stdout)
    assert json.loads((cap / "written.json").read_text()) == out["written"]
    assert ";" in out["written"][0] and "$(" in out["written"][0], out["written"]


@pytest.mark.parametrize("embeds, iframe", [(True, True), (False, False)])
def test_process_embeds_false_takes_the_iframe_out(builder, embeds, iframe):
    """`process.embeds` is a key on the process ticket. No extractor sees this
    page any more, so the unit is the only thing that can honor it."""
    front = builder.frontmatter_for(META)
    body, _has_desc = builder.build_body(META, front, ITEM, "", embeds=embeds)
    assert ("<iframe" in body) is iframe, body[:200]
    assert "![thumbnail]" in body, "only the embed goes; the thumbnail is a plain image"


@pytest.mark.parametrize("process, iframe", [({"embeds": False}, False), ({"embeds": True}, True), ({}, True), (None, True), ("nope", True)])
def test_the_tickets_embeds_key_reaches_the_page_the_script_writes(tmp_path, process, iframe):
    """The shipped path: the ticket's `process.embeds`, off `tickets open`,
    read by the script itself (`embeds_of`), not a `build_body(embeds=)` a
    test hands over — a wrong key there stays green above. Absent, the
    embed stays: the record's own default."""
    cap = _ticketed(tmp_path)
    over = {"process": process} if process is not None else {}
    stub = _stub_ops(tmp_path, ticket=_default_ticket(cap, tmp_path, "process", **over))
    _build(tmp_path, cap, "--format-transcript", str(_stub_formatter(tmp_path)), ops=stub)
    body = (cap / "page.md").read_text()
    assert ("<iframe" in body) is iframe, body[:200]


def test_front_door_interpreter_resolves_a_project_line_in_either_form(builder, tmp_path):
    """F13: `LLM_WIKI_OPS` naming `uv run --project <dir> llm-wiki-ops` (or the
    `=` form) is never rewritten to a durable single path outside a real
    dispatch — reading `door[-1]`'s own shebang then always misses
    (`llm-wiki-ops` is a bare name, not a file here), silently falling back
    to `sys.executable`, which lacks the formatter's own imports. The
    project's own `.venv` interpreter is what a `uv run --project` line
    actually runs under, in either flag form."""
    venv_python = tmp_path / ".venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")
    venv_python.chmod(0o755)
    for door in (
        ["uv", "run", "--project", str(tmp_path), "llm-wiki-ops"],
        ["uv", "run", f"--project={tmp_path}", "llm-wiki-ops"],
    ):
        assert builder._front_door_interpreter(door) == str(venv_python), door


def test_format_transcript_a_missing_front_door_binary_exits_cleanly(builder, monkeypatch, tmp_path):
    """F13: a missing `uv` (or any front-door interpreter) raised
    `FileNotFoundError` straight out of `subprocess.run`, crashing with a
    traceback instead of the documented exit message."""
    monkeypatch.setenv("LLM_WIKI_OPS", "uv run --project /nowhere llm-wiki-ops")

    def boom(*a, **k):
        raise FileNotFoundError("uv: not found")

    monkeypatch.setattr(builder.subprocess, "run", boom)
    captions = tmp_path / "captions.vtt"
    captions.write_text("WEBVTT\n")
    with pytest.raises(SystemExit) as exc:
        builder.format_transcript(str(captions), None, tmp_path, None)
    assert "uv: not found" in str(exc.value)
