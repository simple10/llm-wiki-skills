"""channel-frameio, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-frameio/tests/test_frameio.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import hashlib
import json
import os
import pytest
import re
import shutil
import stat
import sys
import tempfile

from pathlib import Path

from harness import declared_job, ticket_in, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-frameio", "test_frameio"))


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


def test_a_share_document_becomes_a_page_under_the_jobs_dest(ops, env, wiki, monkeypatch, capsys, tmp_path):
    """Both steps, in order. HARVEST plans the share and captures the one leaf
    it still owes — bytes, no page — and writes the report `apply` reads. Then
    the unit's own PROCESS step over that leaf's capture dir writes ONE page
    under the ticket's `dest`, through the REAL `page create`."""
    job = declared_job(ops, env, wiki, UNIT, SHARE, "dest=sources/scrapes/port-frameio")
    assert (job.record["harvest"]["scope"], job.record["harvest"]["assets"]) == ("domain", "download")  # the manifest's shipped defaults
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

    record = _harvested(monkeypatch, capsys, wiki, planned, job.slug, "--title-strip= - Fixture Share",
                        "--author=Ada Lovelace", title="Q3 Roadmap.pdf - Fixture Share")
    assert record["body"] == "document.pdf" and record["content_type"] == "application/pdf"
    assert not (leaf_dir / "page.md").exists(), "harvest renders no page"

    # Out of budget on purpose: the leaf is already on disk, so this pass only counts it and reports.
    code, out, err = _run_script("harvest_share.py", rel, "--budget-seconds", "-1", cwd=wiki)
    assert code == 0, err
    report = json.loads((cap / "report.json").read_text())
    assert report["outcome"] == "ok" and report["ticket"] == ticket["ticket"]
    assert report["captured"] == [{"item": fresh["view_url"], "dir": planned["dir"], "title": "Q3 Roadmap.pdf"}]
    assert report["written"] == [], "a harvest report claims no page"

    # PROCESS: one ticket per captured dir, and this is what the unit does with it.
    code, out, err = _process(wiki, planned["dir"], job.dest, env=_front_door(tmp_path, ops, env))
    assert code == 0, err + out
    page = wiki / json.loads(out)["written"][0]
    assert json.loads((leaf_dir / "report.json").read_text())["written"] == [str(page.relative_to(wiki))]

    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest) and page.name == "Q3 Roadmap.pdf.md"
    assert text.count("\n---\n") == 1 and text.startswith("---\n"), "exactly one frontmatter block, the host's own"
    front, _, rendered = text[4:].partition("\n---\n")
    assert "title: Q3 Roadmap.pdf" in front and "status: draft" in front and fresh["view_url"] in front
    assert "extracted: 'true'" in front or "extracted: true" in front, front
    # The two leaves' top folders differ, so the driver kept both: `--crumb-skip=0`.
    assert "# Q3 Roadmap.pdf" in rendered and "*Fixture Share / Decks*" in rendered
    assert "- **Type:** doc" in rendered and "- **Author:** Ada Lovelace" in rendered
    assert "`Q3 Roadmap.pdf` (pdf, 614 bytes)" in rendered
    assert "_raw/" not in rendered, "a committed page names the captured file and never points into `_raw/`"
    assert "> [!note]- Extracted text" in rendered
    # WHAT the callout holds is `test_a_pdfs_text_reaches_the_page_…`'s: this interpreter has no pypdf.

    # A second process ticket over the same bytes replaces the page, and does not refuse.
    code, out, err = _process(wiki, planned["dir"], job.dest, env=_front_door(tmp_path, ops, env))
    assert code == 0, err + out
    assert json.loads(out)["written"] == [str(page.relative_to(wiki))]
    assert page.read_text(encoding="utf-8").count("# Q3 Roadmap.pdf") == 1


