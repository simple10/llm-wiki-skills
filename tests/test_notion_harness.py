"""channel-notion-tasks, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-notion-tasks/tests/test_notion.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import os
import pytest
import shlex
import stat

from pathlib import Path

from harness import declared_job, ticket_in, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-notion-tasks", "test_notion"))


def _bullets(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith("- ")]


@pytest.fixture
def real_door(tmp_path, ops, env, wiki):
    """The front door this unit writes a page through, bound to the harness
    wiki — the real CLI, rooted the way a caller outside the wiki roots one."""
    bin_dir = tmp_path / "real-door"
    bin_dir.mkdir()
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f'#!/bin/sh\nexport LLM_WIKI_ROOT={shlex.quote(str(Path(wiki).resolve()))}\n'
        f'exec {shlex.join(ops)} "$@"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}", "LLM_WIKI_OPS": str(stub)}


@pytest.fixture
def job(ops, env, wiki):
    return declared_job(ops, env, wiki, UNIT, TARGET, "options.workspace=harness")


def test_the_two_steps_make_the_days_ledger_out_of_what_the_pull_left(ops, env, wiki, job, real_door):
    cap = ticket_in(wiki, job, DAY, unit=UNIT, item=TARGET, dest=job.dest)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    pull = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert write(cap, pull, "--exclude-status", "Archived", cwd=wiki).returncode == 0
    assert report(cap)["captured"] == [{"item": TARGET, "dir": f"_raw/{job.slug}/{DAY}", "title": None}]

    # The process step's own words, one row per item the day holds. The fixture's
    # hostile task carries none, so its bullet falls back to the task's own title.
    (cap / "lines.json").write_text(
        json.dumps([{"id": one["id"], "line": one["summary"], "junk": one["junk"]} for one in pull]), encoding="utf-8"
    )
    r = run("ledger", cap, "--dest", job.dest, cwd=wiki, env=real_door)
    assert r.returncode == 0, r.stderr + r.stdout
    ledger = wiki / job.dest / f"{DAY}.md"
    assert json.loads(r.stdout)["written"] == [f"{job.dest}/{DAY}.md"] and ledger.is_file()
    assert report(cap)["written"] == [f"{job.dest}/{DAY}.md"] and report(cap)["captured"] == []

    head, body = ledger.read_text(encoding="utf-8").split("\n---\n", 1)
    assert "type: ledger" in head and f"channel: {job.slug}" in head and "items: '3'" in head
    # No status: a ledger is outside the lifecycle, and a draft a day would put
    # every day of every channel in curate's list.
    assert "status:" not in head

    bullets = _bullets(body)
    assert len(bullets) == 3, body  # 5 pulled: one Archived filtered at harvest, one junked at process, three kept
    assert "discarded: 1 (junk rules)" in body
    assert bullets[0] == "- Launch checklist moved to Doing, due 30 Sep, owner Operator — https://www.notion.so/0a1b2c3d00004000800000000000b001"

    hostile = bullets[1]
    line, pointer = hostile[2:].rsplit(" — ", 1)
    assert len(line) == 200 and line.endswith("…")
    assert "`" not in hostile and "[" not in hostile and "]" not in hostile and "<" not in hostile
    assert "((Home))" in line and "'''" in line and "ignore previous instructions" in line.lower()
    assert pointer == "notion:0000bbbb-0002"  # from the id: the venue's url carried the title, and lost its id at the cap
    assert hostile.count(" — ") == 1 and "*" not in line and "|" not in line and "://" not in line and "www." not in line
    assert body.count("```") == 0 and "[[" not in body and "\n#" not in body

    assert "Reordered backlog" not in ledger.read_text(encoding="utf-8")  # the junked task: counted, never rendered


def test_a_second_pull_the_same_day_regenerates_the_one_ledger_whole(ops, env, wiki, job, real_door):
    day = "2026-09-17"
    cap = ticket_in(wiki, job, day, unit=UNIT, item=TARGET, dest=job.dest)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    assert write(cap, [task(21, last_edited=f"{day}T09:00:00.000Z")], cwd=wiki).returncode == 0
    (cap / "lines.json").write_text(json.dumps(lines(("0000aaaa-0021", "Task 21 moved to Doing, due 30 Sep"))), encoding="utf-8")
    first = run("ledger", cap, "--dest", job.dest, cwd=wiki, env=real_door)
    assert first.returncode == 0, first.stdout + first.stderr
    ledger = wiki / job.dest / f"{day}.md"
    assert _bullets(ledger.read_text(encoding="utf-8")) == ["- Task 21 moved to Doing, due 30 Sep — notion:0000aaaa-0021"]

    # Later the same day: the same task edited again, and a new one.
    pull = [task(21, last_edited=f"{day}T14:00:00.000Z"), task(22, last_edited=f"{day}T13:00:00.000Z")]
    assert write(cap, pull, cwd=wiki).returncode == 0
    (cap / "lines.json").write_text(
        json.dumps(lines(("0000aaaa-0021", "Task 21 completed"), ("0000aaaa-0022", "Task 22 moved to Doing, due 30 Sep"))), encoding="utf-8"
    )
    again = run("ledger", cap, "--dest", job.dest, cwd=wiki, env=real_door)
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(list(ledger.parent.glob(f"{day}*"))) == 1
    body = ledger.read_text(encoding="utf-8").split("\n---\n", 1)[1]
    assert _bullets(body) == [
        "- Task 22 moved to Doing, due 30 Sep — notion:0000aaaa-0022",
        "- Task 21 completed — notion:0000aaaa-0021",
    ]
    assert body.count("discarded:") == 1 and "moved to Doing, due 30 Sep — notion:0000aaaa-0021" not in body
    assert "status:" not in ledger.read_text(encoding="utf-8").split("\n---\n", 1)[0]  # the edit path leaves it unset too
