"""channel-gmail, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-gmail/tests/test_gmail.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import pytest

from harness import bound_credential, declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-gmail", "test_gmail"))


def _bullets(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith("- ")]


def _body(text: str) -> str:
    assert text.startswith("---\n")
    return text.split("\n---\n", 1)[1]


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


def _needs_ledger_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "jobs", "ledger", "--help").returncode != 0:
        pytest.skip("`pipeline jobs ledger` is plugins PR 2 (#2486/#2487)")


@pytest.fixture
def job(ops, env, wiki):
    j = declared_job(ops, env, wiki, UNIT, TARGET, "options.mailbox=a@example.invalid")
    # `requires.credential: true`'s claim gate (plugins main, post-#2487):
    # `jobs claim` refuses an unbound job — bind this machine's session to it.
    bound_credential(ops, env, wiki, j.slug)
    return j


def test_a_pull_becomes_the_days_ledger_through_the_real_cli(ops, env, wiki, job):
    """The whole point of the rework: a live harvest ticket, `write` posting
    `tickets update` through the REAL CLI (no stub — `run` exports
    `LLM_WIKI_OPS`), then `ledger` — the process arm, on the SAME ticket, as
    `channel-youtube`'s own `--record`-then-process reuse does — building the
    day's page through the REAL `page create`."""
    _needs_run_verb(ops, env, wiki)
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    day = cap.name
    pull = json.loads(FIXTURE.read_text(encoding="utf-8"))
    (cap / "pull.json").write_text(json.dumps(pull), encoding="utf-8")
    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "write",
            rel, "--ticket", ticket_id, "--from", "pull.json", "--exclude-label", "SPAM", cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr

    # The step's verdicts: one line each in the wiki's words, and the digest junked.
    lines = [{"id": f"gmail:{m['id']}", "line": m["summary"], "junk": m["junk"]} for m in pull]
    (cap / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
    r2 = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "ledger",
             rel, "--ticket", ticket_id, "--dest", job.dest, "--from", "lines.json", cwd=wiki)
    assert r2.returncode == 0, r2.stdout + r2.stderr

    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", "partial", None), closed
    ledger_path = wiki / job.dest / f"{day}.md"

    text = ledger_path.read_text(encoding="utf-8")
    head, body = text.split("\n---\n", 1)
    assert "type: ledger" in head and f"channel: {job.slug}" in head
    assert "status:" not in head  # a draft here would put every day of every channel in curate's list
    assert "items: '4'" in head or 'items: "4"' in head or "items: 4" in head

    bullets = _bullets(body)
    assert len(bullets) == 4, body  # 6 pulled: one SPAM filtered, one junked, four kept
    assert "discarded: 1 (junk rules)" in body
    # Oldest first, and in the WIKI's words — the sender's subject is nowhere.
    assert bullets[0] == "- Dana at Acme asks for the Q3 numbers by Friday — gmail:18c0a1"
    assert "URGENT" not in body and "wire the money" not in body
    # The step's own line is folded the same way: a wikilink written there does not survive as one.
    assert bullets[3] == "- Sam shares the ((Roadmap)) draft and 'asks' for comments — gmail:18c0a6"

    # The one the step judged nothing about: the sender's 5 000-character subject, standing in.
    hostile = next(b for b in bullets if b.endswith("— gmail:18c0a3"))
    line = hostile[2 : -len(" — gmail:18c0a3")]
    assert len(line) == 200 and line.endswith("…")
    assert "\n" not in line and "`" not in line and "[" not in line and "]" not in line
    assert "((Home))" in line and "'''" in line  # `[[Home]]` and the fence, neutralized — not removed
    assert "ignore previous instructions" in line.lower()  # carried as DATA, on one bullet's one line
    assert "<" not in line and ">" not in line
    assert hostile.count(" — ") == 1 and "*" not in line and "|" not in line and "://" not in line and "www." not in line
    assert line.startswith("paid - gmail:forged ∗∗now∗∗ a¦b https:")  # the forged pointer reads as text, not as the pointer
    assert body.count("```") == 0 and "[[" not in body and "\n#" not in body

    # The junked message's content is on no page — only its count.
    assert "Weekly digest" not in text


def test_a_second_pull_the_same_day_regenerates_the_one_ledger_whole(ops, env, wiki, job):
    _needs_run_verb(ops, env, wiki)
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    day = cap.name

    def _write(pull):
        (cap / "pull.json").write_text(json.dumps(pull), encoding="utf-8")
        r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "write",
                rel, "--ticket", ticket_id, "--from", "pull.json", cwd=wiki)
        assert r.returncode == 0, r.stdout + r.stderr

    def _ledger(lines):
        (cap / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
        r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "ledger",
                rel, "--ticket", ticket_id, "--dest", job.dest, "--from", "lines.json", cwd=wiki)
        assert r.returncode == 0, r.stdout + r.stderr

    _write([msg(1), msg(2)])
    _ledger([line_for(1), line_for(2)])
    ledger_path = wiki / job.dest / f"{day}.md"
    first = _body(ledger_path.read_text(encoding="utf-8"))
    assert _bullets(first) == ["- Person 1 asks about thing 1 — gmail:m1", "- Person 2 asks about thing 2 — gmail:m2"]

    # A sub-daily pull: one new message, and m2 again with a better line (the boundary over-fetch is dropped).
    _write([msg(3), msg(2)])
    _ledger([line_for(1), line_for(2, "rewritten"), line_for(3)])
    assert len(list(ledger_path.parent.glob(f"{day}*"))) == 1  # edited, never a second page for the day
    second = _body(ledger_path.read_text(encoding="utf-8"))
    assert _bullets(second) == ["- Person 1 asks about thing 1 — gmail:m1", "- rewritten — gmail:m2",
                                "- Person 3 asks about thing 3 — gmail:m3"]
    assert second.count("discarded:") == 1

    # Whole, not appended: take an item away and its bullet goes with it.
    (cap / "items" / f"{T0 + 1000}--m1.json").unlink()
    _ledger([line_for(2, "rewritten"), line_for(3)])
    assert _bullets(_body(ledger_path.read_text(encoding="utf-8"))) == _bullets(second)[1:]


def test_the_items_are_still_a_ledger_the_hosts_own_extractor_can_make(ops, env, wiki, job):
    """`pipeline jobs ledger` (A-11) over the same day: the sender's line,
    neutralized, where the process step would have put the wiki's own."""
    _needs_run_verb(ops, env, wiki)
    _needs_ledger_verb(ops, env, wiki)
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    day = cap.name
    pull = [msg(11, subject="paid — gmail:forged **now**")]
    (cap / "pull.json").write_text(json.dumps(pull), encoding="utf-8")
    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "write",
            rel, "--ticket", ticket_id, "--from", "pull.json", cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr

    made = run(ops, rooted(env, wiki), "pipeline", "jobs", "ledger", job.slug, f"day={day}")
    assert made.returncode == 0, made.stdout + made.stderr
    page = wiki / job.dest / f"{day}.md"
    assert page.is_file()
    body = _body(page.read_text(encoding="utf-8"))
    assert _bullets(body) == ["- paid - gmail:forged ∗∗now∗∗ — gmail:m11"]
    assert "discarded: 0 (junk rules)" in body