def test_an_explicit_reference_on_the_job_reaches_the_plan(ops, env, wiki, monkeypatch, capsys, tmp_path):
    """The operator's `harvest.assets=reference` rides the job record into the
    ticket, and the driver's `--plan-only` leaves the media leaf out of
    `plan.json` — named under `unplanned` — while the document is planned."""
    job = declared_job(ops, env, wiki, UNIT, FOLDER, "dest=sources/scrapes/port-frameio-reference", "harvest.assets=reference", slug="port-frameio-reference")
    assert job.record["harvest"]["assets"] == "reference"
    cap = ticket_in(wiki, job, "share-0000--e2eref001", unit=UNIT, item=FOLDER)
    video, deck = _leaf(1, name="Keynote.mov"), _leaf(2, name="Deck.pdf")
    (cap / "tree.json").write_text(json.dumps({"leaves": [video, deck]}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))

    code, out, err = _run_script("harvest_share.py", rel, "--plan-only", cwd=wiki)
    assert code == 0, err
    summary = json.loads(out)
    assert (summary["planned"], summary["skipped"]["reference"]) == (1, 1), summary
    plan = json.loads((cap / "plan.json").read_text())
    assert [leaf["item"] for leaf in plan["leaves"]] == [deck["view_url"]]
    assert plan["unplanned"] == [{"item": video["view_url"], "name": "Keynote.mov", "why": "reference"}]


def test_two_assets_with_one_name_land_as_two_pages(ops, env, wiki, monkeypatch, capsys, tmp_path):
    """A page is filed under its title and the second write wins: before the
    driver settled titles, `Brief.pdf` in a second folder WAS the first one's
    page, and both process tickets said ok."""
    share = "https://next.frame.io/share/22222222-2222-2222-2222-222222222222"
    job = declared_job(ops, env, wiki, UNIT, share, "dest=sources/scrapes/port-frameio-names", slug="port-channel-frameio-names")
    cap = ticket_in(wiki, job, "share-2222--e2e00002", unit=UNIT, item=share)
    leaves = []
    for n, folder in ((1, "Client A"), (2, "Client B")):
        asset = f"{n:08d}-eeee-bbbb-cccc-dddddddddddd"
        leaves.append({"asset_id": asset, "name": "Brief.pdf", "path": ["Fixture Share", folder], "view_url": f"{share}/view/{asset}"})
    (cap / "tree.json").write_text(json.dumps({"leaves": leaves}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))
    code, out, err = _run_script("harvest_share.py", rel, "--plan-only", cwd=wiki)
    assert code == 0, err
    planned = json.loads((cap / "plan.json").read_text())["leaves"]
    assert len(planned) == 2 and len({leaf["dir"] for leaf in planned}) == 2

    for leaf in planned:  # harvested for real, each into its own dir
        _harvested(monkeypatch, capsys, wiki, leaf, job.slug, "--title-strip= - Fixture Share",
                   title="Brief.pdf - Fixture Share")

    reports = []
    for _ in range(2):  # the driver is re-run pass after pass: same names
        code, out, err = _run_script("harvest_share.py", rel, "--budget-seconds", "-1", cwd=wiki)
        assert code == 0, err
        reports.append(json.loads((cap / "report.json").read_text()))
    report = reports[-1]
    assert report["outcome"] == "ok"

    # One process ticket per captured dir, each run as the SKILL runs it.
    front = _front_door(tmp_path, ops, env)
    pages = []
    for entry in report["captured"]:
        code, out, err = _process(wiki, entry["dir"], job.dest, env=front)
        assert code == 0, err + out
        pages.append(wiki / json.loads(out)["written"][0])
    assert len({page.resolve() for page in pages}) == 2 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    assert [page.name for page in pages] == ["Brief.pdf.md", "Brief.pdf (Client B).md"]
    assert [[c["title"] for c in r["captured"]] for r in reports] == [["Brief.pdf", "Brief.pdf (Client B)"]] * 2
    first, second = (page.read_text(encoding="utf-8") for page in pages)
    assert leaves[0]["view_url"] in first and "*Client A*" in first  # still the FIRST asset's page
    assert leaves[1]["view_url"] in second and "*Client B*" in second


