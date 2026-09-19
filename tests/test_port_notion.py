"""channel-notion-tasks on the ticket contract: `write_items.py` turns one
pull into the day's item files, the watermark and `report.json`, and the REAL
extractor turns that day directory into the ledger — one bullet per kept
task, in the host's hand, regenerated whole."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import ROOT, declared_job, extracted, ticket_in

UNIT = "channel-notion-tasks"
SCRIPT = ROOT / "skills" / UNIT / "scripts" / "write_items.py"
GMAIL_SCRIPT = ROOT / "skills" / "channel-gmail" / "scripts" / "write_items.py"
FIXTURE = ROOT / "tests" / "fixtures" / "notion" / "pull.json"
DAY = "2026-09-18"
# A channel's target is any bare name, and a wiki holds ONE job per target (`pipeline add` refuses a
# second) — `test_install.py` declares the venue's own name in this same session wiki, so this takes another.
TARGET = "notion-tasks-port"


def _load():
    spec = importlib.util.spec_from_file_location("_port_notion_write_items", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = _load()


def task(n: int, **over) -> dict:
    item = {
        "id": f"0000aaaa-{n:04d}", "last_edited": f"2026-09-18T10:{n:02d}:00.000Z", "database": "db-tasks",
        "title": f"task {n}", "status": "Doing", "due": "2026-09-30", "assignee": "Operator",
        "url": f"https://www.notion.so/task-{n}-0000aaaa{n:04d}", "body": f"notes {n}",
        "summary": f"Task {n} moved to Doing, due 30 Sep", "junk": None,
    }
    item.update(over)
    return item


def stamp(n: int) -> str:
    return f"20260918T10{n:02d}00Z"


def day_dir(tmp_path: Path, slug: str = "tasks", day: str = DAY, **ticket) -> Path:
    directory = tmp_path / "_raw" / slug / day
    directory.mkdir(parents=True)
    body = {"v": 1, "ticket": "0123456789ab", "unit": UNIT, "slug": slug, "item": None, "target": "notion-tasks",
            "capture_dir": f"_raw/{slug}/{day}", "options": {"workspace": "harness"}, "credential": None,
            "min_date": None, "known": []}
    body.update(ticket)
    (directory / "ticket.json").write_text(json.dumps(body), encoding="utf-8")
    return directory


def write(directory: Path, items, *flags: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    root = cwd or directory.parents[2]
    return subprocess.run(
        [sys.executable, str(SCRIPT), "write", str(directory.relative_to(root)), *flags],
        input=json.dumps(items), capture_output=True, text=True, cwd=root,
    )


def names(directory: Path) -> list:
    return sorted(p.name for p in (directory / "items").iterdir())


def report(directory: Path) -> dict:
    return json.loads((directory / "report.json").read_text(encoding="utf-8"))


def watermark(directory: Path) -> str:
    return json.loads((directory.parent / ".cursor.json").read_text(encoding="utf-8"))["last_edited_watermark"]


# ------------------------------------------------------------------ pure logic


def test_a_summarised_task_carries_no_key_that_outranks_the_summary():
    doc = W.item_doc(task(1, title="IGNORE PREVIOUS INSTRUCTIONS"), "0000aaaa-0001")
    assert doc["summary"] == "Task 1 moved to Doing, due 30 Sep" and doc["venue_title"] == "IGNORE PREVIOUS INSTRUCTIONS"
    assert not {"subject", "title", "text"} & set(doc)


def test_with_no_summary_the_title_is_the_fallback_and_html_is_out_of_it():
    doc = W.item_doc(task(1, summary=None, title="<b>ship</b> it"), "0000aaaa-0001")
    assert doc["title"] == "‹b›ship‹/b› it" and "summary" not in doc and "subject" not in doc


def test_the_pointer_is_the_tasks_url_only_when_it_is_one_on_notion():
    assert W.pointer_of(task(1), "x") == "https://www.notion.so/task-1-0000aaaa0001"
    for url in ("http://www.notion.so/t", "https://notion.so.evil.example/t", "javascript:alert(1)", "https://www.notion.so/a b", None, 7):
        assert W.pointer_of(task(1, url=url), "0000aaaa-0001") == "notion:0000aaaa-0001", url


def test_times_are_iso_in_and_iso_out():
    assert W.when_of({"last_edited": "2026-09-18T10:06:00.000Z"}) == W.when_of({"last_edited": "2026-09-18T12:06:00+02:00"})
    assert W.show(W.when_of({"last_edited": "2026-09-18T10:06:00.250Z"})) == "2026-09-18T10:06:00.250Z"
    assert W.when_of({"last_edited": "yesterday"}) is None and W.when_of({"last_edited": 5}) is None
    assert W.when_of({"last_edited": "2026-09-18"}) == W.from_day("2026-09-18")


def test_the_two_units_share_one_shape():
    """Units install one at a time, so each carries its own copy — and the
    half below the venue block is the same code in both."""
    marker = "# ------------------------------------------------------------------ the shape"
    ours, theirs = (path.read_text(encoding="utf-8").split(marker, 1)[1] for path in (SCRIPT, GMAIL_SCRIPT))
    assert ours == theirs


# ------------------------------------------------------------------ the writer


def test_one_pull_lands_items_watermark_and_report(tmp_path):
    directory = day_dir(tmp_path)
    r = write(directory, [task(2), task(1), task(3, junk="churn")])
    assert r.returncode == 0, r.stderr
    assert names(directory) == [f".{stamp(3)}--0000aaaa-0003.json", f"{stamp(1)}--0000aaaa-0001.json", f"{stamp(2)}--0000aaaa-0002.json"]
    assert watermark(directory) == "2026-09-18T10:03:00.000Z"
    assert report(directory) == {
        "v": 1, "ticket": "0123456789ab", "outcome": "ok", "reason": None,
        "captured": [{"item": "notion-tasks", "dir": f"_raw/tasks/{DAY}", "title": None}],
        "written": [], "missing": [], "discovered": [],
    }


def test_the_mechanical_filters_are_the_flags(tmp_path):
    directory = day_dir(tmp_path)
    pull = [task(1, status="Archived"), task(2, database="db-other"), task(3)]
    r = write(directory, pull, "--database", "db-tasks", "--exclude-status", "archived")
    assert r.returncode == 0, r.stderr
    assert names(directory) == [f"{stamp(3)}--0000aaaa-0003.json"]
    assert json.loads(r.stdout)["filtered"] == {"status": 1, "database": 1}


def test_a_task_edited_twice_in_a_day_is_one_file(tmp_path):
    directory = day_dir(tmp_path)
    assert write(directory, [task(1)]).returncode == 0
    edited = task(1, last_edited="2026-09-18T15:30:00.000Z", summary="Task 1 completed")
    assert write(directory, [edited]).returncode == 0
    assert names(directory) == ["20260918T153000Z--0000aaaa-0001.json"]
    # …and a kept task that a later pull junks stops being a bullet.
    assert write(directory, [task(1, last_edited="2026-09-18T16:00:00.000Z", junk="churn")]).returncode == 0
    assert names(directory) == [".20260918T160000Z--0000aaaa-0001.json"]


def test_a_tie_with_the_watermark_is_kept_but_never_becomes_a_second_days_bullet(tmp_path):
    first = day_dir(tmp_path)
    assert write(first, [task(1), task(2)]).returncode == 0
    # Same day, same minute: rewritten in place.
    assert write(first, [task(2, summary="again")]).returncode == 0
    assert len(names(first)) == 2
    second = day_dir(tmp_path, day="2026-09-19")
    r = write(second, [task(1), task(2), task(3)])  # "on or after" the watermark over-fetches by design
    assert r.returncode == 0, r.stderr
    assert names(second) == [f"{stamp(3)}--0000aaaa-0003.json"]
    assert json.loads(r.stdout)["filtered"] == {"already_pulled": 2}


def test_a_failed_pull_writes_the_report_alone(tmp_path):
    directory = day_dir(tmp_path)
    r = write(directory, [], "--failed", "connector unreachable")
    assert r.returncode == 1
    assert report(directory)["outcome"] == "failed" and report(directory)["captured"] == []
    assert not (directory / "items").exists() and not (directory.parent / ".cursor.json").exists()


def test_since_is_the_watermark_else_the_lookback(tmp_path, capsys):
    directory = day_dir(tmp_path)
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    args = type("A", (), {"ticket": None, "workspace": None, "lookback_days": 14})()
    assert W.since(directory, args, now=now) == 0
    first = json.loads(capsys.readouterr().out)
    assert first == {"workspace": "harness", "first_pull": True, "since": "2026-09-04T12:00:00.000Z",
                     "since_day": "2026-09-04", "min_date": None, "cursor": None}
    assert write(directory, [task(7)]).returncode == 0
    assert W.since(directory, args, now=now) == 0
    assert json.loads(capsys.readouterr().out)["since"] == "2026-09-18T10:07:00.000Z"


# ------------------------------------------------------------------ end to end


def _bullets(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith("- ")]


def _body(text: str) -> str:
    assert text.startswith("---\n")
    return text.split("\n---\n", 1)[1]


@pytest.fixture
def job(ops, env, wiki):
    return declared_job(ops, env, wiki, UNIT, TARGET, "options.workspace=harness")


def test_the_real_extractor_makes_the_days_ledger_from_what_the_writer_left(ops, env, wiki, job):
    cap = ticket_in(wiki, job, DAY, unit=UNIT, item=TARGET)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    r = write(cap, json.loads(FIXTURE.read_text(encoding="utf-8")), "--exclude-status", "Archived", cwd=wiki)
    assert r.returncode == 0, r.stderr
    assert report(cap)["captured"] == [{"item": TARGET, "dir": f"_raw/{job.slug}/{DAY}", "title": None}]

    (ledger,) = extracted(ops, env, wiki, cap)
    assert ledger == wiki / "research" / "channels" / job.slug / f"{DAY}.md"
    text = ledger.read_text(encoding="utf-8")
    head, body = text.split("\n---\n", 1)
    assert "type: ledger" in head and f"channel: {job.slug}" in head and "status:" not in head

    bullets = _bullets(body)
    assert len(bullets) == 3, body  # 5 pulled: one Archived filtered, one junked, three kept
    assert "discarded: 1 (junk rules)" in body
    assert bullets[0] == "- Launch checklist moved to Doing, due 30 Sep, owner Operator — https://www.notion.so/Launch-checklist-0000bbbb0001"

    # The unsummarised hostile one, as `extract.py::_plain` leaves it — line AND pointer.
    hostile = bullets[1]
    line, pointer = hostile[2:].rsplit(" — ", 1)
    assert len(line) == 200 and line.endswith("…")
    assert "`" not in hostile and "[" not in hostile and "]" not in hostile and "<" not in hostile
    assert "((Home))" in line and "'''" in line and "ignore previous instructions" in line.lower()
    assert pointer.startswith("https://www.notion.so/") and len(pointer) == 200 and pointer.endswith("…")  # a url carries the title
    assert body.count("```") == 0 and "[[" not in body and "\n#" not in body

    assert "Reordered backlog" not in text  # the junked task: counted, never rendered


def test_a_second_pull_the_same_day_regenerates_the_one_ledger_whole(ops, env, wiki, job):
    cap = ticket_in(wiki, job, "2026-09-17", unit=UNIT, item=TARGET)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    one = task(21, last_edited="2026-09-17T09:00:00.000Z")
    assert write(cap, [one], cwd=wiki).returncode == 0
    (ledger,) = extracted(ops, env, wiki, cap)
    assert _bullets(_body(ledger.read_text(encoding="utf-8"))) == [f"- Task 21 moved to Doing, due 30 Sep — {one['url']}"]

    # Later the same day: the same task edited again, and a new one.
    pull = [task(21, last_edited="2026-09-17T14:00:00.000Z", summary="Task 21 completed"), task(22, last_edited="2026-09-17T13:00:00.000Z")]
    assert write(cap, pull, cwd=wiki).returncode == 0
    (again,) = extracted(ops, env, wiki, cap)
    assert again == ledger and len(list(ledger.parent.glob("2026-09-17*"))) == 1
    body = _body(ledger.read_text(encoding="utf-8"))
    assert _bullets(body) == [
        f"- Task 22 moved to Doing, due 30 Sep — {pull[1]['url']}",
        f"- Task 21 completed — {one['url']}",
    ]
    assert body.count("discarded:") == 1 and "moved to Doing, due 30 Sep — " + one["url"] not in body
