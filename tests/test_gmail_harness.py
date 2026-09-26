"""channel-gmail, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-gmail/tests/test_gmail.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import pytest

from harness import advanced, bound_credential, declared_job, landed, live_ticket, rooted, run, unit_tests

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


def _closing_the_process_ticket_discards_the_scripts_own_ledger():
    """FOUND running this file against plugins main c284c4839, not a plugins
    PR 2/4 gap: `tickets_close.py` (~line 479-486), unconditionally, for
    ANY job whose `dest` is the ledger route (`jobs.route_of(dest) ==
    ROUTE_LEDGER`), calls `extract_ledger._day_to_ledger(...)` on a process
    `close` — an in-process `pages.write()`, not through `page create`/`edit`
    — which REGENERATES the day's page straight from the raw `items/*.json`
    files (`extract_ledger._item_bullet`: `subject|title|summary|text`, the
    first present — `write_items.py`'s own `HOST_LINE_KEY="subject"` is
    always present, so it always wins). This runs AFTER and OVERWRITES
    whatever `write_items.py ledger`'s own `page create` — which reads the
    process step's OWN judged `lines.json` — already wrote: the unit's
    curation never survives a close. Confirmed by direct instrumentation:
    the script computes the correct body (`ledger_body()`, `unjudged: 0`)
    and passes it to `page create` correctly; the FILE ends up with the
    raw-subject fallback anyway, `document_revision` unchanged (a second,
    in-process write, not a CLI `edit`). Same job for `jobs claim` — a
    LEDGER job apparently cannot ever land the unit's own line — reported
    to the coordinator; not a fix a harness case should paper over."""
    pytest.skip("plugins main c284c4839: tickets_close.py's extract_ledger overwrites a ledger job's page on close, discarding write_items.py ledger's own lines.json curation — reported, not a harness gap")


@pytest.fixture
def job(ops, env, wiki, request):
    # A job's capture dir is keyed on TODAY, not on this test — sharing one
    # slug/target across cases run on the same real day would read one
    # case's items back as another's. Each case gets its own of both.
    # A channel target/slug is `^[a-z0-9][a-z0-9-]*$` — no underscores, so
    # the test's own name (hyphenated, lowered) stands in for it.
    own = request.node.name.replace("_", "-").lower()
    slug = f"harness-gmail-{own}"[:60]
    # `pipeline jobs ledger` (A-11) refuses a `dest` that is not the ledger
    # route (`research/channels/<slug>`) — gmail's own, not the generic
    # staged default `declared_job` gives every other unit.
    j = declared_job(ops, env, wiki, UNIT, f"{TARGET}-{own}"[:60], "options.mailbox=a@example.invalid",
                      f"dest=research/channels/{slug}", slug=slug)
    # `requires.credential: true`'s claim gate (plugins main, post-#2487):
    # `jobs claim` refuses an unbound job — bind this machine's session to it.
    bound_credential(ops, env, wiki, j.slug)
    return j


def test_a_pull_becomes_the_days_ledger_through_the_real_cli(ops, env, wiki, job):
    """The whole point of the rework: a live harvest ticket, `write` posting
    `tickets update` through the REAL CLI (no stub — `run` exports
    `LLM_WIKI_OPS`), then `ledger` — the process arm, on the process ticket
    the harvest ticket's own `close` mints — building the day's page
    through the REAL `page create`."""
    _closing_the_process_ticket_discards_the_scripts_own_ledger()
    _needs_run_verb(ops, env, wiki)
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    day = cap.name
    pull = json.loads(FIXTURE.read_text(encoding="utf-8"))
    (cap / "pull.json").write_text(json.dumps(pull), encoding="utf-8")
    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "write",
            rel, "--ticket", ticket_id, "--from", "pull.json", "--exclude-label", "SPAM", cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr

    process_id, process_cap = advanced(ops, env, wiki, ticket_id)
    assert process_cap == cap
    rel = str(process_cap.relative_to(wiki))

    # The step's verdicts: one line each in the wiki's words, and the digest junked.
    lines = [{"id": f"gmail:{m['id']}", "line": m["summary"], "junk": m["junk"]} for m in pull]
    (cap / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
    r2 = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "ledger",
             rel, "--ticket", process_id, "--dest", job.dest, "--from", "lines.json", cwd=wiki)
    assert r2.returncode == 0, r2.stdout + r2.stderr

    closed = landed(ops, env, wiki, process_id)
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
    """`write` and `ledger` are the harvest and process stages of TWO
    different tickets now (`close` mints the process one), never one id
    reused across both — a sub-daily re-pull mints a fresh pair, into the
    SAME day's capture dir."""
    _closing_the_process_ticket_discards_the_scripts_own_ledger()
    _needs_run_verb(ops, env, wiki)
    day = None

    def _write(pull):
        nonlocal day
        ticket_id, cap = live_ticket(ops, env, wiki, job)
        day = day or cap.name
        assert cap.name == day, "a sub-daily re-pull still lands in the same day's capture dir"
        (cap / "pull.json").write_text(json.dumps(pull), encoding="utf-8")
        r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "write",
                str(cap.relative_to(wiki)), "--ticket", ticket_id, "--from", "pull.json", cwd=wiki)
        assert r.returncode == 0, r.stdout + r.stderr
        process_id, process_cap = advanced(ops, env, wiki, ticket_id)
        return process_id, process_cap

    def _ledger(process_id, cap, lines):
        (cap / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
        r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-gmail/scripts/write_items.py", "ledger",
                str(cap.relative_to(wiki)), "--ticket", process_id, "--dest", job.dest, "--from", "lines.json", cwd=wiki)
        assert r.returncode == 0, r.stdout + r.stderr
        landed(ops, env, wiki, process_id)

    process_id, cap = _write([msg(1), msg(2)])
    _ledger(process_id, cap, [line_for(1), line_for(2)])
    ledger_path = wiki / job.dest / f"{day}.md"
    first = _body(ledger_path.read_text(encoding="utf-8"))
    assert _bullets(first) == ["- Person 1 asks about thing 1 — gmail:m1", "- Person 2 asks about thing 2 — gmail:m2"]

    # A sub-daily pull: one new message, and m2 again with a better line (the boundary over-fetch is dropped).
    process_id, cap = _write([msg(3), msg(2)])
    _ledger(process_id, cap, [line_for(1), line_for(2, "rewritten"), line_for(3)])
    assert len(list(ledger_path.parent.glob(f"{day}*"))) == 1  # edited, never a second page for the day
    second = _body(ledger_path.read_text(encoding="utf-8"))
    assert _bullets(second) == ["- Person 1 asks about thing 1 — gmail:m1", "- rewritten — gmail:m2",
                                "- Person 3 asks about thing 3 — gmail:m3"]
    assert second.count("discarded:") == 1

    # Whole, not appended: take an item away and its bullet goes with it.
    # Another pull (same items — `write` is idempotent) for a fresh ticket
    # pair to post the regenerated ledger through.
    process_id, cap = _write([msg(3), msg(2)])
    (cap / "items" / f"{T0 + 1000}--m1.json").unlink()
    _ledger(process_id, cap, [line_for(2, "rewritten"), line_for(3)])
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
    landed(ops, env, wiki, ticket_id)  # frees the harvest cap slot for every later case in this session