def test_a_share_video_becomes_a_page_waiting_for_its_transcript(ops, env, wiki, monkeypatch, capsys, tmp_path):
    """The unit's own process step mints the transcribe stage's stub — and the
    REAL walker finds it. `page create` takes `extracted=` and `media=` as
    ordinary keys, so no unit needs the generic extractor for a media body."""
    job = declared_job(ops, env, wiki, UNIT, SHARE, "dest=sources/scrapes/port-frameio")
    url = f"{SHARE}/view/vid00002"
    leaf_dir = wiki / "_raw" / job.slug / f"keynote--{hashlib.sha1(url.encode()).hexdigest()[:8]}"
    code, _calls, _io = _capture_leaf(monkeypatch, capsys, wiki, leaf_dir, url, "video", "--slug", job.slug, "--name", "Keynote.mov")
    assert code == 0
    rel = str(leaf_dir.relative_to(wiki))

    code, out, err = _process(wiki, rel, job.dest, env=_front_door(tmp_path, ops, env))
    assert code == 0, err + out
    page = wiki / json.loads(out)["written"][0]
    assert json.loads((leaf_dir / "report.json").read_text())["written"] == [str(page.relative_to(wiki))]

    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest) and page.name == "Real Title - Fixture Share.md"
    assert "extracted: queued" in text and url in text
    assert "type: video" in text, "the note format requires a type, and a video stub is not a `note`"
    assert f"media: {rel}/video.mp4" in text, "the page names the media the transcriber is owed"
    assert text.rstrip().endswith("---"), "an empty body: the transcript is what fills it"

    # The stub is an ITEM to the transcribe stage's own walker, not just a page.
    assert _queued_media(wiki, job.dest) == [(str(page.relative_to(wiki)), f"{rel}/video.mp4")]


def test_a_pdfs_text_reaches_the_page_when_the_renderer_runs_as_production_runs_it(ops, env, wiki, tmp_path):
    """Under the test interpreter there is no pypdf, the callout reads
    `(extraction failed: ModuleNotFoundError…)` — and a test that accepted that
    text verified nothing. Run the way production runs it, or not at all."""
    why = _uv_cannot_run_the_renderer()
    if why:
        pytest.skip(why)
    share = "https://next.frame.io/share/33333333-3333-3333-3333-333333333333"
    job = declared_job(ops, env, wiki, UNIT, share, "dest=sources/scrapes/port-frameio-pdf", slug="port-channel-frameio-pdf")
    url = f"{share}/view/00000001-ffff-bbbb-cccc-dddddddddddd"
    leaf_dir = wiki / "_raw" / job.slug / f"deck--{hashlib.sha1(url.encode()).hexdigest()[:8]}"
    leaf_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "deck.pdf", leaf_dir / "document.pdf")
    (leaf_dir / "meta.json").write_text(json.dumps(
        {"url": url, "title": "Deck", "name": "Deck.pdf", "kind": "document", "bytes": 614, "path": [], "crumb_skip": 0},
    ), encoding="utf-8")
    (leaf_dir / "capture.json").write_text(json.dumps(
        {"v": 1, "slug": job.slug, "item": url, "title": "Deck", "body": "document.pdf",
         "content_type": "application/pdf", "fetched_at": "2026-09-19T00:00:00Z"},
    ), encoding="utf-8")
    code, out, err = _process(
        wiki, str(leaf_dir.relative_to(wiki)), job.dest,
        env=_front_door(tmp_path, ops, env), runner=_render_as_production_does,
    )
    assert code == 0, err
    assert json.loads(out)["extracted_chars"] > 0

    page = wiki / json.loads(out)["written"][0]
    text = page.read_text(encoding="utf-8")
    assert "> **Page 1**" in text and "Quarterly roadmap for the fixture share" in text
    assert "extraction failed" not in text
    assert "type: doc" in text, "the note format requires a type, and a document is not a `note`"


