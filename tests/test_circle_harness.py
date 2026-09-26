"""channel-circle, the harness tier: the unit installed and enabled through
the REAL CLI, landing pages in the session wiki via a live ticket. Its
helpers and constants are the unit's own tests' —
`skills/channel-circle/tests/test_circle.py`, which ships with the unit —
so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys

from pathlib import Path

import pytest

from harness import declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-circle", "test_circle"))


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


def to_markdown(directory: Path) -> str:
    """SKILL.md's process step 1, over one capture's bytes."""
    done = subprocess.run(["uv", "run", "--script", str(TO_MARKDOWN), str(directory / "page.html"),
                           "--out", str(directory / "page.md")], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return (directory / "page.md").read_text(encoding="utf-8")


def paged(ops, env, wiki, capture_dir: Path, dest: str, body: str, verb: str = "create") -> subprocess.CompletedProcess:
    """SKILL.md's process step 3, as the shell line a worker TYPES: the title
    and the url off `capture.json`, each single-quoted as the documented line
    has them, the body on stdin — so every content case is a quoting case."""
    record = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    where = [f"'title={record['title']}'", f"'dest={dest}'"] if verb == "create" else [f"'{dest}/{record['title']}.md'"]
    line = " ".join([shlex.join([*ops, "--json", "page", verb]), *where, f"'resource={record['item']}'", "type=lesson", "extracted=true", "--stdin"])
    return subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), input=body, capture_output=True, text=True, check=False)


def written(ops, env, wiki, capture_dir: Path, dest: str, body: str) -> Path:
    """`create`, and on the host's `already exists` refusal, `edit` — the page."""
    done = paged(ops, env, wiki, capture_dir, dest, body)
    if done.returncode == 2 and "already exists" in done.stdout:
        done = paged(ops, env, wiki, capture_dir, dest, body, verb="edit")
    assert done.returncode == 0, done.stdout + done.stderr
    return wiki / json.loads(done.stdout)["path"]


