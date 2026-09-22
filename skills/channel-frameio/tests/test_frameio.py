"""`channel-frameio` on the ticket contract: two steps, one ticket each.

HARVEST is bytes. Nothing fans a share's leaves out into further tickets, so
the unit does it: `harvest_share.py` plans the leaves from the ticket's own
filters, captures each into its own `_raw/<slug>/<leaf>--<hash8>/`, and writes
the one `report.json`. No page is rendered there.

PROCESS is the unit's own. `apply` mints one process ticket per captured leaf
and `frameio_doc_note.py` turns that leaf's bytes into the page under `dest`,
through the real `page create`.

The pure half — the plan, the leaf names, the report — is tested without a CLI
so it runs everywhere; the end-to-end cases put a fixture asset where
`capture_asset.py` would have left it, run the unit's own process step over it,
and — for a video, whose page only `pipeline extract` can mint — the REAL
extractor.

Loaded by path: unit scripts live under `skills/<unit>/scripts/` and are
launched with `uv run`, so there is no package to import them from.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


UNIT_DIR = Path(__file__).resolve().parents[1]  # the unit, wherever its tree sits
SCRIPTS = UNIT_DIR / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
UNIT = "channel-frameio"

SHARE = "https://next.frame.io/share/00000000-0000-0000-0000-000000000000"
FOLDER = f"{SHARE}/11111111-1111-1111-1111-111111111111"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))  # sibling imports, as `uv run` does
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


def _flag(cmd, flag, default=None):
    """One flag's value off a child's argv, in the `--flag=<value>` form the
    unit uses (and the separate-item form it used to)."""
    for i, item in enumerate(cmd):
        if item.startswith(f"{flag}="):
            return item.split("=", 1)[1]
        if item == flag and i + 1 < len(cmd):
            return cmd[i + 1]
    return default


def _flags(cmd, flag):
    return [item.split("=", 1)[1] for item in cmd if item.startswith(f"{flag}=")]


def _leaf(n, name=None, path=("Share", "Decks")):
    asset = f"{n:08d}-aaaa-bbbb-cccc-dddddddddddd"
    return {"asset_id": asset, "name": name or f"Deck {n}.pdf", "path": list(path), "view_url": f"{SHARE}/view/{asset}"}


def _ticket(**over):
    ticket = {
        "v": 1, "ticket": "0123456789ab", "unit": UNIT, "slug": "talks", "item": SHARE, "target": SHARE,
        "capture_dir": "_raw/talks/share-0000--deadbeef", "dest": None, "hosts": ["*.frame.io", "frame.io"],
        "harvest": {"scope": "domain", "access": "free", "exclude_urls": [], "assets": "download"},  # the shipped default
        "options": {}, "credential": None, "min_date": None, "known": [],
    }
    ticket.update(over)
    return ticket


def _harvest(**keys):
    return {"scope": "domain", "access": "free", "exclude_urls": [], "assets": "download", **keys}


# ---------------------------------------------------------------- the plan


def test_a_leaf_dir_is_the_hosts_own_shape_and_one_component():
    """`apply` mints a process ticket only for `_raw/<slug>/<one>`, and the
    host's namer is `<slug>--<first 8 hex of sha1(item)>` — composed here
    because the host has no verb for it."""
    mod = _module("harvest_share")
    leaf = _leaf(1, name="Q3 Roadmap / Final.pdf", path=("Share", "Decks", "2026"))
    (planned,) = mod.plan_leaves([leaf], _ticket())["leaves"]

    hash8 = hashlib.sha1(leaf["view_url"].encode()).hexdigest()[:8]
    assert planned["dir"] == f"_raw/talks/decks-2026-q3-roadmap-final--{hash8}"
    assert len(Path(planned["dir"]).parts) == 3
    assert planned["item"] == leaf["view_url"] and planned["path"] == ["Share", "Decks", "2026"]


def test_a_nameless_leaf_is_named_for_its_asset_id_and_a_long_one_is_capped():
    rec = _module("capture_record")
    url = f"{SHARE}/view/abc123"
    assert rec.leaf_dir_name(url) == f"abc123--{hashlib.sha1(url.encode()).hexdigest()[:8]}"
    long = rec.leaf_dir_name(url, "x" * 200 + ".pdf", [])
    assert re.fullmatch(r"x{60}--[0-9a-f]{8}", long), long


def test_the_top_folder_is_dropped_only_when_every_leaf_carries_it():
    """`enumerate_tree.py` starts `path` EMPTY at the URL it was given, so
    `path[0]` is the first folder below it — not the share's own name. One
    top folder on every leaf tells nothing apart; several are the distinction."""
    mod, rec = _module("harvest_share"), _module("capture_record")
    assert rec.shared_top([["Share", "A"], ["Share"], ["Share", "B", "C"]]) == 1
    assert rec.shared_top([["Client A"], ["Client B", "Drafts"]]) == 0
    assert rec.shared_top([["Share"], []]) == 0 and rec.shared_top([[], []]) == 0 and rec.shared_top([]) == 0

    one = mod.plan_leaves([_leaf(1, "Brief.pdf", ("Share", "Client A")), _leaf(2, "Brief.pdf", ("Share", "Client B"))], _ticket())
    assert [leaf["crumb"] for leaf in one["leaves"]] == [["Client A"], ["Client B"]]
    assert [Path(leaf["dir"]).name.rsplit("--", 1)[0] for leaf in one["leaves"]] == ["client-a-brief", "client-b-brief"]

    many = mod.plan_leaves([_leaf(1, "Brief.pdf", ("Client A",)), _leaf(2, "Brief.pdf", ("Client B",))], _ticket())
    assert [leaf["crumb"] for leaf in many["leaves"]] == [["Client A"], ["Client B"]], "the top folder IS the distinction: kept"
    assert [rec.leaf_qualifiers(leaf)[0] for leaf in many["leaves"]] == ["Client A", "Client B"]
    # Judged over the whole MANIFEST, so a leaf's dir does not move as `known[]` grows.
    known = _ticket(known=[{"resource": _leaf(2)["view_url"], "harvested_at": None}])
    (kept,) = mod.plan_leaves([_leaf(1, "Brief.pdf", ("Client A",)), _leaf(2, "Brief.pdf", ("Client B",))], known)["leaves"]
    assert kept["dir"] == many["leaves"][0]["dir"]


def test_a_leaf_url_must_be_http():
    mod = _module("harvest_share")
    for bad in ("-rf", "file:///etc/passwd", "javascript:alert(1)//share/00000000-0000-0000-0000-000000000000/view/x", f"{SHARE}/view/a b"):
        with pytest.raises(mod.Unusable):
            mod.leaves_of({"leaves": [{"view_url": bad}]})


def test_known_excluded_and_duplicate_leaves_are_not_planned_and_order_holds():
    """`known[]` is the resume state: a share too big for one slice finishes
    on a later ticket that is handed the pages the first one landed."""
    mod = _module("harvest_share")
    leaves = [_leaf(n) for n in range(1, 7)] + [_leaf(2)]
    ticket = _ticket(
        known=[{"resource": leaves[0]["view_url"], "harvested_at": "2026-09-01T00:00:00Z"}, "junk", {"resource": 7}],
        harvest=_harvest(exclude_urls=[leaves[2]["view_url"], f"{SHARE}/view/00000004*", ""]),
    )
    plan = mod.plan_leaves(leaves, ticket)

    assert [p["item"] for p in plan["leaves"]] == [leaves[i]["view_url"] for i in (1, 4, 5)]
    assert plan["skipped"] == {"known": 1, "excluded": 2, "scope": 0, "duplicate": 1, "reference": 0}


def test_an_explicit_reference_plans_no_media_leaf_and_every_document():
    """The only reference this venue offers is a signed HLS URL that is dead
    by the next day, and nothing transcribes a video that was never fetched —
    so `reference` keeps a share's documents and leaves its media unplanned.
    A leaf with no card name is captured whatever the value says: its kind
    is known only once fetched."""
    mod = _module("harvest_share")
    leaves = [_leaf(1, "Keynote.MOV"), _leaf(2), _leaf(3, "Intro.mp4"), _leaf(4, "Q&A.m4a"), _leaf(5, "Brief.pdf.mp4"), _leaf(6, "")]
    leaves[5]["name"] = None
    plan = mod.plan_leaves(leaves, _ticket(harvest=_harvest(assets="reference")))
    assert [p["item"] for p in plan["leaves"]] == [leaves[i]["view_url"] for i in (1, 5)]
    assert plan["skipped"]["reference"] == 4
    assert plan["unplanned"] == [{"item": leaves[i]["view_url"], "name": leaves[i]["name"], "why": "reference"} for i in (0, 2, 3, 4)]
    assert not mod.is_media_name("Notes.ts"), "a .ts stays a document: a false positive drops one, a false negative downloads a video"
    for assets in ("download", "download-audio", None):
        plan = mod.plan_leaves(leaves, _ticket(harvest=_harvest(assets=assets)))
        assert len(plan["leaves"]) == 6 and plan["skipped"]["reference"] == 0, assets
    # a single-leaf ticket carries no card name: planned, under every value
    single = mod.single_leaf_plan(_ticket(target=leaves[0]["view_url"], harvest=_harvest(assets="reference")))
    assert len(single["leaves"]) == 1 and single["skipped"]["reference"] == 0


def test_a_share_that_is_all_media_under_reference_is_skipped_and_says_why():
    mod = _module("harvest_share")
    plan = mod.plan_leaves([_leaf(1, "a.mp4"), _leaf(2, "b.mov")], _ticket(harvest=_harvest(assets="reference")))
    report = mod.report_of(_ticket(), plan, {})
    assert report["outcome"] == "skipped" and "harvest.assets=reference" in report["reason"] and "2 leaves" in report["reason"]


@pytest.mark.parametrize(
    "target, scope, kept",
    [
        (SHARE, "domain", 3),
        (FOLDER, "domain", 3),
        (SHARE, "section", 3),  # a leaf is /share/<id>/view/<asset>: under a share ROOT…
        (FOLDER, "section", 0),  # …and under no FOLDER
        (SHARE, "page", 0),
        (SHARE, None, 3),  # a ticket with no scope reads as the unit's shipped default
    ],
)
def test_scope_is_read_literally_against_the_target(target, scope, kept):
    mod = _module("harvest_share")
    plan = mod.plan_leaves([_leaf(n) for n in (1, 2, 3)], _ticket(target=target, harvest=_harvest(scope=scope)))
    assert len(plan["leaves"]) == kept and plan["skipped"]["scope"] == 3 - kept


def test_a_manifest_without_leaves_is_refused():
    mod = _module("harvest_share")
    for bad in ([], {"leaves": "nope"}, {"leaves": [{"name": "no url"}]}):
        with pytest.raises(mod.Unusable):
            mod.leaves_of(bad)


# -------------------------------------------------------------- the report


def _report(plan_leaves, states, ticket=None, **skipped):
    mod = _module("harvest_share")
    plan = {"scope": "domain", "leaves": plan_leaves, "skipped": {"known": 0, "excluded": 0, "scope": 0, "duplicate": 0, "reference": 0, **skipped}}
    return mod.report_of(ticket or _ticket(), plan, states)


def _planned(*ns):
    mod = _module("harvest_share")
    return mod.plan_leaves([_leaf(n) for n in ns], _ticket())["leaves"]


def test_the_report_lists_every_landed_leaf_and_is_the_documented_shape():
    leaves = _planned(1, 2)
    report = _report(leaves, {leaf["item"]: ("captured", f"T{i}") for i, leaf in enumerate(leaves)})

    assert list(report) == ["v", "ticket", "outcome", "reason", "captured", "written", "missing", "discovered"]
    assert report["outcome"] == "ok" and report["reason"] is None and report["ticket"] == "0123456789ab"
    assert report["captured"] == [{"item": leaf["item"], "dir": leaf["dir"], "title": f"T{i}"} for i, leaf in enumerate(leaves)]
    # `discovered[]` does nothing for pages now; a leaf is captured or it is not.
    assert report["discovered"] == [] and report["written"] == [] and report["missing"] == []


def test_a_slice_that_stopped_early_reports_partial_with_the_count():
    leaves = _planned(1, 2, 3)
    report = _report(leaves, {leaves[0]["item"]: ("captured", None), leaves[1]["item"]: ("failed", "timeout")})

    assert report["outcome"] == "partial"
    assert "1 of 3" in report["reason"] and "1 failed" in report["reason"] and "1 not reached" in report["reason"]
    assert [c["dir"] for c in report["captured"]] == [leaves[0]["dir"]]
    assert report["missing"] == [{"host": "next.frame.io", "url": leaves[1]["item"], "why": "timeout"}]


def test_nothing_captured_is_failed_and_an_all_known_share_is_skipped():
    leaves = _planned(1)
    assert _report(leaves, {})["outcome"] == "failed"
    assert _report(leaves, {leaves[0]["item"]: ("failed", "error")})["outcome"] == "failed"

    held = _report([], {}, known=4)
    assert held["outcome"] == "skipped" and held["reason"].startswith("known:")


def test_a_scope_that_empties_the_plan_fails_loudly_and_names_the_fix():
    """The old host-side filter dropped every leaf as out of scope while the
    run exited 0. The unit applies scope itself now, so it can say so."""
    report = _report([], {}, scope=5)
    assert report["outcome"] == "failed" and "harvest.scope=domain" in report["reason"]


# ------------------------------------------------------------- the driver


def _fake_capture_job(calls, fail=()):
    """Stands in for `uv run capture_job.py …`: leaves what a captured leaf
    leaves — the downloaded file and a FLAT record naming it — or exits 1
    having left nothing."""

    def run(cmd, timeout=None):
        calls.append(cmd)
        leaf_dir = Path(cmd[3])
        url = _flag(cmd, "--url")
        if any(url.endswith(f) for f in fail):
            return 1, "", "Timeout 20000ms exceeded"
        (leaf_dir / "document.pdf").write_bytes(b"%PDF-1.4 fake")
        (leaf_dir / "capture.json").write_text(
            json.dumps({"item": url, "title": "T", "body": "document.pdf", "content_type": "application/pdf"}),
            encoding="utf-8",
        )
        return 0, "{}", ""

    return run


def _share_dir(tmp_path, leaves, **ticket_over):
    ticket = _ticket(**ticket_over)
    cap = tmp_path / ticket["capture_dir"]
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    (cap / "tree.json").write_text(json.dumps({"share_id": "x", "domain": "next.frame.io", "leaves": leaves}), encoding="utf-8")
    return cap


def _drive(monkeypatch, capsys, cap, calls, *argv, fail=()):
    mod = _module("harvest_share")
    monkeypatch.setattr(mod, "run", _fake_capture_job(calls, fail=fail))
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(cap), "--pause-seconds", "0", *argv])
    code = mod.main()
    return code, json.loads(capsys.readouterr().out), json.loads((cap / "report.json").read_text())


def test_one_ticket_captures_every_leaf_into_its_own_dir_and_reports_last(monkeypatch, capsys, tmp_path):
    leaves = [_leaf(1), _leaf(2), _leaf(3)]
    cap = _share_dir(tmp_path, leaves, known=[{"resource": leaves[2]["view_url"], "harvested_at": None}])
    calls = []
    code, summary, report = _drive(monkeypatch, capsys, cap, calls, "--title-strip", " - Share", fail=("00000002-aaaa-bbbb-cccc-dddddddddddd",))

    assert code == 0 and summary["stop"] == "done"
    assert (summary["planned"], summary["captured"], summary["failed"], summary["skipped"]["known"]) == (2, 1, 1, 1)
    assert report["outcome"] == "partial" and report["ticket"] == "0123456789ab"
    (landed,) = report["captured"]
    assert landed["item"] == leaves[0]["view_url"] and (tmp_path / landed["dir"] / "capture.json").is_file()
    assert Path(landed["dir"]).parent == Path("_raw/talks") and landed["dir"] != "_raw/talks/share-0000--deadbeef"
    assert report["missing"] == [{"host": "next.frame.io", "url": leaves[1]["view_url"], "why": "timeout"}]
    # The manifest's name and breadcrumb ride to the per-leaf capture, and so does the operator's title trim.
    first = calls[0]
    assert Path(first[2]).name == "capture_job.py" and "--name=Deck 1.pdf" in first
    assert "--title-strip= - Share" in first and _flags(first, "--path") == ["Share", "Decks"]
    # Every leaf carries `Share`, so it is left off the breadcrumb: one leading folder to skip.
    assert "--crumb-skip=1" in first


def test_a_second_pass_refetches_nothing_that_landed_and_a_spent_budget_stops_cleanly(monkeypatch, capsys, tmp_path):
    cap = _share_dir(tmp_path, [_leaf(1), _leaf(2)])
    calls = []
    code, summary, report = _drive(monkeypatch, capsys, cap, calls, "--budget-seconds", "-1")
    # Out of budget before the first leaf: nothing fetched, and STILL a report.
    assert calls == [] and summary["stop"] == "budget" and summary["remaining"] == 2
    assert code == 1 and report["outcome"] == "failed" and "2 not reached" in report["reason"]

    code, summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 2 and report["outcome"] == "ok" and code == 0

    code, summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 2, "a leaf whose dir already holds its capture is counted, not re-fetched"
    assert report["outcome"] == "ok" and len(report["captured"]) == 2


def _respawn(cap, age=0.0):
    """What the spawner does on every dispatch — a pull, a `queue retry`, a
    respawn after a widen: `ticket.json` REWRITTEN, the SAME id in it (the id
    is a hash of the job's slug and target). `age` backdates it, which is how
    a test gets a spawn that happened a while ago."""
    path = cap / "ticket.json"
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    stamp = time.time_ns() - int(age * 1e9)
    before = path.stat().st_mtime_ns
    os.utime(path, ns=(stamp, stamp if stamp != before else stamp + 1))


def _age_state(cap, root, seconds):
    """Everything an earlier spawn left, moved `seconds` into the past: the
    ticket file, the plan's clock (whatever it is called) and the leaves' error files."""
    plan = json.loads((cap / "plan.json").read_text())
    for key in ("first_pass_epoch", "slice_epoch"):
        if isinstance(plan.get(key), (int, float)):
            plan[key] -= seconds
    (cap / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    for path in [cap / "ticket.json", cap / "plan.json", *root.glob("_raw/*/*/error.json")]:
        stamp = path.stat().st_mtime_ns - int(seconds * 1e9)
        os.utime(path, ns=(stamp, stamp))


def test_the_next_pull_of_a_share_starts_leaves_and_retries_what_the_last_one_failed(monkeypatch, capsys, tmp_path):
    """The blocker. A ticket's id never changes between pulls and the capture
    dir is only ever `mkdir -p`'d, so state keyed on the id made every later
    pull inherit the first one's slice clock — "spent" before it began: no
    leaf started, the failed leaf never retried, outcome `failed`."""
    leaves = [_leaf(1), _leaf(2), _leaf(3)]
    cap = _share_dir(tmp_path, leaves)
    calls = []
    failing = ("00000002-aaaa-bbbb-cccc-dddddddddddd", "00000003-aaaa-bbbb-cccc-dddddddddddd")
    _code, _summary, report = _drive(monkeypatch, capsys, cap, calls, fail=failing)
    assert len(calls) == 3 and report["outcome"] == "partial" and len(report["missing"]) == 2
    _drive(monkeypatch, capsys, cap, calls, fail=failing)
    assert len(calls) == 3, "within ONE spawn a leaf that failed is not hammered"

    _age_state(cap, tmp_path, 7200)  # two hours pass; the first page landed and is known
    ticket = json.loads((cap / "ticket.json").read_text())
    ticket["known"] = [{"resource": leaves[0]["view_url"], "harvested_at": "2026-09-19T00:00:00Z"}]
    (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    _respawn(cap)
    assert json.loads((cap / "ticket.json").read_text())["ticket"] == "0123456789ab", "the SAME id, as on every real pull"

    code, summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 5, "the next pull starts the leaves the last one owed"
    assert (code, summary["stop"], report["outcome"]) == (0, "done", "ok")
    assert [c["item"] for c in report["captured"]] == [leaves[1]["view_url"], leaves[2]["view_url"]]


def test_a_respawn_after_a_widen_is_a_fresh_spawn_too(monkeypatch, capsys, tmp_path):
    """`dispatch <id> widen=<host>` goes down the one spawn path: `ticket.json`
    rewritten, a fresh thirty minutes — minutes, not hours, after the last."""
    cap = _share_dir(tmp_path, [_leaf(1)])
    calls = []
    _drive(monkeypatch, capsys, cap, calls, fail=("dddddddddddd",))
    assert len(calls) == 1
    _respawn(cap)
    code, _summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 2 and code == 0 and report["outcome"] == "ok"


def test_the_slice_clock_is_the_spawn_not_this_scripts_first_pass(monkeypatch, capsys, tmp_path):
    """The kill is measured from the spawn, and the enumeration runs before the
    first pass. A spawn 1600 s old starts no leaf — and says so, with a report."""
    cap = _share_dir(tmp_path, [_leaf(1)])
    _respawn(cap, age=1600)
    calls = []
    code, summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert calls == [] and summary["stop"] == "slice" and code == 1 and "1 not reached" in report["reason"]
    # A hand run in an old capture dir is in no slice: `--slice-seconds 0` says so.
    code, summary, report = _drive(monkeypatch, capsys, cap, calls, "--slice-seconds", "0")
    assert len(calls) == 1 and report["outcome"] == "ok" and _flag(calls[0], "--deadline-seconds") is None


def test_every_child_gets_what_is_left_of_the_slice_and_a_killed_one_is_a_timeout(monkeypatch, capsys, tmp_path):
    cap = _share_dir(tmp_path, [_leaf(1), _leaf(2)])
    _respawn(cap, age=1000)
    mod = _module("harvest_share")
    seen = []

    def run(cmd, timeout=None):
        seen.append((cmd, timeout))
        leaf_dir = Path(cmd[3])
        if len(seen) == 1:  # killed mid-write: a capture.json may be there, and is not a capture
            (leaf_dir / "document.pdf").write_bytes(b"%PDF-1.4 fake")
            (leaf_dir / "capture.json").write_text(json.dumps({"title": "T", "body": "document.pdf"}), encoding="utf-8")
            return _module("capture_record").TIMED_OUT, "", "timeout: killed after 740s"
        return _fake_capture_job([])(cmd)

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(cap), "--pause-seconds", "0"])
    assert mod.main() == 0
    capsys.readouterr()
    report = json.loads((cap / "report.json").read_text())
    assert report["outcome"] == "partial" and [m["why"] for m in report["missing"]] == ["timeout"]
    for cmd, timeout in seen:  # 1740 s after the spawn, less the 1000 already gone
        assert 700 < timeout <= 740
        assert 0 < float(_flag(cmd, "--deadline-seconds")) < timeout, "the child's own children expire first"


def test_run_kills_a_child_and_everything_it_started_at_the_deadline(tmp_path):
    """`uv run` is the parent of the script, which is the parent of yt-dlp:
    killing only the first leaves the download running past the slice."""
    rec = _module("capture_record")
    pidfile = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    began = time.monotonic()
    code, _out, err = rec.run([sys.executable, "-c", child], timeout=1.5)
    assert code == rec.TIMED_OUT and "timeout" in err and time.monotonic() - began < 20
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("the grandchild outlived the deadline")
    assert rec.run([sys.executable, "-c", "print('hi')"], timeout=30) == (0, "hi", "")


def test_the_report_is_rewritten_after_every_leaf_so_a_killed_pass_still_reports(monkeypatch, capsys, tmp_path):
    """One long video on pass 1 could outrun the slice's kill; written only at
    the end of the pass, the report went with it and the ticket was `no_report`."""
    cap = _share_dir(tmp_path, [_leaf(1), _leaf(2), _leaf(3)])
    (cap / "report.json").write_text(json.dumps({"v": 1, "outcome": "ok", "captured": [{"dir": "_raw/talks/stale"}]}), encoding="utf-8")
    mod = _module("harvest_share")
    seen, fake = [], _fake_capture_job([])

    def run(cmd, timeout=None):
        seen.append(json.loads((cap / "report.json").read_text()))
        if len(seen) == 3:
            raise KeyboardInterrupt  # the slice's kill, as near as a test gets
        return fake(cmd)

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(cap), "--pause-seconds", "0"])
    with pytest.raises(KeyboardInterrupt):
        mod.main()
    # Before the first leaf: the earlier spawn's `ok` is already gone, and what stands is true.
    assert seen[0]["outcome"] == "failed" and seen[0]["captured"] == []
    assert [len(r["captured"]) for r in seen] == [0, 1, 2]
    left = json.loads((cap / "report.json").read_text())
    assert left["outcome"] == "partial" and len(left["captured"]) == 2 and "1 not reached" in left["reason"]
    assert sorted(p.name for p in cap.iterdir()) == ["plan.json", "report.json", "ticket.json", "tree.json"], "no temp file left"


def test_a_refusal_removes_the_report_an_earlier_run_left(monkeypatch, capsys, tmp_path):
    """`apply` does not check whose `report.json` it reads: a refusal that
    left the last run's `ok` standing would be read as this run's success."""
    cap = _share_dir(tmp_path, [_leaf(1)])
    (cap / "report.json").write_text(json.dumps({"v": 1, "outcome": "ok"}), encoding="utf-8")
    (cap / "tree.json").unlink()
    mod = _module("harvest_share")
    monkeypatch.setattr(mod, "run", _fake_capture_job([]))
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(cap)])
    assert mod.main() == 2 and not (cap / "report.json").exists()


def test_write_json_is_atomic(monkeypatch, tmp_path):
    rec = _module("capture_record")
    target = tmp_path / "report.json"
    rec.write_json(target, {"outcome": "ok"})
    monkeypatch.setattr(rec.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("killed here")))
    with pytest.raises(OSError):
        rec.write_json(target, {"outcome": "failed", "pad": "x" * 10000})
    assert json.loads(target.read_text()) == {"outcome": "ok"}, "a write that did not finish changed nothing"
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


# ---- refresh: exactly the refreshed resource, read again ------------------------------


def _refresh_dir(tmp_path, resource):
    ticket = _ticket(target=resource, item=resource, refresh=True, resource=resource, prev_harvested_at="2026-08-01T00:00:00Z",
                     capture_dir="_raw/talks/share-0000-view-asset--feedface",
                     known=[{"resource": resource, "harvested_at": "2026-08-01T00:00:00Z"}])
    cap = tmp_path / ticket["capture_dir"]
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    return cap


def test_a_refresh_ticket_recaptures_its_leaf_instead_of_counting_the_old_capture(monkeypatch, capsys, tmp_path):
    """The refresh's capture dir is stable, so the LAST refresh's capture is
    in it. Counted as landed, nothing was fetched and `apply` restamped
    `unchanged` over bytes nobody re-read."""
    resource = _leaf(9)["view_url"]
    cap = _refresh_dir(tmp_path, resource)
    (cap / "document.pdf").write_bytes(b"%PDF-1.4 last time")
    (cap / "capture.json").write_text(json.dumps({"item": resource, "title": "Old", "body": "document.pdf"}), encoding="utf-8")
    (cap / "report.json").write_text(json.dumps({"v": 1, "outcome": "ok"}), encoding="utf-8")  # the process step's, from last time
    calls = []
    code, summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 1 and "--fresh" in calls[0] and _flag(calls[0], "--url") == resource
    assert Path(calls[0][3]) == cap, "exactly the refreshed resource, in the ticket's own dir — known[] does not skip it"
    assert code == 0 and report["outcome"] == "ok" and report["captured"] == [{"item": resource, "dir": "_raw/talks/share-0000-view-asset--feedface", "title": "T"}]
    _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 1, "once a spawn: a later pass of the same spawn counts what this one fetched"
    _respawn(cap)
    _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 2, "the next refresh reads it again"


def test_a_refresh_that_fails_reports_failed_not_the_old_capture(monkeypatch, capsys, tmp_path):
    resource = _leaf(9)["view_url"]
    cap = _refresh_dir(tmp_path, resource)
    (cap / "document.pdf").write_bytes(b"%PDF-1.4 last time")
    (cap / "capture.json").write_text(json.dumps({"item": resource, "title": "Old", "body": "document.pdf"}), encoding="utf-8")
    code, _summary, report = _drive(monkeypatch, capsys, cap, [], fail=("dddddddddddd",))
    assert code == 1 and report["outcome"] == "failed" and report["captured"] == []


def test_a_refresh_of_something_that_is_no_leaf_viewer_is_refresh_unsupported(monkeypatch, capsys, tmp_path):
    cap = _refresh_dir(tmp_path, SHARE)
    calls = []
    mod = _module("harvest_share")
    monkeypatch.setattr(mod, "run", _fake_capture_job(calls))
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(cap)])
    assert mod.main() == 1 and calls == []
    report = json.loads((cap / "report.json").read_text())
    assert report["outcome"] == "failed" and report["reason"].startswith("refresh_unsupported:")


def test_a_capture_dir_that_is_not_the_tickets_is_refused_before_anything_runs(monkeypatch, capsys, tmp_path):
    cap = _share_dir(tmp_path, [_leaf(1)])
    elsewhere = tmp_path / "_raw" / "talks" / "somewhere-else"
    elsewhere.mkdir()
    shutil.copy(cap / "ticket.json", elsewhere / "ticket.json")
    shutil.copy(cap / "tree.json", elsewhere / "tree.json")
    mod = _module("harvest_share")
    calls = []
    monkeypatch.setattr(mod, "run", _fake_capture_job(calls))
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(elsewhere)])
    assert mod.main() == 2 and calls == [] and not (elsewhere / "report.json").exists()


# ------------------------------------------------- one leaf: capture_job.py


# ---- page names: one page per asset, whatever two assets are called -------------------


def test_the_page_key_is_the_hosts_filename_rule_plus_what_a_filesystem_folds():
    """`page/note.py::filename_for` is `title.strip() + ".md"` and nothing else:
    outer whitespace is all the HOST folds; case is what a case-insensitive
    filesystem folds under it."""
    rec = _module("capture_record")
    assert rec.page_key("Brief.pdf") == rec.page_key(" Brief.pdf\n") == rec.page_key("BRIEF.PDF")
    assert rec.page_key("Brief.pdf") != rec.page_key("Brief")  # nothing else is dropped
    assert rec.TITLE_ILLEGAL == '/\\:*?"<>|'  # `page/note.py::ILLEGAL` — a title carrying one is refused
    taken = {}
    assert rec.unique_title("Brief.pdf ", ["Client A", "aaaaaaaa"], taken) == "Brief.pdf "  # the first: untouched
    assert rec.unique_title("brief.pdf", ["Client B", "bbbbbbbb"], taken) == "brief.pdf (Client B)"
    assert rec.unique_title("Brief.pdf", ["Client A", "cccccccc"], taken) == "Brief.pdf (cccccccc)"  # same folder: tells nothing apart
    assert rec.unique_title("Brief.pdf", ["Client B", "dddddddd"], taken) == "Brief.pdf (dddddddd)"  # that name is taken
    assert rec.unique_title("Brief.pdf", ["Client B"], taken) == "Brief.pdf (Client B) (2)"
    # The breadcrumb is below the shared top folder, and never carries the `/` a title is refused for.
    leaf = {"item": f"{SHARE}/view/x", "path": ["Share", "Client A/B", "Drafts: v2"]}
    assert rec.leaf_qualifiers(leaf) == ["Client A/B - Drafts: v2", rec.hash8(leaf["item"])]
    assert rec.unique_title("Brief.pdf", rec.leaf_qualifiers(leaf), taken) == "Brief.pdf (Client A-B - Drafts- v2)"
    assert rec.leaf_qualifiers({"item": "u", "path": ["Share"]})[0] == ""  # only the shared top folder: nothing meaningful


def _titled_capture_job(titles):
    """As `_fake_capture_job`, the captured title read off the leaf's `--name`."""

    def run(cmd, timeout=None):
        leaf_dir, url = Path(cmd[3]), _flag(cmd, "--url")
        (leaf_dir / "document.pdf").write_bytes(b"%PDF-1.4 fake")
        title = titles[_flag(cmd, "--name")]
        (leaf_dir / "capture.json").write_text(
            json.dumps({"item": url, "title": title, "body": "document.pdf"}), encoding="utf-8")
        return 0, "{}", ""

    return run


def test_same_named_assets_are_told_apart_by_their_folder_on_every_pass(monkeypatch, capsys, tmp_path):
    leaves = [_leaf(1, "Brief.pdf", ("Share", "Client A")), _leaf(2, "Brief.pdf", ("Share", "Client B", "Drafts")),
              _leaf(3, "brief.PDF", ("Share", "Client A")), _leaf(4, "Notes.pdf", ("Share",)), _leaf(5, "Untitled", ("Share",)),
              _leaf(6, "Untitled 2", ("Share",))]
    cap = _share_dir(tmp_path, leaves)
    titles = {"Brief.pdf": "Brief.pdf", "brief.PDF": "brief.PDF ", "Notes.pdf": "Notes.pdf", "Untitled": None, "Untitled 2": None}
    mod, hash8 = _module("harvest_share"), _module("capture_record").hash8
    want = ["Brief.pdf", "Brief.pdf (Client B - Drafts)", f"brief.PDF ({hash8(leaves[2]['view_url'])})", "Notes.pdf",
            None, f"document ({hash8(leaves[5]['view_url'])})"]  # no title: the body's own stem tells them apart
    for _ in range(2):  # a second pass fetches nothing and renames nothing a second time
        monkeypatch.setattr(mod, "run", _titled_capture_job(titles))
        monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(cap), "--pause-seconds", "0"])
        assert mod.main() == 0
        capsys.readouterr()
        report = json.loads((cap / "report.json").read_text())
        assert [c["title"] for c in report["captured"]] == want
        assert [json.loads((tmp_path / c["dir"] / "capture.json").read_text()).get("title") for c in report["captured"]] == want