# ---- Rule 1: the title names a FILE ----------------------------------------------------
def test_titles_no_filename_can_hold_still_land_as_pages(ops, env, wiki, monkeypatch, capsys, tmp_path):
    """Harvest said ok and the page never landed: `page create` names the FILE
    from the title and refuses `: ? / "`, a leading dot — and the filesystem
    refuses 300 bytes. The H1 keeps the venue's own title."""
    share = "https://next.frame.io/share/44444444-4444-4444-4444-444444444444"
    job = declared_job(ops, env, wiki, UNIT, share, "dest=sources/scrapes/port-frameio-titles", slug="port-channel-frameio-titles")
    cap = ticket_in(wiki, job, "share-4444--e2e00004", unit=UNIT, item=share)
    leaves = []
    for n, (name, _safe) in enumerate(HOSTILE_TITLES, 1):
        for folder in ("Client A", "Client B"):  # and a namesake each, so the qualifier is in play too
            asset = f"{n:08d}-{folder[-1].lower() * 4}-bbbb-cccc-dddddddddddd"
            leaves.append({"asset_id": asset, "name": name, "path": [folder], "view_url": f"{share}/view/{asset}"})
    (cap / "tree.json").write_text(json.dumps({"leaves": leaves}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))
    code, out, err = _run_script("harvest_share.py", rel, "--plan-only", cwd=wiki)
    assert code == 0, err
    planned = json.loads((cap / "plan.json").read_text())["leaves"]
    for leaf in planned:
        _harvested(monkeypatch, capsys, wiki, leaf, job.slug, title=leaf["name"])
    code, out, err = _run_script("harvest_share.py", rel, "--budget-seconds", "-1", cwd=wiki)
    assert code == 0, err
    report = json.loads((cap / "report.json").read_text())
    assert report["outcome"] == "ok" and len(report["captured"]) == 6

    rec = _module("capture_record")
    front = _front_door(tmp_path, ops, env)
    pages = []
    for entry in report["captured"]:
        code, out, err = _process(wiki, entry["dir"], job.dest, env=front)
        assert code == 0, err + out
        pages.append(wiki / json.loads(out)["written"][0])
    assert len({page.resolve() for page in pages}) == 6, "every asset its own page"
    for (name, safe), first, second, leaf in zip(
        [t for t in HOSTILE_TITLES for _ in (0, 1)][::2], pages[::2], pages[1::2], planned[::2], strict=True
    ):
        assert first.name == f"{safe or rec.safe_title(name)}.md"
        assert second.name.endswith(" (Client B).md") and len(second.name.encode()) <= 255
        text = first.read_text(encoding="utf-8")
        assert f"# {name}\n" in text, "the H1 is the venue's own title"
        record = json.loads((wiki / leaf["dir"] / "capture.json").read_text())
        meta = json.loads((wiki / leaf["dir"] / "meta.json").read_text())
        assert meta["title"] == name and record["title"] == first.stem


@pytest.mark.parametrize("bundle, under_dest", [(True, True), (False, False)])
def test_bundle_media_decides_where_the_stub_points(ops, env, wiki, monkeypatch, capsys, tmp_path, bundle, under_dest):
    """`process.bundle_media` is on the ticket and the host no longer acts on
    it: the unit writes this page, so the copy is the unit's. True puts the
    media under `<dest>/assets/` beside the page; false leaves it in `_raw`."""
    job = declared_job(ops, env, wiki, UNIT, SHARE, "dest=sources/scrapes/port-frameio")
    url = f"{SHARE}/view/bundle{int(bundle)}"
    leaf_dir = wiki / "_raw" / job.slug / f"bundled{int(bundle)}--{hashlib.sha1(url.encode()).hexdigest()[:8]}"
    code, _calls, _io = _capture_leaf(monkeypatch, capsys, wiki, leaf_dir, url, "video",
                                      "--slug", job.slug, "--name", f"Bundle{int(bundle)}.mov")
    assert code == 0
    rel = str(leaf_dir.relative_to(wiki))
    ticket = json.loads((leaf_dir / "ticket.json").read_text()) if (leaf_dir / "ticket.json").exists() else {}
    ticket["process"] = {"embeds": True, "bundle_media": bundle, "on_change": "replace", "exclude_rules": []}
    (leaf_dir / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")

    code, out, err = _process(wiki, rel, job.dest, env=_front_door(tmp_path, ops, env))
    assert code == 0, err + out
    page = wiki / json.loads((leaf_dir / "report.json").read_text())["written"][0]
    named = re.search(r"^media: (.+)$", page.read_text(encoding="utf-8"), re.M).group(1)
    assert (wiki / named).is_file(), named
    assert named.startswith(f"{job.dest}/assets/") is under_dest, named
    assert named.startswith("_raw/") is not under_dest, named
