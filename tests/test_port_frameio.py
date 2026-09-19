"""`channel-frameio` on the ticket contract: one ticket captures a whole share.

Nothing fans a share's leaves out into further tickets any more, so the unit
does it: `harvest_share.py` plans the leaves from the ticket's own filters,
captures each into its own `_raw/<slug>/<leaf>--<hash8>/`, and writes the one
`report.json`. The pure half — the plan, the leaf names, the report — is
tested without a CLI so it runs everywhere; the end-to-end cases put a fixture
asset where `capture_asset.py` would have left it and run the REAL extractor
over what the unit rendered.

Loaded by path: unit scripts live under `skills/<unit>/scripts/` and are
launched with `uv run`, so there is no package to import them from.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import declared_job, extracted, ticket_in

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/channel-frameio/scripts"
FIXTURES = ROOT / "tests/fixtures/frameio"
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


def _leaf(n, name=None, path=("Share", "Decks")):
    asset = f"{n:08d}-aaaa-bbbb-cccc-dddddddddddd"
    return {"asset_id": asset, "name": name or f"Deck {n}.pdf", "path": list(path), "view_url": f"{SHARE}/view/{asset}"}


def _ticket(**over):
    ticket = {
        "v": 1, "ticket": "0123456789ab", "unit": UNIT, "slug": "talks", "item": SHARE, "target": SHARE,
        "capture_dir": "_raw/talks/share-0000--deadbeef", "dest": None, "hosts": ["*.frame.io", "frame.io"],
        "harvest": {"scope": "domain", "access": "free", "exclude_urls": [], "assets": "reference"},
        "options": {}, "credential": None, "min_date": None, "known": [],
    }
    ticket.update(over)
    return ticket


def _harvest(**keys):
    return {"scope": "domain", "access": "free", "exclude_urls": [], "assets": "reference", **keys}


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
    long = rec.leaf_dir_name(url, "x" * 200 + ".pdf", ["Share"])
    assert re.fullmatch(r"x{60}--[0-9a-f]{8}", long), long


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
    assert plan["skipped"] == {"known": 1, "excluded": 2, "scope": 0, "duplicate": 1}


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
    plan = {"scope": "domain", "leaves": plan_leaves, "skipped": {"known": 0, "excluded": 0, "scope": 0, "duplicate": 0, **skipped}}
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
    leaves, or exits 1 having left nothing."""

    def run(cmd):
        calls.append(cmd)
        leaf_dir = Path(cmd[3])
        url = cmd[cmd.index("--url") + 1]
        if any(url.endswith(f) for f in fail):
            return 1, "", "Timeout 20000ms exceeded"
        (leaf_dir / "page.md").write_text("# x\n", encoding="utf-8")
        (leaf_dir / "capture.json").write_text(json.dumps({"item": url, "title": "T", "body": "page.md"}), encoding="utf-8")
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
    assert Path(first[2]).name == "capture_job.py" and first[first.index("--name") + 1] == "Deck 1.pdf"
    assert first[first.index("--title-strip") + 1] == " - Share" and first.count("--path") == 2


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


def test_a_failure_another_ticket_recorded_is_retried_by_this_one(monkeypatch, capsys, tmp_path):
    """A share's capture dirs outlive a ticket. An `error.json` an earlier
    ticket left must not stop a later one from trying the leaf again."""
    cap = _share_dir(tmp_path, [_leaf(1)])
    calls = []
    _drive(monkeypatch, capsys, cap, calls, fail=("dddddddddddd",))
    assert len(calls) == 1
    _drive(monkeypatch, capsys, cap, calls, fail=("dddddddddddd",))
    assert len(calls) == 1, "the same ticket does not hammer a leaf it already failed"

    ticket = json.loads((cap / "ticket.json").read_text())
    (cap / "ticket.json").write_text(json.dumps({**ticket, "ticket": "ffffffffffff"}), encoding="utf-8")
    code, _summary, report = _drive(monkeypatch, capsys, cap, calls)
    assert len(calls) == 2 and code == 0 and report["outcome"] == "ok" and report["ticket"] == "ffffffffffff"


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