def _fake_asset_run(calls, kind, title="Real Title - Fixture Share"):
    """`capture_asset.py` stubbed — it needs a browser and the venue. It is the
    ONLY child a capture has: harvest renders nothing."""

    def run(cmd, timeout=None):
        calls.append(cmd)
        out = Path(_flag(cmd, "--out"))
        out.mkdir(parents=True, exist_ok=True)
        name = _flag(cmd, "--name")
        if kind == "video":
            (out / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        else:
            shutil.copy(FIXTURES / "deck.pdf", out / "document.pdf")
        meta = {"url": cmd[3], "final_url": cmd[3], "title": title, "name": name, "kind": kind, "bytes": 16}
        (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return 0, json.dumps({"ok": True, "kind": kind, "bytes": 16, "title": meta["title"]}), ""

    return run


def _capture_leaf(monkeypatch, capsys, root, leaf_dir, url, kind, *argv, title="Real Title - Fixture Share"):
    mod = _module("capture_job")
    calls = []
    monkeypatch.setattr(mod, "run", _fake_asset_run(calls, kind, title))
    monkeypatch.setattr(sys, "argv", ["capture_job.py", str(leaf_dir), "--root", str(root), "--url", url, *argv])
    code = mod.main()
    return code, calls, capsys.readouterr()


def test_a_video_leaf_names_the_media_file_as_its_body(monkeypatch, capsys, tmp_path):
    """The extractor hands a media body to the transcriber. A `page.md` that
    only linked the video would be taken as the whole page."""
    url = f"{SHARE}/view/vid00001"
    leaf_dir = tmp_path / "_raw/talks/keynote--0a0a0a0a"
    code, calls, _io = _capture_leaf(
        monkeypatch, capsys, tmp_path, leaf_dir, url, "video",
        "--slug", "talks", "--name", "Keynote.mov", "--path", "Share", "--path", "Day 1", "--title-strip", " - Fixture Share",
    )
    assert code == 0 and [Path(c[2]).name for c in calls] == ["capture_asset.py"]

    record = json.loads((leaf_dir / "capture.json").read_text())
    assert (record["body"], record["content_type"], record["item"], record["slug"]) == ("video.mp4", "video/mp4", url, "talks")
    assert record["title"] == "Real Title" and not (leaf_dir / "page.md").exists()
    assert list(record) == ["v", "slug", "item", "title", "body", "content_type", "fetched_at"], "flat: harvest is bytes"
    for field in ("slug", "item", "title", "body", "fetched_at"):
        assert isinstance(record[field], str), field
    # What the harvest knew and the bytes do not carry, for the process step.
    meta = json.loads((leaf_dir / "meta.json").read_text())
    assert meta["kind"] == "video" and meta["path"] == ["Share", "Day 1"] and meta["title_strip"] == " - Fixture Share"


def test_a_videos_title_is_made_a_filename_and_the_venues_own_is_kept(monkeypatch, capsys, tmp_path):
    """A video has no body to carry an H1, so the true title rides in `frontmatter`."""
    mod = _module("capture_job")
    url, leaf_dir = f"{SHARE}/view/vid00003", tmp_path / "_raw/talks/keynote--0b0b0b0b"
    fake = _fake_asset_run([], "video")

    def run(cmd, timeout=None):
        code, out, err = fake(cmd)
        return code, out.replace("Real Title - Fixture Share", "Day 1: Keynote / Q&A?"), err

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(sys, "argv", ["capture_job.py", str(leaf_dir), f"--root={tmp_path}", f"--url={url}", "--slug=talks", "--name=-k.mov"])
    assert mod.main() == 0
    record = json.loads((leaf_dir / "capture.json").read_text())
    assert record["title"] == "Day 1 - Keynote - Q&A", "the page's FILE is named from this one"
    meta = json.loads((leaf_dir / "meta.json").read_text())
    assert meta["title"] == "Day 1: Keynote / Q&A?", "the venue's own spelling, for the process step's H1"
    assert meta["name"] == "-k.mov"


def test_a_leafs_children_get_a_shorter_deadline_and_a_killed_fetch_leaves_nothing_behind(monkeypatch, capsys, tmp_path):
    mod = _module("capture_job")
    leaf_dir = tmp_path / "_raw/talks/leaf--1"
    leaf_dir.mkdir(parents=True)
    # What an earlier attempt left: none of it is this one's.
    (leaf_dir / "capture.json").write_text(json.dumps({"title": "Old", "body": "page.md"}), encoding="utf-8")
    (leaf_dir / "page.md").write_text("# old\n", encoding="utf-8")
    (leaf_dir / "video.mp4.part").write_bytes(b"resume me")
    seen = []

    def run(cmd, timeout=None):
        seen.append((cmd, timeout))
        (leaf_dir / "document.pdf").write_bytes(b"half")
        return mod.TIMED_OUT, "", "timeout: killed after 280s"

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(sys, "argv", ["capture_job.py", str(leaf_dir), f"--root={tmp_path}", "--slug=talks", f"--url={SHARE}/view/x1", "--deadline-seconds=300"])
    assert mod.main() == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is False and "timeout" in answer["error"]
    ((cmd, timeout),) = seen
    assert timeout == 290 and float(_flag(cmd, "--deadline-seconds")) == 280, "each level expires before the one above it"
    assert sorted(p.name for p in leaf_dir.iterdir()) == ["video.mp4.part"], "yt-dlp resumes a .part; nothing else survives"


@pytest.mark.parametrize(
    "rel",
    ["_raw/talks/a/b", "_raw/other/leaf--1", "_raw/talks/../other/leaf--1", "sources/talks/leaf--1", "_raw/talks"],
)
def test_a_leaf_dir_that_is_not_in_the_jobs_slice_refuses_before_fetching(monkeypatch, capsys, tmp_path, rel):
    (tmp_path / "_raw/talks").mkdir(parents=True)
    (tmp_path / "_raw/other").mkdir(parents=True)
    code, calls, io = _capture_leaf(monkeypatch, capsys, tmp_path, tmp_path / rel, f"{SHARE}/view/x1", "document", "--slug", "talks")
    assert code == 2 and calls == [] and "nothing was fetched" in io.err


def test_a_symlinked_leaf_dir_is_judged_by_where_the_bytes_land(monkeypatch, capsys, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "_raw/talks").mkdir(parents=True)
    (tmp_path / "_raw/talks/leaf--1").symlink_to(outside, target_is_directory=True)
    code, calls, _io = _capture_leaf(monkeypatch, capsys, tmp_path, tmp_path / "_raw/talks/leaf--1", f"{SHARE}/view/x1", "document", "--slug", "talks")
    assert code == 2 and calls == []


def test_a_capture_that_does_not_add_up_leaves_no_capture_json(monkeypatch, capsys, tmp_path):
    """No `capture.json` is how the driver reads a failed leaf, so a half
    capture must not leave one behind."""
    mod = _module("capture_job")
    leaf_dir = tmp_path / "_raw/talks/leaf--1"
    leaf_dir.mkdir(parents=True)
    monkeypatch.setattr(mod, "run", lambda cmd, timeout=None: (0, "not json", ""))
    monkeypatch.setattr(sys, "argv", ["capture_job.py", str(leaf_dir), "--root", str(tmp_path), "--slug", "talks", "--url", f"{SHARE}/view/x1"])
    assert mod.main() == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False and not (leaf_dir / "capture.json").exists()


# ------------------------------------------------ end to end, the real CLI


def _run_script(name, *argv, cwd, env=None):
    done = subprocess.run([sys.executable, str(SCRIPTS / name), *argv], cwd=cwd, env=env, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


def _queued_media(wiki, dest):
    """`(page, media)` for every stub under `dest` the transcribe stage would
    take, by ITS rule: `extracted: queued` and a non-empty `media`
    (`pipeline/media.py::queued_under`). Read here rather than imported — a
    unit reaches the machinery by verb, and so does its test."""
    found = []
    for path in sorted((wiki / dest).rglob("*.md")):
        front = path.read_text(encoding="utf-8").split("\n---\n", 1)[0]
        keys = dict(
            line.split(": ", 1) for line in front.splitlines() if ": " in line and not line.startswith(" ")
        )
        if keys.get("extracted") == "queued" and keys.get("media"):
            found.append((str(path.relative_to(wiki)), keys["media"]))
    return found


def _front_door(tmp, ops, env):
    """A `PATH` whose bare `llm-wiki-ops` is the harness's REAL CLI.

    In production that name is the console script on PATH, bound to the wiki
    by the cwd; a suite may reach neither the machine's wikis nor its packages
    home, so this execs the CLI under test with the harness's own environment
    instead. What is being tested is the page `page create` writes, not how
    the name resolves — `tests/test_scripts_frameio_doc_note.py` pins the
    argv, the cwd and the variable the unit drops.
    """
    bin_dir = Path(tempfile.mkdtemp(prefix="front-door-", dir=tmp))
    stub = bin_dir / "llm-wiki-ops"
    # `execve` takes a PATH, never a name: `LLM_WIKI_OPS` is a command LINE, and
    # its first word is `uv` wherever the CLI is run out of a checkout. Resolved
    # here, where PATH is still this process's.
    runner = shutil.which(ops[0]) or ops[0]
    stub.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"os.execve({runner!r}, [{runner!r}, *{list(ops[1:])!r}, *sys.argv[1:]], {dict(env)!r})\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}", "LLM_WIKI_OPS": str(stub)}


def _stub_front_door(tmp):
    """A recording `llm-wiki-ops` on PATH, for a case with no wiki behind it.
    Returns `(env, seen)`; `seen` holds one JSON line per call."""
    bin_dir = Path(tempfile.mkdtemp(prefix="stub-door-", dir=tmp))
    seen = bin_dir / "seen.jsonl"
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}) + '\\n')\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{bin_dir}{os.pathsep}{OFFLINE['PATH']}", "LLM_WIKI_OPS": str(stub)}, seen


