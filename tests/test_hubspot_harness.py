"""channel-hubspot-video, the harness tier: the unit installed and enabled
through the REAL CLI, landing pages in the session wiki via a live ticket.
Its helpers and constants are the unit's own tests' —
`skills/channel-hubspot-video/tests/test_hubspot.py`, which ships with the
unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess

from pathlib import Path

import pytest

from harness import declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-hubspot-video", "test_hubspot"))


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


def _needs_retry_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "retry", "--help").returncode != 0:
        pytest.skip("`pipeline tickets retry` is plugins PR 2 (#2486)")


def test_a_cadence_change_and_a_retry_refusal_are_real(ops, env, wiki):
    """`every` is not an identity key, so a `once` job can be given a period
    while a section fills, and back — and `tickets retry` refuses a ticket
    that never ran, naming it (G3 side note: the refusal text is re-read
    from the new verb)."""
    job = declared_job(ops, env, wiki, UNIT, "https://www.example-hubspot.invalid/continue", slug="port-channel-hubspot-continue")
    assert job.record["every"] == "once"
    for cadence in ("1h", "once"):
        done = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "edit", job.slug, f"every={cadence}")
        assert done.returncode == 0, done.stdout + done.stderr
        assert run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "show", job.slug).data["job"]["every"] == cadence
    _needs_retry_verb(ops, env, wiki)
    refused = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "retry", "0123456789ab")
    assert refused.returncode != 0 and "no ticket" in refused.stdout + refused.stderr  # never minted, never active


def test_a_title_with_an_apostrophe_survives_the_documented_shell_line(ops, env, wiki):
    """The process step is a shell line a worker TYPES, single-quoting the title
    off `capture.json`. `safe_title` maps BOTH quote forms to U+2019, so no
    title it can produce breaks out of those quotes and loses its page."""
    dest = "sources/courses/port-hubspot-apostrophe"
    for venue in ("Don't Panic", 'He said "no" twice'):
        title = leaves.safe_title(venue)
        assert "'" not in title, title
        line = (
            "printf '%s' 'body' | "
            + shlex.join([*ops, "--json", "page", "create"])
            + f" 'title={title}' 'dest={dest}' 'resource=https://example.invalid/x'"
            + f" 'extracted=true' 'type=video' --stdin"
        )
        done = subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), capture_output=True, text=True, check=False)
        assert done.returncode == 0, line + "\n" + done.stdout + done.stderr
        assert (wiki / json.loads(done.stdout)["path"]).is_file()


def test_a_harvested_lesson_becomes_the_staged_page(ops, env, wiki):
    """The whole point of the rework: a live ticket, `leaves.py plan`/
    `record`/`report` posting `tickets update` through the REAL CLI, and the
    process arm's `to_markdown.py` + `page create` writing a real page —
    no `ticket.json`, no `report.json` anywhere on disk."""
    _needs_run_verb(ops, env, wiki)
    job = declared_job(ops, env, wiki, UNIT, SECTION, slug="harness-hubspot")
    shutil.rmtree(wiki / "_raw" / job.slug, ignore_errors=True)
    shutil.rmtree(wiki / job.dest, ignore_errors=True)
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    rel = str(cap.relative_to(wiki))

    urls = cap / "urls.json"
    urls.write_text(json.dumps([{"url": LESSON, "lastmod": "2026-07-15"}]), encoding="utf-8")
    (cap / "sites.json").write_text((FIX / "sites.json").read_text(encoding="utf-8"), encoding="utf-8")
    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-hubspot-video/scripts/leaves.py", "plan", rel,
            "--urls", f"{rel}/urls.json", "--sites", f"{rel}/sites.json", "--ticket", ticket_id, cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    (leaf_row,) = plan["leaves"]
    leaf_dir = wiki / leaf_row["dir"]
    _fill(leaf_dir)

    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-hubspot-video/scripts/leaves.py", "record", rel,
            "--leaf", "0", cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((leaf_dir / "capture.json").read_text(encoding="utf-8"))["title"] == "Pricing the offer"

    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-hubspot-video/scripts/leaves.py", "report", rel,
            "--ticket", ticket_id, cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "ok"

    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", None), closed

    # The process arm: no network, no credential — `to_markdown.py` off the
    # site's own selectors, then the REAL `page create` through the front door.
    md = run(ops, rooted(env, wiki), "run", "ops/skills/channel-hubspot-video/scripts/to_markdown.py",
             f"{leaf_row['dir']}/page.html", "--selector", "main#main-content",
             "--drop-selector", "main#main-content h1", "--title-selector", "main#main-content h2",
             "--base-url", LESSON, cwd=wiki)
    assert md.returncode == 0, md.stdout + md.stderr
    body = (leaf_dir / "page.md").read_text(encoding="utf-8")
    # `page create` reads the body off stdin; `harness.run` has no stdin
    # plumbing, so post it directly through the front door instead.
    created = subprocess.run(
        [*ops, "--json", "page", "create", "title=Pricing the offer", f"dest={job.dest}", f"resource={LESSON}",
         "extracted=true", "type=video", "venue=hubspot-cms", "--stdin"],
        input=body, capture_output=True, text=True, env=rooted(env, wiki), cwd=wiki, check=False,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    page = wiki / json.loads(created.stdout)["path"]
    assert page.is_file() and page.read_text(encoding="utf-8").lstrip().startswith("# Pricing the offer")

    # `--written-from` names a file INSIDE the capture dir, per A-2.
    (leaf_dir / "written.json").write_text(json.dumps([str(page.relative_to(wiki))]), encoding="utf-8")
    r = run(ops, rooted(env, wiki), "run", "ops/skills/channel-hubspot-video/scripts/leaves.py", "report",
            leaf_row["dir"], "--ticket", ticket_id, "--written-from", "written.json", cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "ok"