def test_one_ticket_walks_the_section_and_every_lesson_becomes_a_page(ops, env, wiki):
    """The whole point of the rework: a live ticket, `section_plan.py plan`/
    `record`/`report` posting `tickets update` through the REAL CLI, and the
    process arm's `to_markdown.py` + `page create` writing real pages — no
    `ticket.json`, no `report.json` anywhere on disk."""
    _needs_run_verb(ops, env, wiki)
    # `spawn=self` refuses a ticket whose target host does not resolve to a
    # public address (plugins main, post-#2487). `TARGET`'s host is
    # `example.com` (RFC 2606) — resolvable everywhere with DNS/internet
    # egress, unlike the old `.invalid` host — and `test_circle.py`'s own
    # fixtures and assertions are keyed to the SAME host throughout, so the
    # plan's `harvest.scope=section` match still keeps the right leaves.
    job = declared_job(ops, env, wiki, UNIT, TARGET, slug="harness-circle")
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    for name in ("page.html", "meta.json"):
        (cap / name).write_bytes((FIX / "root" / name).read_bytes())

    # Paths are wiki-relative with the wiki root as cwd — what `llm-wiki-ops run` gives a script.
    planned = run(ops, rooted(env, wiki), "run", "ops/skills/channel-circle/scripts/section_plan.py", "plan", rel,
                  "--ticket", ticket_id, cwd=wiki)
    assert planned.returncode == 0, planned.stdout + planned.stderr
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [leaf["url"] for leaf in plan["leaves"]] == [L1, L2]

    for leaf, fixture in zip(plan["leaves"], ("lesson-1", "lesson-2")):
        assert leaf["dir"].startswith(f"_raw/{job.slug}/") and len(leaf["dir"].split("/")) == 3
        # THE DOCUMENTED WAY: a lesson is `--leaf N`, never a url on a command line.
        directory = bytes_in(wiki / leaf["dir"], fixture)
        if fixture == "lesson-1":  # the caption track the capture resolved in-browser
            (directory / "captions").mkdir(exist_ok=True)
            (directory / "captions" / "en.vtt").write_text(VTT, encoding="utf-8")
        number = str(leaf["order"])
        for _ in range(2):  # a respawned worker records again: nothing may stack
            done = subprocess.run(
                [sys.executable, str(PLAN), "record", str(rel), "--leaf", number], cwd=wiki, capture_output=True, text=True,
            )
            assert done.returncode == 0, done.stderr
        record = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
        assert (record["slug"], record["item"], record["body"], record["content_type"]) == (
            job.slug, leaf["url"], "page.html", "text/html")
        assert "frontmatter" not in record and not set(record) & set(HOST_KEYS)
        assert not (directory / "page.md").exists(), "harvest renders no page"
    assert json.loads((wiki / plan["leaves"][1]["dir"] / "facts.json").read_text(encoding="utf-8")) == {
        "course": "Course One | Example Community", "space": "course-one", "section": "Section One",
        "duration": "12:30", "source_title": "Reading the Room"}

    reported = run(ops, rooted(env, wiki), "run", "ops/skills/channel-circle/scripts/section_plan.py", "report", rel,
                   "--ticket", ticket_id, cwd=wiki)
    assert reported.returncode == 0, reported.stdout + reported.stderr
    update = json.loads(reported.stdout)
    assert update["status"] == "ok"

    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", None), closed

    # `close` mints one process ticket per captured directory; each runs the
    # process step over that leaf. Its own ticket is opened the same way.
    texts = []
    for leaf in plan["leaves"]:
        directory = wiki / leaf["dir"]
        body = to_markdown(directory)  # step 1; step 2 is the agent's, so only its inputs are asserted
        facts = json.loads((directory / "facts.json").read_text(encoding="utf-8"))
        assert facts["course"] == "Course One | Example Community" and facts["section"] == "Section One"
        page = written(ops, env, wiki, directory, job.dest, body)
        assert page.is_relative_to(wiki / job.dest)
        texts.append(page.read_text(encoding="utf-8"))
    first, second = texts
    assert "title: Getting the Frame Right" in first and f"resource: {L1}" in first and "status: draft" in first
    assert "extracted: 'true'" in first
    assert "marmalade-sandwich rule" in first and "> A frame is a promise about what matters." in first
    assert "Topic 1 of 2" in first and "heliotrope question" in second
    assert "https://assets-v2.circle.so/abc123def" in second  # the extensionless Resources link survives
    for text in texts:
        assert len(re.findall(r"^---$", text, flags=re.M)) == 2, "one frontmatter block, the host's own"
        assert "Powered by a community platform" not in text and "logo123" not in text  # chrome stripped

    # A second process run for one lesson EDITS the page the first one wrote.
    directory = wiki / plan["leaves"][0]["dir"]
    assert written(ops, env, wiki, directory, job.dest, to_markdown(directory)).name == "Getting the Frame Right.md"
    assert sorted(page.name for page in (wiki / job.dest).glob("*.md")) == [
        "Getting the Frame Right.md", "Reading the Room.md"]


def test_a_title_with_an_apostrophe_survives_the_documented_shell_line(ops, env, wiki):
    """The process step is a shell line a worker TYPES, single-quoting the title
    off `capture.json`. `safe_title` maps BOTH quote forms to U+2019, so no
    title it can produce breaks out of those quotes and loses its page."""
    dest = "sources/courses/port-circle-apostrophe"
    for venue in ("Don't Panic", 'He said "no" twice'):
        title = mod.safe_title(venue)
        assert "'" not in title, title
        line = (
            "printf '%s' 'body' | "
            + shlex.join([*ops, "--json", "page", "create"])
            + f" 'title={title}' 'dest={dest}' 'resource=https://example.invalid/x'"
            + f" 'extracted=true' 'type=lesson' --stdin"
        )
        done = subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), capture_output=True, text=True, check=False)
        assert done.returncode == 0, line + "\n" + done.stdout + done.stderr
        assert (wiki / json.loads(done.stdout)["path"]).is_file()