def _process(wiki, capture_rel, dest, *extra, env, runner=None):
    """The unit's PROCESS step, as the SKILL runs it: the wiki root, the
    ticket's `capture_dir` and the ticket's `dest`."""
    argv = [".", f"--capture-dir={capture_rel}", f"--dest={dest}", *extra]
    if runner is not None:
        return runner(*argv, cwd=wiki, env=env)
    return _run_script("frameio_doc_note.py", *argv, cwd=wiki, env=env)


def _harvested(monkeypatch, capsys, wiki, leaf, slug, *argv, title="Real Title - Fixture Share"):
    """One planned leaf, harvested for real — `capture_job.py` with only the
    browser stubbed — and the flat record it leaves."""
    code, _calls, _io = _capture_leaf(
        monkeypatch, capsys, wiki, wiki / leaf["dir"], leaf["item"], "document",
        "--slug", slug, *( [f"--name={leaf['name']}"] if leaf.get("name") else [] ),
        *[f"--path={bit}" for bit in leaf.get("path") or []],
        f"--crumb-skip={len(leaf.get('path') or []) - len(leaf.get('crumb') or [])}",
        *argv, title=title,
    )
    assert code == 0
    return json.loads((wiki / leaf["dir"] / "capture.json").read_text())


OFFLINE = {**os.environ, "UV_OFFLINE": "1"}  # a test never reaches the network, and neither does a resolver it starts
OFFLINE.pop("VIRTUAL_ENV", None)


