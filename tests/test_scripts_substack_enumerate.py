"""`channel-substack`'s archive enumerator — it plans the leaves one ticket captures.

Ported 2026-09-19 with the unit. The script used to emit ONE `discovered` row
for a host script to fan out into child jobs; that host half is gone, so it
now applies the job's rules itself and plans the leaf capture directories.
What this file pinned about the WALK still holds and is kept: the free/paid
split, the `min_date` stop, the bound on one run, and that a lower floor is
not a resume. What it pinned about the ROW is replaced, case by case, below —
each replaced case says what it used to assert and why that is gone.
The filter/naming/report logic and the end-to-end run live in
`test_port_substack.py`.

Loaded by path: unit scripts live under `skills/<unit>/scripts/` and are
deliberately self-contained (stdlib only, no sibling imports), so there is
no package to import them from.
"""
import ast
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills/channel-substack/scripts"
          / "enumerate_archive.py")


def _module():
    spec = importlib.util.spec_from_file_location("enumerate_archive", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _post(n, audience="everyone", date="2026-08-01"):
    return {"slug": f"post-{n}", "audience": audience,
            "post_date": f"{date}T12:00:00.000Z",
            "canonical_url": f"https://ex.substack.com/p/post-{n}"}


def _urls(out):
    return [leaf["item"] for leaf in out["leaves"]]


def _run(monkeypatch, capsys, pages, *argv, ticket=None, tmp_path=None):
    """One walk over `pages`. With `ticket`, the inputs come from a
    `ticket.json` in `tmp_path`, the way a worker's do; without, from flags."""
    mod = _module()
    served = list(pages)
    base = ["ex.substack.com", "--slug", "w1"]
    if ticket is not None:
        (tmp_path / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
        base = ["--capture-dir", str(tmp_path)]

    def fake_fetch(domain, offset, limit):
        return served.pop(0) if served else []

    monkeypatch.setattr(mod, "fetch_page", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", *base, *argv])
    mod.main()
    return json.loads(capsys.readouterr().out)


def test_it_plans_leaves_and_emits_no_discovered_row(monkeypatch, capsys):
    """WAS `test_it_emits_one_report_row_and_queues_nothing`, which pinned the
    `{"parent", "watch_id", "urls"}` row. Nothing applies such a row any more
    — `discovered[]` does nothing for pages — so emitting one would be a
    harvest that silently captures nothing. The plan is what replaced it."""
    out = _run(monkeypatch, capsys, [[_post(1), _post(2)]])
    assert "discovered" not in out
    assert _urls(out) == ["https://ex.substack.com/p/post-1",
                          "https://ex.substack.com/p/post-2"]
    assert [leaf["dir"].rsplit("--", 1)[0] for leaf in out["leaves"]] == [
        "_raw/w1/p-post-1", "_raw/w1/p-post-2"]
    assert out["summary"]["planned"] == 2


def test_the_script_shells_nothing():
    """A confined worker has no queue and no ledger, so this must not
    reach either.

    Over the AST, not the text. The substring version of this test split the
    source on triple quotes and searched element 2: a 223-byte window
    holding the imports and one `def` line, which could not see `main()` at
    all. Its other assertion looked for the exact string `import
    subprocess`, which `from subprocess import run as _shell` walks past.
    Both were measured GREEN under mutation.
    """
    tree = ast.parse(SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    # An allow-list, not a deny-list: a deny-list is why the old one passed.
    # Grown by the port, each for a reason: `pathlib` reads `ticket.json` and
    # writes `leaves.json`, `hashlib` + `re` compose the leaf name, `fnmatch`
    # matches `harvest.exclude_urls`. Still nothing that can shell or import.
    assert imported == {"argparse", "fnmatch", "hashlib", "json", "pathlib", "re", "sys", "time", "urllib"}, imported

    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__", "open"}


def test_free_access_emits_only_everyone(monkeypatch, capsys):
    out = _run(monkeypatch, capsys,
               [[_post(1), _post(2, audience="only_paid"), _post(3)]])
    assert out["summary"]["skipped_paywalled"] == 1
    assert len(out["leaves"]) == 2


def test_min_date_stops_the_walk(monkeypatch, capsys):
    out = _run(monkeypatch, capsys,
               [[_post(1, date="2026-08-01"), _post(2, date="2025-01-01")]],
               "--min-date", "2026-01-01")
    assert out["summary"]["stopped_at_min_date"] is True
    assert len(out["leaves"]) == 1
    assert out["summary"]["truncated"] is False


def test_one_run_never_plans_past_its_cap(monkeypatch, capsys):
    """WAS `test_the_emission_never_exceeds_the_hosts_ceiling`: the cap used to
    restate the host's `max_discovered_urls`, past which a report was refused
    whole. That ceiling is gone with the row. The bound stays for a different
    reason — a slice is killed at thirty minutes and a killed slice leaves no
    report — so `--max-urls` became `--max-leaves`."""
    pages = [[_post(i, date="2026-08-01") for i in range(p * 50, p * 50 + 50)]
             for p in range(10)]
    out = _run(monkeypatch, capsys, pages, "--max-leaves", "120")

    # Exact, not `<=`: halving the cap plans 60 and a `<=` assertion still
    # passes, pinning "never over" rather than "stops at the cap".
    assert len(out["leaves"]) == 120
    assert out["summary"]["truncated"] is True
    # WAS also `resume_max_date == "2026-08-01"`. Gone: see the resume test.
    assert "resume_max_date" not in out["summary"]


def test_an_untruncated_walk_is_not_flagged(monkeypatch, capsys):
    """WAS `..._names_no_resume_ceiling`; the flag half still holds."""
    out = _run(monkeypatch, capsys, [[_post(1)]], "--max-leaves", "100")
    assert out["summary"]["truncated"] is False


def _dated_archive(n, start="2026-08-31"):
    """`n` posts, newest-first, one per day — the real archive's shape."""
    base = date.fromisoformat(start)
    return [{"slug": f"p{i}", "audience": "everyone",
             "post_date": f"{base - timedelta(days=i)}T12:00:00.000Z",
             "canonical_url": f"https://ex.substack.com/p/p{i}"}
            for i in range(n)]


def _ticket(known=()):
    return {"v": 1, "ticket": "0123456789ab", "unit": "channel-substack", "slug": "w1",
            "target": "https://ex.substack.com/archive", "capture_dir": "_raw/w1/archive--00000000",
            "harvest": {"scope": "domain", "access": "free", "exclude_urls": []},
            "min_date": None, "known": [{"resource": u, "harvested_at": "2026-09-01T00:00:00Z"} for u in known]}


def test_known_is_what_advances_a_bounded_walk(monkeypatch, capsys, tmp_path):
    """WAS `test_the_resume_ceiling_actually_advances_the_walk`, which resumed
    with `--max-date <resume_max_date>` and leaned on the host's seen ledger
    to drop the re-emitted boundary post. There is no seen ledger behind a
    report now; the ticket's `known[]` is the job's corpus, so skipping it IS
    the resume — and it costs no slot, so nothing is re-planned at all."""
    pages = [_dated_archive(300)[i:i + 50] for i in range(0, 300, 50)]

    first = _run(monkeypatch, capsys, pages, "--max-leaves", "50", ticket=_ticket(), tmp_path=tmp_path)
    assert first["summary"]["truncated"] is True

    second = _run(monkeypatch, capsys, pages, "--max-leaves", "50",
                  ticket=_ticket(known=_urls(first)), tmp_path=tmp_path)

    assert second["summary"]["skipped_known"] == 50
    assert not set(_urls(first)) & set(_urls(second))
    assert _urls(second)[0] == "https://ex.substack.com/p/p50"
    assert len(second["leaves"]) == 50


def test_a_same_day_flood_cannot_stall_the_walk(monkeypatch, capsys, tmp_path):
    """WAS two cases, `test_a_walk_that_cannot_advance_says_so` and
    `test_an_advancing_walk_is_not_flagged_as_stalled`. A stall was the
    inclusive DATE ceiling failing to move when the cap's worth of posts
    shared one date. `known[]` resumes by URL, so 60 posts on one day at cap
    50 finish on pass two — the `stalled` flag is gone because the failure is."""
    same_day = [{"slug": f"p{i}", "audience": "everyone",
                 "post_date": "2026-08-31T12:00:00.000Z",
                 "canonical_url": f"https://ex.substack.com/p/p{i}"}
                for i in range(60)]
    pages = [same_day[:50], same_day[50:]]
    first = _run(monkeypatch, capsys, pages, "--max-leaves", "50", ticket=_ticket(), tmp_path=tmp_path)
    second = _run(monkeypatch, capsys, pages, "--max-leaves", "50",
                  ticket=_ticket(known=_urls(first)), tmp_path=tmp_path)

    assert "stalled" not in first["summary"]
    assert len(second["leaves"]) == 10
    assert second["summary"]["truncated"] is False


def test_min_date_is_not_a_resume(monkeypatch, capsys):
    """Kept: a lower floor on a truncated walk plans exactly the same posts.
    The walk always restarts at `offset=0`, under either contract."""
    pages = [_dated_archive(300)[i:i + 50] for i in range(0, 300, 50)]
    first = _run(monkeypatch, capsys, pages, "--max-leaves", "50")
    again = _run(monkeypatch, capsys, pages, "--max-leaves", "50",
                 "--min-date", "2020-01-01")
    assert _urls(again) == _urls(first)


def test_a_run_with_no_job_behind_it_is_refused(monkeypatch, tmp_path):
    """WAS `test_parent_is_required`: `--parent` named the dispatched job a
    `discovered` row had to match, and is gone with the row. What a run cannot
    do without is the job's SLUG — it names every leaf directory — so that is
    the refusal at the call now, from a flag or from `ticket.json`."""
    mod = _module()
    # Stubbed even though the refusal should come first: without this, a
    # regression that made the slug optional would send this test to the real
    # Substack API — green or red by network rather than by the property.
    monkeypatch.setattr(mod, "fetch_page", lambda *a: [])
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "ex.substack.com",
                                      "--capture-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        mod.main()