def _fake_asset_run(calls, kind):
    """`capture_asset.py` stubbed — it needs a browser and the venue — and
    `frameio_doc_note.py` run FOR REAL, under this interpreter."""

    def run(cmd):
        calls.append(cmd)
        script = Path(cmd[2]).name
        if script == "capture_asset.py":
            out = Path(cmd[cmd.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            name = cmd[cmd.index("--name") + 1] if "--name" in cmd else None
            if kind == "video":
                (out / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
            else:
                shutil.copy(FIXTURES / "deck.pdf", out / "document.pdf")
            meta = {"url": cmd[3], "final_url": cmd[3], "title": "Real Title - Fixture Share", "name": name, "kind": kind, "bytes": 16}
            (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
            return 0, json.dumps({"ok": True, "kind": kind, "bytes": 16, "title": meta["title"]}), ""
        done = subprocess.run([sys.executable, *cmd[2:]], capture_output=True, text=True)
        return done.returncode, done.stdout.strip(), done.stderr.strip()

    return run


def _capture_leaf(monkeypatch, capsys, root, leaf_dir, url, kind, *argv):
    mod = _module("capture_job")
    calls = []
    monkeypatch.setattr(mod, "run", _fake_asset_run(calls, kind))
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
    assert record["frontmatter"]["type"] == "video" and record["frontmatter"]["path"] == ["Share", "Day 1"]
    for field in ("slug", "item", "title", "body", "fetched_at"):
        assert isinstance(record[field], str), field


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
    monkeypatch.setattr(mod, "run", lambda cmd: (0, "not json", ""))
    monkeypatch.setattr(sys, "argv", ["capture_job.py", str(leaf_dir), "--root", str(tmp_path), "--slug", "talks", "--url", f"{SHARE}/view/x1"])
    assert mod.main() == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False and not (leaf_dir / "capture.json").exists()


# ------------------------------------------- end to end, the real extractor


def _run_script(name, *argv, cwd):
    done = subprocess.run([sys.executable, str(SCRIPTS / name), *argv], cwd=cwd, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


def test_a_share_document_becomes_a_page_under_the_jobs_dest(ops, env, wiki):
    """Plan the share, put a fixture document where `capture_asset.py` would
    have, render it with the ported `frameio_doc_note.py`, write the ticket's
    report — then the REAL `pipeline extract` over the leaf the report names."""
    job = declared_job(ops, env, wiki, UNIT, SHARE, "dest=sources/scrapes/port-frameio")
    assert job.record["harvest"]["scope"] == "domain"  # the manifest's shipped default
    cap = ticket_in(wiki, job, "share-0000--e2e00001", unit=UNIT, item=SHARE)
    held, fresh = _leaf(1, name="Old Deck.pdf"), _leaf(2, name="Q3 Roadmap.pdf", path=("Fixture Share", "Decks"))
    ticket = json.loads((cap / "ticket.json").read_text())
    ticket["known"] = [{"resource": held["view_url"], "harvested_at": "2026-09-01T00:00:00Z"}]
    (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    (cap / "tree.json").write_text(json.dumps({"leaves": [held, fresh]}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))

    code, out, err = _run_script("harvest_share.py", rel, "--plan-only", cwd=wiki)  # from the wiki root, as `run` does
    assert code == 0, err
    assert json.loads(out)["planned"] == 1 and not (cap / "report.json").exists()
    (planned,) = json.loads((cap / "plan.json").read_text())["leaves"]
    leaf_dir = wiki / planned["dir"]
    assert leaf_dir.parent == cap.parent and leaf_dir != cap

    leaf_dir.mkdir()
    shutil.copy(FIXTURES / "deck.pdf", leaf_dir / "document.pdf")
    (leaf_dir / "meta.json").write_text(json.dumps({
        "url": fresh["view_url"], "title": "Q3 Roadmap.pdf - Fixture Share", "name": fresh["name"], "kind": "document", "bytes": 614,
    }), encoding="utf-8")
    code, out, err = _run_script(
        "frameio_doc_note.py", planned["dir"], "--slug", job.slug, "--path", "Fixture Share", "--path", "Decks",
        "--title-strip", " - Fixture Share", "--author", "Ada Lovelace", cwd=wiki,
    )
    assert code == 0, err
    body = (leaf_dir / "page.md").read_text(encoding="utf-8")
    assert not body.startswith("---") and "\n---" not in body

    # Out of budget on purpose: the leaf is already on disk, so this pass only counts it and reports.
    code, out, err = _run_script("harvest_share.py", rel, "--budget-seconds", "-1", cwd=wiki)
    assert code == 0, err
    report = json.loads((cap / "report.json").read_text())
    assert report["outcome"] == "ok" and report["ticket"] == ticket["ticket"]
    assert report["captured"] == [{"item": fresh["view_url"], "dir": planned["dir"], "title": "Q3 Roadmap.pdf"}]

    (page,) = extracted(ops, env, wiki, wiki / report["captured"][0]["dir"])
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest)
    assert text.count("\n---\n") == 1 and text.startswith("---\n"), "exactly the extractor's own frontmatter block"
    front, _, rendered = text[4:].partition("\n---\n")
    assert "title: Q3 Roadmap.pdf" in front and "status: draft" in front and fresh["view_url"] in front
    assert "# Q3 Roadmap.pdf" in rendered and "*Decks*" in rendered
    assert "- **Type:** doc" in rendered and "- **Author:** Ada Lovelace" in rendered
    assert f"`{planned['dir']}/document.pdf`" in rendered and "`Q3 Roadmap.pdf` (pdf, 614 bytes)" in rendered
    assert "> [!note]- Extracted text" in rendered
    if importlib.util.find_spec("pypdf"):  # the script's own PEP 723 dependency; absent under a bare interpreter
        assert "Quarterly roadmap for the fixture share" in rendered
    else:
        assert "(extraction failed: ModuleNotFoundError" in rendered


def test_a_share_video_becomes_a_page_waiting_for_its_transcript(ops, env, wiki, monkeypatch, capsys):
    job = declared_job(ops, env, wiki, UNIT, SHARE, "dest=sources/scrapes/port-frameio")
    url = f"{SHARE}/view/vid00002"
    leaf_dir = wiki / "_raw" / job.slug / f"keynote--{hashlib.sha1(url.encode()).hexdigest()[:8]}"
    code, _calls, _io = _capture_leaf(monkeypatch, capsys, wiki, leaf_dir, url, "video", "--slug", job.slug, "--name", "Keynote.mov")
    assert code == 0

    (page,) = extracted(ops, env, wiki, leaf_dir)
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest) and "Real Title - Fixture Share" in text and url in text
    assert f"_raw/{job.slug}/{leaf_dir.name}/video.mp4" in text, "the page names the media the transcriber is owed"
