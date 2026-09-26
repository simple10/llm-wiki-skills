"""channel-frameio, the harness tier: the unit installed and enabled through
the REAL CLI, landing pages in the session wiki via a live ticket. Its
helpers and constants are the unit's own tests' —
`skills/channel-frameio/tests/test_frameio.py`, which ships with the unit —
so a case here reads exactly as it did beside them.

The full harvest→process chain needs `pipeline tickets run`/`close` (plugins
PR 2, #2486) to mint the process ticket a captured leaf gets — not on
`main` yet, so the process arm's real-CLI round trip is unverified here and
skips naming it. Rich behavior (title settling, `reference`, video stubs,
`bundle_media`, PDF extraction) is the unit's own tests' — full rigor, and
they actually run — this file's job is proving the front door plumbing
itself, not re-covering what already runs.
"""

from __future__ import annotations

import json

import pytest

from harness import declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-frameio", "test_frameio"))


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


def test_a_live_harvest_ticket_plans_and_captures_through_the_real_cli(ops, env, wiki):
    """The whole point of the rework: a live ticket, `harvest_share.py`
    opening it through `tickets open` and posting `tickets update` — no stub
    — and `close` landing what it posted."""
    _needs_run_verb(ops, env, wiki)
    job = declared_job(ops, env, wiki, UNIT, SHARE, "dest=sources/scrapes/harness-frameio", slug="harness-frameio")
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    (cap / "tree.json").write_text(json.dumps({"leaves": [_leaf(1)]}), encoding="utf-8")

    planned = run(ops, rooted(env, wiki), "run", "ops/skills/channel-frameio/scripts/harvest_share.py", rel,
                  "--ticket", ticket_id, "--plan-only", cwd=wiki)
    assert planned.returncode == 0, planned.stdout + planned.stderr
    summary = json.loads(planned.stdout)
    assert summary["planned"] == 1

    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    (leaf,) = plan["leaves"]
    leaf_dir = wiki / leaf["dir"]
    leaf_dir.mkdir(parents=True, exist_ok=True)
    (leaf_dir / "document.pdf").write_bytes(b"%PDF-1.4 fake")
    (leaf_dir / "capture.json").write_text(json.dumps(
        {"item": leaf["item"], "title": "T", "body": "document.pdf", "content_type": "application/pdf"}), encoding="utf-8")

    reported = run(ops, rooted(env, wiki), "run", "ops/skills/channel-frameio/scripts/harvest_share.py", rel,
                   "--ticket", ticket_id, "--budget-seconds", "-1", cwd=wiki)
    assert reported.returncode == 0, reported.stdout + reported.stderr
    assert json.loads(reported.stdout)["captured"] == 1

    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", None), closed


def test_an_explicit_reference_on_the_job_reaches_the_plan(ops, env, wiki):
    """The operator's `harvest.assets=reference` rides the job record into the
    ticket, and the driver's `--plan-only` leaves the media leaf out of
    `plan.json` — named under `unplanned` — while the document is planned."""
    _needs_run_verb(ops, env, wiki)
    job = declared_job(ops, env, wiki, UNIT, FOLDER, "dest=sources/scrapes/harness-frameio-ref",
                       "harvest.assets=reference", slug="harness-frameio-reference")
    assert job.record["harvest"]["assets"] == "reference"
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))
    video, deck = _leaf(1, name="Keynote.mov"), _leaf(2, name="Deck.pdf")
    (cap / "tree.json").write_text(json.dumps({"leaves": [video, deck]}), encoding="utf-8")

    planned = run(ops, rooted(env, wiki), "run", "ops/skills/channel-frameio/scripts/harvest_share.py", rel,
                  "--ticket", ticket_id, "--plan-only", cwd=wiki)
    assert planned.returncode == 0, planned.stdout + planned.stderr
    summary = json.loads(planned.stdout)
    assert (summary["planned"], summary["skipped"]["reference"]) == (1, 1), summary
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [leaf["item"] for leaf in plan["leaves"]] == [deck["view_url"]]
    assert plan["unplanned"] == [{"item": video["view_url"], "name": "Keynote.mov", "why": "reference"}]
    landed(ops, env, wiki, ticket_id)  # frees the harvest cap slot for every later case in this session