def _uv_cannot_run_the_renderer():
    """Why `uv run` cannot run `frameio_doc_note.py` here, or None when it can.

    Production runs the renderer with `uv run`, which resolves its PEP 723
    dependencies (pypdf, python-pptx, openpyxl). A test may not fetch them, so
    it asks uv to resolve them OFFLINE, from its cache: `-h` imports none of
    them, but uv builds the script's environment before it runs a line of it.
    """
    if shutil.which("uv") is None:
        return "no `uv` on PATH: the renderer's PEP 723 dependencies cannot be resolved"
    done = subprocess.run(["uv", "run", "--script", str(SCRIPTS / "frameio_doc_note.py"), "-h"], env=OFFLINE, capture_output=True, text=True)
    if done.returncode != 0:
        return f"uv cannot resolve pypdf/python-pptx/openpyxl offline (not in its cache): {done.stderr.strip()[-200:]}"
    return None


def _render_as_production_does(*argv, cwd, env=None):
    done = subprocess.run(
        ["uv", "run", "--script", str(SCRIPTS / "frameio_doc_note.py"), *argv],
        cwd=cwd, env={**OFFLINE, **(env or {})}, capture_output=True, text=True,
    )
    return done.returncode, done.stdout, done.stderr


def test_safe_title_is_a_title_the_hosts_filename_rule_holds():
    """`page/note.py::filename_for` refuses `/\\:*?"<>|`, a control character and
    a leading dot, and checks no length — the filesystem does, in BYTES."""
    rec = _module("capture_record")
    assert rec.safe_title('Lesson 3: "Pricing"? A/B <draft> | v2*') == "Lesson 3 - \u2019Pricing\u2019 A-B (draft) - v2"
    assert rec.safe_title("...hidden.pdf") == "hidden.pdf" and rec.safe_title(" . ") == "Untitled"
    assert rec.safe_title("a\nb\tc\x00d\x7fe\u2028f") == "a b c d e f"
    assert rec.safe_title("C:\\decks\\q3.pdf") == "C --decks-q3.pdf"
    assert rec.safe_title("", fallback="abc123") == "abc123" and rec.safe_title(None) == "Untitled"
    long = rec.safe_title("x" * 300)
    assert long == "x" * 120 + "…"
    cjk = rec.safe_title("路" * 100)
    assert cjk.endswith("…") and len((cjk + ".md").encode()) <= 255 and len(cjk.encode()) <= rec.TITLE_MAX_BYTES + 3
    assert rec.safe_title("Plain title.pdf") == "Plain title.pdf", "a legal title is left alone"
    for hostile in ('a/b', "x: y", "?", ".", "\x01", '"<>|*'):
        title = rec.safe_title(hostile)
        assert title and not title.startswith(".") and not any(c in rec.TITLE_ILLEGAL or ord(c) < 32 for c in title)


