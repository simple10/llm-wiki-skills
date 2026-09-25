"""web-page, the harness tier: the unit installed and enabled through the REAL
CLI, a live ticket run unjailed exactly as `spawn=self` prints it (G2), and
its capture landing through the plugin's real `extract`.

Its helpers and constants are the unit's own tests' —
`skills/web-page/tests/test_fetch.py`, which ships with the unit — so a case
here reads exactly as it did beside them. `tests/test_port_smoke.py` folds
in here (the youtube-shaped `page.md` with a `frontmatter` key the extractor
ignores is this file's `test_a_unit_rendered_page_md_becomes_a_staged_page`
fixture body now).
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import threading

import pytest

from harness import declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("web-page", "test_fetch"))


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


@pytest.fixture
def page_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("web-page-http")
    (directory / "page.html").write_bytes(PAGE)
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(directory), **k)  # noqa: E731
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/page.html"
    server.shutdown()
    server.server_close()


def test_a_harvested_url_lands_as_a_report_through_the_run_line(ops, env, wiki, page_server):
    """`live_ticket` starts the ticket exactly as `spawn=self` would print it
    for a `script` stage (G2): `llm-wiki-ops run
    ops/skills/web-page/scripts/fetch.py ticket=<id>`, unjailed here."""
    _needs_run_verb(ops, env, wiki)
    job = declared_job(ops, env, wiki, "web-page", page_server, slug="port-web")
    ticket_id, capture_dir = live_ticket(ops, env, wiki, job)
    r = run(
        ops, rooted(env, wiki), "run", "ops/skills/web-page/scripts/fetch.py", f"ticket={ticket_id}",
        cwd=wiki,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # `report.<id>.json` lands in the CAPTURE directory (the host's file).
    report = json.loads((capture_dir / f"report.{ticket_id}.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok" and report["stage"] == "harvest"


def test_a_harvested_capture_becomes_a_staged_page_through_the_real_pass(ops, env, wiki, page_server):
    """The whole point of the rework: what this unit's harvest leaves is what
    the process ticket the pass mints reads through the plugin's real
    `extract`. A `frontmatter` key on `capture.json` — this unit never
    writes one, but `capture.TEXT_FIELDS` names only five keys and the
    extractor must ignore any other — is the one fact `test_port_smoke.py`
    proved and this case folds in, so it keeps a test."""
    _needs_run_verb(ops, env, wiki)
    if run(ops, rooted(env, wiki), "pipeline", "pass", "--help").returncode != 0:
        pytest.skip("`pipeline pass` is plugins PR 2 (#2486)")
    job = declared_job(ops, env, wiki, "web-page", page_server, slug="port-web-pass")
    ticket_id, capture_dir = live_ticket(ops, env, wiki, job)
    r = run(ops, rooted(env, wiki), "run", "ops/skills/web-page/scripts/fetch.py", f"ticket={ticket_id}", cwd=wiki)
    assert r.returncode == 0, r.stdout + r.stderr
    capture = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    capture["frontmatter"] = {"type": "should-be-ignored"}
    (capture_dir / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", None), closed
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "pass", "wait=30")
    assert r.returncode == 0, r.stdout + r.stderr
    pages = sorted((wiki / job.dest).glob("*.md")) if (wiki / job.dest).is_dir() else []
    assert pages, f"no page landed under {job.dest}"
    assert "Hello from the venue" in pages[0].read_text(encoding="utf-8")


def test_a_host_outside_the_allowlist_is_reported_denied_under_a_jail(ops, env, wiki):
    """`test_harvest_page_live.py`'s one row, on the ticketed path: a
    dispatched `script` stage runs jailed under the seeded `harvest`
    sandbox plus the ticket's own host (A-6) — never under `spawn=self`,
    which moves the ticket to this session and runs it UNJAILED, so
    proving the jail needs the real pass dispatch, not a hand-built nono
    invocation guessing at PR 2's own composition. Skips without `nono`,
    and skips until `pipeline pass` (PR 2) exists to do that dispatching."""
    if shutil.which("nono") is None:
        pytest.skip("no nono on PATH")
    if run(ops, rooted(env, wiki), "pipeline", "pass", "--help").returncode != 0:
        pytest.skip("`pipeline pass` — the jailed `script`-stage dispatch (A-6) — is plugins PR 2 (#2486)")
    plugin = os.environ.get("LLM_WIKI_OPS_PLUGIN")
    if not plugin:
        pytest.skip("set LLM_WIKI_OPS_PLUGIN to the ops plugin's root — the harvest sandbox profile is served from it")
    job = declared_job(ops, env, wiki, "web-page", "https://example.invalid/denied-by-the-allowlist", slug="port-web-denied")
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "claim", job.slug)
    assert r.returncode == 0, r.stdout + r.stderr
    ticket_id = r.data["claimed"][0]["tickets"][0]["id"]
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "pass", "wait=30")
    assert r.returncode == 0, r.stdout + r.stderr
    capture_rel = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "show", ticket_id).data["tickets"][0]["capture_dir"]
    report = json.loads((wiki / capture_rel / f"report.{ticket_id}.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert any(m.get("why") == "denied" for m in report.get("missing", [])), report
