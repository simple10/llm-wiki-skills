"""`channel-substack`'s archive enumerator, since #587 moved it off intake.

The script queues nothing now: it emits one `discovered` row for the worker
to copy into its report, and the HOST applies it. What is worth pinning is
the part that is not obvious — it must never hand back a payload larger than
the host will accept, because an oversized report is refused WHOLE and every
job in the slice goes back to `pending/`.

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


def _run(monkeypatch, capsys, pages, *argv):
    mod = _module()
    served = list(pages)

    def fake_fetch(domain, offset, limit):
        return served.pop(0) if served else []

    monkeypatch.setattr(mod, "fetch_page", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "ex.substack.com",
                                      "--slug", "w1", "--parent", "j1",
                                      *argv])
    mod.main()
    return json.loads(capsys.readouterr().out)


def test_it_emits_one_report_row_and_queues_nothing(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [[_post(1), _post(2)]])
    assert out["discovered"] == {
        "parent": "j1", "watch_id": "w1",
        "urls": ["https://ex.substack.com/p/post-1",
                 "https://ex.substack.com/p/post-2"]}
    assert out["summary"]["emitted"] == 2
    # The three intake-side fields are gone, not renamed: the host owns them.
    for gone in ("queued", "intake_skipped", "intake_reasons"):
        assert gone not in out["summary"]


def test_the_script_shells_nothing():
    """The property #587 is about — a confined worker has no queue and no
    ledger, so this must not reach either.

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
    assert imported == {"argparse", "json", "sys", "time", "urllib"}, imported

    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__", "open"}


def test_free_access_emits_only_everyone(monkeypatch, capsys):
    out = _run(monkeypatch, capsys,
               [[_post(1), _post(2, audience="only_paid"), _post(3)]])
    assert out["summary"]["skipped_paywalled"] == 1
    assert len(out["discovered"]["urls"]) == 2


def test_min_date_stops_the_walk(monkeypatch, capsys):
    out = _run(monkeypatch, capsys,
               [[_post(1, date="2026-08-01"), _post(2, date="2025-01-01")]],
               "--min-date", "2026-01-01")
    assert out["summary"]["stopped_at_min_date"] is True
    assert len(out["discovered"]["urls"]) == 1
    assert out["summary"]["truncated"] is False


def test_the_emission_never_exceeds_the_hosts_ceiling(monkeypatch, capsys):
    """An oversized report is refused WHOLE and sends every job in the slice
    back to `pending/`, so walking a 5000-post archive and handing it over
    would cost the slice its work, not just the tail."""
    pages = [[_post(i, date="2026-08-01") for i in range(50)]
             for _ in range(10)]
    out = _run(monkeypatch, capsys, pages, "--max-urls", "120")

    # Exact, not `<=`: halving the cap emits 60 and a `<=` assertion still
    # passes, pinning "never over" rather than "stops at the ceiling".
    # 120 is exactly reachable here — pages of 50, so 50/50/20.
    assert len(out["discovered"]["urls"]) == 120
    assert out["summary"]["truncated"] is True
    # The resume floor is the oldest post actually kept — the API is
    # newest-first, so everything unwalked is older than this.
    assert out["summary"]["resume_max_date"] == "2026-08-01"


def test_an_untruncated_walk_names_no_resume_ceiling(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, [[_post(1)]], "--max-urls", "100")
    assert out["summary"]["truncated"] is False
    assert out["summary"]["resume_max_date"] is None


def _dated_archive(n, start="2026-08-31"):
    """`n` posts, newest-first, one per day — the real archive's shape."""
    base = date.fromisoformat(start)
    return [{"slug": f"p{i}", "audience": "everyone",
             "post_date": f"{base - timedelta(days=i)}T12:00:00.000Z",
             "canonical_url": f"https://ex.substack.com/p/p{i}"}
            for i in range(n)]


def test_the_resume_ceiling_actually_advances_the_walk(monkeypatch, capsys):
    """The point of `resume_max_date`, and what the first version of this got
    wrong: `--min-date` is a FLOOR on a walk that always starts at
    `offset=0`, so lowering it re-emits the same first `--max-urls` posts
    forever. `--max-date` is what moves the start."""
    pages = [_dated_archive(300)[i:i + 50] for i in range(0, 300, 50)]

    first = _run(monkeypatch, capsys, pages, "--max-urls", "50")
    ceiling = first["summary"]["resume_max_date"]
    assert first["summary"]["truncated"] is True

    second = _run(monkeypatch, capsys, pages, "--max-urls", "50",
                  "--max-date", ceiling)

    assert second["discovered"]["urls"] != first["discovered"]["urls"]
    assert second["summary"]["skipped_newer"] == 49
    # One boundary post is re-emitted on purpose: `--max-date` is inclusive
    # because several posts can share a date, and re-emitting one costs a
    # slot intake's seen ledger then dedupes, where excluding it would drop
    # a post silently.
    assert len(set(first["discovered"]["urls"])
               & set(second["discovered"]["urls"])) == 1
    assert second["summary"]["resume_max_date"] < ceiling


def test_a_walk_that_cannot_advance_says_so(monkeypatch, capsys):
    """The inclusive ceiling has one failure mode: when `--max-urls` or more
    posts share the boundary date, the whole emission is one date, the
    ceiling cannot move below itself, and the next pass returns the same
    set. That is the exact shape of the `--min-date` bug this PR fixed, so
    it must not be silent — measured, 60 posts/day at cap 50 stalls on
    pass 2."""
    same_day = [{"slug": f"p{i}", "audience": "everyone",
                 "post_date": "2026-08-31T12:00:00.000Z",
                 "canonical_url": f"https://ex.substack.com/p/p{i}"}
                for i in range(60)]
    out = _run(monkeypatch, capsys, [same_day[:50], same_day[50:]],
               "--max-urls", "50")

    assert out["summary"]["truncated"] is True
    assert out["summary"]["stalled"] is True
    assert out["summary"]["resume_max_date"] == "2026-08-31"


def test_an_advancing_walk_is_not_flagged_as_stalled(monkeypatch, capsys):
    """The control: a truncated walk spanning more than one date advances
    normally and must not wear the flag."""
    pages = [_dated_archive(300)[i:i + 50] for i in range(0, 300, 50)]
    out = _run(monkeypatch, capsys, pages, "--max-urls", "50")
    assert out["summary"]["truncated"] is True
    assert out["summary"]["stalled"] is False


def test_min_date_is_not_a_resume(monkeypatch, capsys):
    """Guards the instruction that was wrong, so nobody restores it: a lower
    floor on a truncated walk emits exactly the same URLs."""
    pages = [_dated_archive(300)[i:i + 50] for i in range(0, 300, 50)]
    first = _run(monkeypatch, capsys, pages, "--max-urls", "50")
    again = _run(monkeypatch, capsys, pages, "--max-urls", "50",
                 "--min-date", "2020-01-01")
    assert again["discovered"]["urls"] == first["discovered"]["urls"]


def test_parent_is_required(monkeypatch):
    """The host refuses a discovered row whose parent it did not dispatch,
    so a row without one can never apply — the refusal belongs at the call,
    not three stages later in `apply`'s output."""
    mod = _module()
    # Stubbed even though the refusal should come first: without this, a
    # regression that made `--parent` optional again would send this test to
    # the real Substack API — slow, and green or red by network rather than
    # by the property. Measured: it did exactly that.
    monkeypatch.setattr(mod, "fetch_page", lambda *a: [])
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "ex.substack.com",
                                      "--slug", "w1"])
    with pytest.raises(SystemExit):
        mod.main()