def test_a_qualified_title_still_fits_a_filename_and_keeps_its_qualifier():
    """`safe_title` caps the bytes and the de-dup qualifier put them back: the
    BASE gives way, never the ` (<folder>)` that tells the page apart."""
    rec = _module("capture_record")
    base = rec.safe_title("路" * 100)
    taken = {}
    first = rec.fitted_title(base, ["客户甲", "aaaaaaaa"], taken)
    second = rec.fitted_title(base, ["客户乙", "bbbbbbbb"], taken)
    third = rec.fitted_title(base, ["客户乙", "cccccccc"], taken)
    assert first == base and second.endswith("… (客户乙)") and third.endswith("… (cccccccc)")
    for title in (first, second, third):
        assert len((title + ".md").encode()) <= 255 and len(title.encode()) <= rec.TITLE_MAX_BYTES + 3
    assert len({rec.page_key(t) for t in (first, second, third)}) == 3
    # The next pass sees the fitted titles and renames nothing.
    again = {}
    assert [rec.fitted_title(t, q, again) for t, q in ((first, ["客户甲"]), (second, ["客户乙"]), (third, ["客户乙"]))] == [first, second, third]
    # A breadcrumb is capped in bytes before it qualifies anything.
    deep = rec.leaf_qualifiers({"item": "u", "crumb": ["深" * 40, "层" * 40]})[0]
    assert len(deep.encode()) <= rec.CRUMB_MAX_BYTES
    # A short title is `unique_title`'s answer, untouched.
    assert rec.fitted_title("Brief.pdf", ["Client B"], {rec.page_key("Brief.pdf"): []}) == "Brief.pdf (Client B)"


