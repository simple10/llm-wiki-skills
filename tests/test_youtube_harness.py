"""channel-youtube, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-youtube/tests/test_youtube.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import os
import pytest
import re

from pathlib import Path

from harness import advanced, declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-youtube", "test_youtube"))


def _formatter():
    """The plugin's real formatter, or a skip with the true reason."""
    plugin = os.environ.get("LLM_WIKI_OPS_PLUGIN")
    if not plugin:
        pytest.skip("set LLM_WIKI_OPS_PLUGIN to the ops plugin's root — format_transcript.py is host code, not this package's")
    rel = re.search(r'^FORMATTER = "([^"]+)"$', BUILDER.read_text(encoding="utf-8"), re.M).group(1)
    path = Path(plugin) / rel
    old = Path(plugin) / "skills/process/scripts/format_transcript.py"  # G3, plugins PR 4
    if not path.is_file() and old.is_file():
        path = old
    assert path.is_file(), f"{rel} is not under LLM_WIKI_OPS_PLUGIN={plugin} — did the plugin move it?"
    return path


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


def test_a_harvested_video_becomes_the_staged_page(ops, env, wiki):
    """The whole point of the rework. A live ticket over what yt-dlp leaves
    (fixtures; no network) → harvest's capture record → this unit's own
    process step, through the REAL `page create` → one staged page under
    the job's `dest`, carrying the venue-specific body and the venue's own
    facts. `open_ticket`/`post_update` reach the REAL CLI here — no stub —
    since `run` exports `LLM_WIKI_OPS` for the worker it starts."""
    formatter = _formatter()
    _needs_run_verb(ops, env, wiki)
    job = declared_job(ops, env, wiki, UNIT, JOB_TARGET)
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    _fill(cap)

    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-youtube/scripts/youtube_note.py", ".",
            "--capture-dir", str(cap.relative_to(wiki)), "--record", "--ticket", ticket_id, cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    record = json.loads((cap / "capture.json").read_text())
    assert record["slug"] == job.slug and record["item"] == ITEM and "frontmatter" not in record

    # The script only builds the capture; `SKILL.md`'s own `## Stages` lines
    # are the worker's `tickets update` — the session's, not the script's.
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "update", ticket_id, "stage=harvest", "status=ok")
    assert r.returncode == 0, r.stdout + r.stderr

    # The harvest ticket's own `close` (A-10) mints the process ticket the
    # unit's process arm reads through `--ticket` — the two steps share the
    # capture dir, never a ticket id.
    process_id, process_cap = advanced(ops, env, wiki, ticket_id)
    assert process_cap == cap

    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-youtube/scripts/youtube_note.py", ".",
            "--capture-dir", str(cap.relative_to(wiki)), "--dest", job.dest, "--ticket", process_id,
            "--format-transcript", str(formatter), cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["has_transcript"] is True and out["chapters"] == 2

    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "update", process_id, "stage=process",
            "status=ok", "written_from=written.json")
    assert r.returncode == 0, r.stdout + r.stderr

    closed = landed(ops, env, wiki, process_id)
    assert closed.get("status") in ("ok", None), closed

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


# Rule 1 — the page's FILE is named from `capture.json`'s title, and the host
# refuses a title its filename rule cannot hold (`page/note.py`: ILLEGAL, and
# what `filename_for` refuses).
@pytest.mark.parametrize("leaf, title, safe", [
    ("hostile--5e2e0002", 'Lesson 3: Pricing? A/B "testing"', "Lesson 3 - Pricing A-B ’testing’"),
    ("hostile--5e2e0003", ".hidden: what is <X> | Y?\n---\n# Forged", "hidden - what is (X) - Y --- # Forged"),
    ("hostile--5e2e0004", "漢" * 100, None),
])
def test_a_title_no_filename_can_hold_still_lands_as_a_page(ops, env, wiki, tmp_path, leaf, title, safe):
    """Through the REAL `page create`, which names the page's file from the
    title and refuses `:` `?` `/` `"` or a leading dot outright."""
    _needs_run_verb(ops, env, wiki)
    # A wiki holds ONE job per target url — reusing JOB_TARGET across these
    # cases would collide with the other test's job (and each other), so
    # every hostile case gets its own target, not just its own slug.
    item = f"https://www.youtube.com/watch?v={leaf[-8:]}xyz"
    job = declared_job(ops, env, wiki, UNIT, item, slug=f"harness-yt-{leaf}")
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    _fill(cap)
    (cap / "metadata.json").write_text(json.dumps({**META, "title": title}))

    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-youtube/scripts/youtube_note.py", ".",
            "--capture-dir", str(cap.relative_to(wiki)), "--record", "--ticket", ticket_id, cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    record = json.loads((cap / "capture.json").read_text())
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "update", ticket_id, "stage=harvest", "status=ok")
    assert r.returncode == 0, r.stdout + r.stderr
    process_id, _cap = advanced(ops, env, wiki, ticket_id)

    # FIRST: the refusal this pins is `page create`'s own, not an assertion of ours.
    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-youtube/scripts/youtube_note.py", ".",
            "--capture-dir", str(cap.relative_to(wiki)), "--dest", job.dest, "--ticket", process_id,
            "--format-transcript", str(_stub_formatter(tmp_path)), cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    if safe:
        assert record["title"] == safe
    page = wiki / out["written"][0]
    assert page.is_file() and page.name == f"{record['title']}.md"
    text = page.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    folded = " ".join(title.split())
    assert body.lstrip().startswith(f"# {folded.replace('<', '&lt;')}\n"), "the TRUE title is the H1"
    assert "source_title: " in front and folded[:20] in front
    assert len(re.findall(r"^---$", text, re.M)) == 2 and "\n# Forged" not in text

    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "update", process_id, "stage=process",
            "status=ok", "written_from=written.json")
    assert r.returncode == 0, r.stdout + r.stderr
    landed(ops, env, wiki, process_id)  # frees the process cap slot for the next case
