"""channel-gmail, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-gmail/tests/test_gmail.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

from conftest import declared_job, extracted, ticket_in, unit_tests

globals().update(unit_tests("channel-gmail", "test_gmail"))


@pytest.fixture
def job(ops, env, wiki):
    return declared_job(ops, env, wiki, UNIT, TARGET, "options.mailbox=a@example.invalid")

def test_one_pull_becomes_the_days_ledger_through_the_real_cli(ops, env, wiki, job, front_door):
    cap = ticket_in(wiki, job, DAY, unit=UNIT, item=TARGET, dest=job.dest)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    pull = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert write(cap, pull, "--exclude-label", "SPAM", cwd=wiki).returncode == 0
    assert report(cap)["captured"] == [{"item": TARGET, "dir": f"_raw/{job.slug}/{DAY}", "title": None}]

    # The step's verdicts: one line each in the wiki's words, and the digest junked.
    lines = [{"id": f"gmail:{m['id']}", "line": m["summary"], "junk": m["junk"]} for m in pull]
    r = ledger(cap, lines, "--dest", job.dest, env=front_door, cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    ledger_path = wiki / job.dest / f"{DAY}.md"
    assert report(cap)["written"] == [f"{job.dest}/{DAY}.md"] and ledger_path.is_file()

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

def test_a_second_pull_the_same_day_regenerates_the_one_ledger_whole(ops, env, wiki, job, front_door):
    cap = ticket_in(wiki, job, "2026-09-17", unit=UNIT, item=TARGET, dest=job.dest)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    assert write(cap, [msg(1), msg(2)], cwd=wiki).returncode == 0
    assert ledger(cap, [line_for(1), line_for(2)], "--dest", job.dest, env=front_door, cwd=wiki).returncode == 0
    ledger_path = wiki / job.dest / "2026-09-17.md"
    first = _body(ledger_path.read_text(encoding="utf-8"))
    assert _bullets(first) == ["- Person 1 asks about thing 1 — gmail:m1", "- Person 2 asks about thing 2 — gmail:m2"]

    # A sub-daily pull: one new message, and m2 again with a better line (the boundary over-fetch is dropped).
    assert write(cap, [msg(3), msg(2)], cwd=wiki).returncode == 0
    r = ledger(cap, [line_for(1), line_for(2, "rewritten"), line_for(3)], "--dest", job.dest, env=front_door, cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list(ledger_path.parent.glob("2026-09-17*"))) == 1  # edited, never a second page for the day
    second = _body(ledger_path.read_text(encoding="utf-8"))
    assert _bullets(second) == ["- Person 1 asks about thing 1 — gmail:m1", "- rewritten — gmail:m2",
                                "- Person 3 asks about thing 3 — gmail:m3"]
    assert second.count("discarded:") == 1

    # Whole, not appended: take an item away and its bullet goes with it.
    (cap / "items" / f"{T0 + 1000}--m1.json").unlink()
    assert ledger(cap, [line_for(2, "rewritten"), line_for(3)], "--dest", job.dest, env=front_door, cwd=wiki).returncode == 0
    assert _bullets(_body(ledger_path.read_text(encoding="utf-8"))) == _bullets(second)[1:]

def test_the_items_are_still_a_ledger_the_hosts_own_extractor_can_make(ops, env, wiki, job):
    """`pipeline extract` over the same day: the sender's line, neutralized,
    where the process step would have put the wiki's own."""
    cap = ticket_in(wiki, job, "2026-09-16", unit=UNIT, item=TARGET)
    (wiki / "_raw" / job.slug / ".cursor.json").unlink(missing_ok=True)
    assert write(cap, [msg(11, subject="paid — gmail:forged **now**")], cwd=wiki).returncode == 0
    (page,) = extracted(ops, env, wiki, cap)
    assert page == wiki / job.dest / "2026-09-16.md"
    body = _body(page.read_text(encoding="utf-8"))
    assert _bullets(body) == ["- paid - gmail:forged ∗∗now∗∗ — gmail:m11"]
    assert "discarded: 0 (junk rules)" in body