HOSTILE_TITLES = [
    ('Lesson 3: "Pricing"? A/B.pdf', "Lesson 3 - ’Pricing’ A-B.pdf"),
    ('.hidden: what/why?.pdf', "hidden - what-why.pdf"),
    ("路" * 100 + ".pdf", None),  # 300 bytes of CJK: the host checks no length, the filesystem does
]


STUB_ASSET = '''# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""capture_asset.py, stubbed: the one script that touches the network. Same argv, same outputs."""
import argparse, json, shutil
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("--out", required=True)
ap.add_argument("--name", default=None)
ap.add_argument("--timeout-ms", type=int, default=20000)
ap.add_argument("--deadline-seconds", type=float, default=None)
args = ap.parse_args()
out = Path(args.out)
shutil.copy(r"{fixture}", out / "document.pdf")
(out / "meta.json").write_text(json.dumps({{"url": args.url, "title": args.name, "name": args.name, "kind": "document", "bytes": 614}}))
print(json.dumps({{"ok": True, "kind": "document", "bytes": 614, "title": args.name}}))
'''


def test_the_real_argv_chain_carries_a_file_named_like_an_option(tmp_path):
    """Every driver test above replaces `run`, so the argv the three scripts
    hand each other was never parsed by the script it was meant for. A file
    called `-rf.pdf` in a folder called `--help`, as separate argv items, is an
    OPTION to argparse: exit 2 on every pass, the leaf never captured."""
    why = _uv_cannot_run_the_renderer()
    if why:
        pytest.skip(why)
    scripts = tmp_path / "unit" / "scripts"  # the unit's own scripts, with only the network-touching one stubbed
    shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "capture_asset.py").write_text(STUB_ASSET.format(fixture=FIXTURES / "deck.pdf"), encoding="utf-8")

    root = tmp_path / "wiki"
    leaves = [_leaf(1, name="-rf.pdf", path=("--help", "-x")), _leaf(2, name="--version", path=("Other",))]
    cap = _share_dir(root, leaves)
    done = subprocess.run(
        [sys.executable, str(scripts / "harvest_share.py"), "_raw/talks/share-0000--deadbeef", "--pause-seconds=0", "--author=-Ada"],
        cwd=root, env=OFFLINE, capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads((cap / "report.json").read_text())
    assert report["outcome"] == "ok" and report["missing"] == [], [json.loads(p.read_text()) for p in root.glob("_raw/talks/*/error.json")]
    assert [c["title"] for c in report["captured"]] == ["-rf.pdf", "--version"]

    first = root / report["captured"][0]["dir"]
    record = json.loads((first / "capture.json").read_text())
    assert record["body"] == "document.pdf" and not (first / "page.md").exists(), "harvest is bytes"
    meta = json.loads((first / "meta.json").read_text())
    assert meta["path"] == ["--help", "-x"] and meta["name"] == "-rf.pdf" and meta["author"] == "-Ada"

    # And the PROCESS step over the same leaf: the same venue text crosses argv
    # again, into the renderer and on to `page create`.
    door, seen = _stub_front_door(tmp_path)
    code, out, err = _process(root, report["captured"][0]["dir"], "sources/decks", env=door, runner=_render_as_production_does)
    assert code == 0, err
    body = (first / "page.md").read_text(encoding="utf-8")
    assert body.startswith("# -rf.pdf\n") and "*--help / -x*" in body and "- **File:** `-rf.pdf` (pdf, 614 bytes)" in body
    assert "- **Author:** -Ada" in body
    assert "Quarterly roadmap for the fixture share" in body, "and the PDF's text is inlined, run as production runs it"
    (call,) = [json.loads(line) for line in seen.read_text().splitlines()]
    assert call["argv"][:4] == ["--json", "page", "create", "title=-rf.pdf"], "a title opening with `-` is no option"
    assert "dest=sources/decks" in call["argv"] and call["stdin"] == body


