"""channel-hubspot-video on the rebuilt worker contract, in its two steps.

HARVEST is bytes: `leaves.py plan` filters the enumeration and names one
capture directory per page, `leaves.py record` writes the flat `capture.json`
naming the rendered `page.html`, and `leaves.py report` writes the report
`apply` reads. PROCESS is this unit's own: the worker converts that
`page.html` with the SITE's selectors — which live in the unit's
`references/sites.json` because nothing reads a manifest `extract` block any
more — and writes the pages through `llm-wiki-ops page create`.

The pure logic is imported and runs everywhere. The end-to-end cases write
into a real wiki through the real CLI and skip where none is at hand. Nothing
here touches the network: the rendered page is a fixture.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT, at, declared_job, run, ticket_in

UNIT = "channel-hubspot-video"
SCRIPTS = ROOT / "skills" / UNIT / "scripts"
LEAVES = SCRIPTS / "leaves.py"
CAPTURE = SCRIPTS / "capture_hubspot_video.py"
FIX = Path(__file__).resolve().parent / "fixtures" / "hubspot"

SECTION = "https://www.example-hubspot.invalid/learn"
LESSON = "https://www.example-hubspot.invalid/learn/offers/lesson-two"
STREAM = "https://stream.mux.com/AbCdEfGhIjKlMnOpQrStUvWx0123456789.m3u8"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


leaves = _load(LEAVES)
capturer = _load(CAPTURE)  # Playwright is imported inside `render`, so this loads anywhere


def _run(*args, cwd=None, env=None):
    """The unit's harvest script from the WORKING TREE, its PEP 723 deps
    resolved by uv exactly as `llm-wiki-ops run` would."""
    return subprocess.run(["uv", "run", "-q", "--script", str(LEAVES), *args], capture_output=True, text=True, cwd=cwd, env=env, check=False)


def _documented(root: Path, *args, env=None):
    """THE DOCUMENTED WAY: `llm-wiki-ops run` starts a script at the WIKI ROOT
    (`commands/run/run.py::_exec`), and every path the SKILL.md passes is the
    ticket's wiki-relative `capture_dir`. Never an absolute path."""
    assert not any(str(arg).startswith("/") for arg in args), args
    return _run(*args, cwd=root, env=env)


def _hash8(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:8]


# ------------------------------------------------------------ pure logic


def test_hslang_and_fragments_fold_to_one_url():
    assert leaves.normalize_url("https://WWW.Example.com/learn/a/?hsLang=en#top") == "https://www.example.com/learn/a"
    assert leaves.normalize_url("https://x.example/a?page=2&hsLang=en") == "https://x.example/a?page=2"
    assert leaves.normalize_url("https://x.example/a?utm_source=n&b=1", ["utm_source"]) == "https://x.example/a?b=1"
    assert leaves.normalize_url("https://x.example") == "https://x.example/"


def test_the_leaf_name_is_the_hosts_own_shape():
    """`<slugified path>--<first 8 hex of sha1(item)>`, as `pipeline/jobs.py::capture_dir_for` makes it."""
    assert leaves.leaf_dir("site-learn", LESSON) == f"_raw/site-learn/learn-offers-lesson-two--{_hash8(LESSON)}"
    root = "https://www.example-hubspot.invalid/"
    assert leaves.leaf_name(root) == f"www-example-hubspot-invalid--{_hash8(root)}"
    long = "https://x.example/" + "very-long-segment/" * 10
    assert len(leaves.leaf_name(long).rsplit("--", 1)[0]) <= 60


def test_section_scope_is_a_path_prefix_not_a_string_prefix():
    assert leaves.in_scope(LESSON, SECTION, "section")
    assert leaves.in_scope(SECTION, SECTION, "section")
    assert leaves.in_scope("https://example-hubspot.invalid/learn/x", SECTION, "section")  # www is not a second host
    assert not leaves.in_scope("https://www.example-hubspot.invalid/learning-is-not-learn", SECTION, "section")
    assert not leaves.in_scope("https://other.example.invalid/learn/x", SECTION, "section")
    assert leaves.in_scope("https://www.example-hubspot.invalid/blog/a", SECTION, "domain")
    assert not leaves.in_scope(LESSON, SECTION, "page")


def _plan(**over):
    pages, children = leaves.parse_urls((FIX / "sitemap.xml").read_text(encoding="utf-8"))
    assert children == []
    args = dict(slug="site-learn", target=SECTION, item=SECTION, capture_dir=f"_raw/site-learn/learn--{_hash8(SECTION)}", scope="section")
    args.update(over)
    return leaves.plan_leaves(pages, **args)


def test_the_plan_applies_scope_exclusions_min_date_and_known():
    plan = _plan(
        exclude_urls=["*/learn/legacy/*", "*hsLang=*"],
        min_date="2026-01-01",
        known=[{"resource": "https://www.example-hubspot.invalid/learn/offers/lesson-one?hsLang=en", "harvested_at": "2026-08-30T09:12:04Z"}],
    )
    assert [leaf["item"] for leaf in plan["leaves"]] == [LESSON, SECTION]  # newest first
    why = {row["url"]: row["why"] for row in plan["skipped"]}
    assert why["https://www.example-hubspot.invalid/learn/offers/lesson-one"] == "known"  # known[] is matched after hsLang is stripped
    assert why["https://www.example-hubspot.invalid/learn/offers/lesson-three"] == "older_than_min_date"
    assert why["https://www.example-hubspot.invalid/learn/legacy/old-lesson"] == "excluded"
    assert why["https://www.example-hubspot.invalid/learning-is-not-learn"] == "scope"
    assert why["https://other.example.invalid/learn/elsewhere"] == "scope"
    # S11: a url a shell would read as a command, and one that is not http(s), are DROPPED with a why —
    # they never become a leaf, so nothing downstream can be handed them.
    assert why["https://www.example-hubspot.invalid/learn/offers/x;$(touch${IFS}PWNED)"] == "unsafe_url"
    assert why["javascript:alert(1)"] == "unsafe_url"
    assert all(leaves.safe_url(leaf["item"]) for leaf in plan["leaves"])
    # The `*hsLang=*` exclusion the old docs asked for costs no page: the
    # parameter is stripped before the globs are read, and the two spellings are one leaf.
    assert sum(1 for leaf in plan["leaves"] if "lesson-two" in leaf["item"]) == 1


def test_the_tickets_own_item_keeps_the_tickets_own_directory():
    plan = _plan()
    by_item = {leaf["item"]: leaf["dir"] for leaf in plan["leaves"]}
    assert by_item[SECTION] == f"_raw/site-learn/learn--{_hash8(SECTION)}"
    assert by_item[LESSON] == f"_raw/site-learn/learn-offers-lesson-two--{_hash8(LESSON)}"
    assert all(len(d.split("/")) == 3 for d in by_item.values())  # `apply.py::CAPTURE_DEPTH`


def test_a_limit_keeps_the_newest_and_says_what_it_left():
    plan = _plan(limit=1)
    assert [leaf["item"] for leaf in plan["leaves"]] == ["https://www.example-hubspot.invalid/learn/legacy/old-lesson"]
    assert {row["why"] for row in plan["skipped"]} >= {"over_limit"}


def test_page_scope_is_the_target_alone_even_with_nothing_enumerated():
    plan = leaves.plan_leaves([], slug="s", target=LESSON, item=LESSON, capture_dir="_raw/s/x--00000000", scope="page")
    assert plan["leaves"] == [{"item": LESSON, "dir": "_raw/s/x--00000000", "lastmod": None}]


def test_a_sitemap_index_yields_children_not_pages():
    index = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://x.example/sitemap-1.xml</loc></sitemap></sitemapindex>'
    assert leaves.parse_urls(index) == ([], ["https://x.example/sitemap-1.xml"])
    assert leaves.parse_urls('["https://x.example/a", {"url": "https://x.example/b", "lastmod": "2026-07-01T00:00:00Z"}]')[0] == [
        {"url": "https://x.example/a", "lastmod": None}, {"url": "https://x.example/b", "lastmod": "2026-07-01"}]
    assert leaves.parse_urls("https://x.example/a\n\nhttps://x.example/b\n")[0][1]["url"] == "https://x.example/b"


def test_site_rules_are_found_by_host():
    sites = json.loads((FIX / "sites.json").read_text(encoding="utf-8"))
    assert leaves.site_rules(sites, "www.example-hubspot.invalid")["title_selector"] == "main#main-content h2"
    assert leaves.site_rules(sites, "example-hubspot.invalid")["content_selector"] == "main#main-content"
    assert leaves.site_rules(sites, "nobody.example") == {}
    assert leaves.site_rules({"sites": {"*": {"content_selector": "main"}}}, "nobody.example") == {"content_selector": "main"}


def test_the_shipped_sites_file_is_an_empty_template():
    """The unit is a platform template: it ships the FILE a script reads, and no site's selectors."""
    assert leaves.load_sites(None) == {"v": 1, "sites": {}}


def test_harvest_records_bytes_and_nothing_a_host_verb_owns():
    """`capture.json` is FLAT: what was fetched and the file it is in. The page,
    its frontmatter and its identity are the process step's."""
    record = leaves.capture_record(slug="s", item=LESSON, title="Pricing the offer", body="page.html",
                                   content_type="text/html", fetched_at="2026-07-15T09:00:00Z")
    assert record == {"slug": "s", "item": LESSON, "title": "Pricing the offer", "body": "page.html",
                      "content_type": "text/html", "fetched_at": "2026-07-15T09:00:00Z"}
    for owned in ("frontmatter", "status", "resource", "harvested", "extracted", "document_id", "document_revision"):
        assert owned not in record, owned


def test_the_report_names_its_outcome_from_what_landed():
    planned = [{"item": "u1", "dir": "_raw/s/a--1"}, {"item": "u2", "dir": "_raw/s/b--2"}]
    got = [{"item": "u1", "dir": "_raw/s/a--1", "title": "A"}]
    both = [*got, {"item": "u2", "dir": "_raw/s/b--2", "title": None}]
    report = leaves.build_report(ticket="t1", planned=planned, captured=both, skipped=[], missing=[])
    assert (report["outcome"], report["reason"], report["captured"]) == ("ok", None, both)
    assert set(report) == {"v", "ticket", "outcome", "reason", "captured", "written", "missing", "discovered"}
    assert report["discovered"] == [] and report["written"] == []  # `discovered[]` does nothing for pages now
    partial = leaves.build_report(ticket="t1", planned=planned, captured=got, skipped=[], missing=[])
    assert partial["outcome"] == "partial" and "1 of 2" in partial["reason"]
    denied = [{"host": "stream.mux.com", "url": STREAM, "why": "denied"}]
    assert leaves.build_report(ticket="t1", planned=planned, captured=both, skipped=[], missing=denied)["outcome"] == "partial"
    known = leaves.build_report(ticket="t1", planned=[], captured=[], skipped=[{"url": "u1", "why": "known"}], missing=[])
    assert known["outcome"] == "skipped" and "known: 1" in known["reason"]
    assert leaves.build_report(ticket="t1", planned=planned, captured=[], skipped=[], missing=[])["outcome"] == "failed"
    assert leaves.build_report(ticket="t1", planned=[], captured=[], skipped=[], missing=[])["outcome"] == "failed"
    forced = leaves.build_report(ticket="t1", planned=planned, captured=got, skipped=[], missing=[], reason="auth_expired:x", failed=True)
    assert forced["outcome"] == "failed" and forced["captured"] == [] and forced["reason"] == "auth_expired:x"


def test_a_process_report_names_its_pages_and_captures_nothing():
    """A process ticket rewrites `ticket.json` long after harvest wrote
    `capture.json`, so a capture-freshness rule would refuse every honest
    process report; the report is about `written[]` instead."""
    pages = ["sources/courses/s/Lesson.md", "sources/courses/s/Lesson (video).md"]
    report = leaves.process_report(ticket="t1", written=pages)
    assert (report["outcome"], report["written"], report["captured"]) == ("ok", pages, [])
    assert set(report) == {"v", "ticket", "outcome", "reason", "captured", "written", "missing", "discovered"}
    skipped = leaves.process_report(ticket="t1", written=[], skipped=True, reason="excluded")
    assert (skipped["outcome"], skipped["reason"]) == ("skipped", "excluded")
    nothing = leaves.process_report(ticket="t1", written=[])
    assert nothing["outcome"] == "failed" and nothing["reason"]


def test_patch_assets_runs_with_no_browser_installed(tmp_path):
    """`patch-assets` is pure JSON, so the Playwright import is `render`'s alone."""
    manifest = tmp_path / "assets.json"
    manifest.write_text(json.dumps([
        {"type": "image", "src_url": "https://www.example-hubspot.invalid/hubfs/a.png"},
        {"type": "image", "src_url": "https://verifi.podscribe.com/tag?x=1"},
        {"type": "hls", "src_url": "https://manifest-gcp-us-east1.edgemv.mux.com/abc/rendition.m3u8?expires=1"},
    ]), encoding="utf-8")
    done = subprocess.run(["python3", str(CAPTURE), "patch-assets", str(manifest), "--meta", str(FIX / "meta.json")], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    kept = json.loads(manifest.read_text(encoding="utf-8"))
    assert [a["src_url"] for a in kept] == ["https://www.example-hubspot.invalid/hubfs/a.png", STREAM]
    assert kept[-1]["type"] == "hls" and kept[-1]["embed_url"].startswith("https://play.hubspotvideo.com/")


# ------------------------------------------------------------ files only, no CLI


def _fake_wiki(tmp_path: Path, *, known=(), exclude=()) -> tuple[Path, Path]:
    """A capture directory holding the `ticket.json` a spawner would have left, in a tree shaped like a wiki's."""
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = tmp_path / rel
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps({
        "v": 1, "ticket": "0123456789ab", "unit": UNIT, "slug": "site-learn", "item": SECTION, "target": SECTION,
        "capture_dir": rel, "dest": None, "hosts": ["www.example-hubspot.invalid"],
        "harvest": {"scope": "section", "access": "free", "max_age": None, "refresh": "never", "exclude_urls": list(exclude), "assets": "download"},
        "options": {}, "credential": None, "min_date": "2026-01-01", "known": list(known),
    }), encoding="utf-8")
    return tmp_path, cap


def _fill(leaf: Path) -> None:
    leaf.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "page.html", leaf / "page.html")
    shutil.copy(FIX / "meta.json", leaf / "meta.json")


def test_plan_record_report_from_a_ticket_with_files_only(tmp_path):
    root, cap = _fake_wiki(tmp_path, known=[{"resource": SECTION, "harvested_at": "2026-08-30T09:12:04Z"}])
    done = _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--sites", str(FIX / "sites.json"))
    assert done.returncode == 0, done.stderr
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    items = [leaf["item"] for leaf in plan["leaves"]]
    assert items == [LESSON, "https://www.example-hubspot.invalid/learn/offers/lesson-one"]
    why = {row["url"]: row["why"] for row in plan["skipped"]}
    assert why[SECTION] == "known"
    assert why["https://www.example-hubspot.invalid/learn/legacy/old-lesson"] == "excluded"  # the SITE's own exclusion, off sites.json

    lesson_dir = root / plan["leaves"][0]["dir"]
    _fill(lesson_dir)
    done = _run("record", str(cap), str(lesson_dir))
    assert done.returncode == 0, done.stderr
    # Harvest rendered nothing: the capture is the bytes the venue served.
    assert not (lesson_dir / "page.md").exists()
    assert json.loads((lesson_dir / "capture.json").read_text(encoding="utf-8"))["body"] == "page.html"

    done = _run("report", str(cap))
    assert done.returncode == 0, done.stderr
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["ticket"] == "0123456789ab" and report["outcome"] == "partial"  # one of two pages was captured
    # S5: what an operator does next, said where `queue show` prints it — a `once` job is never pulled again.
    assert "pipeline queue retry 0123456789ab" in report["reason"] and "pipeline edit site-learn every=" in report["reason"]
    assert report["captured"] == [{"item": LESSON, "dir": plan["leaves"][0]["dir"], "title": "Pricing the offer"}]
    assert report["discovered"] == []


def test_everything_known_is_a_skipped_report_not_a_failure(tmp_path):
    pages, _ = leaves.parse_urls((FIX / "sitemap.xml").read_text(encoding="utf-8"))
    known = [{"resource": page["url"], "harvested_at": None} for page in pages]
    _root, cap = _fake_wiki(tmp_path, known=known)
    assert _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml")).returncode == 0
    done = _run("report", str(cap))
    assert done.returncode == 0, done.stderr
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "skipped" and "known" in report["reason"] and report["captured"] == []


def test_a_hand_run_with_no_ticket_takes_the_job_as_flags(tmp_path):
    cap = tmp_path / "_raw" / "site-learn" / "learn--00000000"
    cap.mkdir(parents=True)
    refused = _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"))
    assert refused.returncode == 2 and "--slug" in refused.stderr
    done = _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--slug", "site-learn", "--target", SECTION, "--ticket", "feedfacecafe")
    assert done.returncode == 0, done.stderr
    assert _run("report", str(cap), "--failed", "--reason", "auth_expired:www.example-hubspot.invalid").returncode == 1
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert (report["ticket"], report["outcome"], report["reason"]) == ("feedfacecafe", "failed", "auth_expired:www.example-hubspot.invalid")


def test_the_converter_is_the_circle_units_copy_unchanged():
    """Units install one at a time, so each carries its own converter; the copies must not drift."""
    assert (SCRIPTS / "to_markdown.py").read_bytes() == (ROOT / "skills" / "channel-circle" / "scripts" / "to_markdown.py").read_bytes()


def test_no_doc_or_manifest_names_the_extract_block_any_more():
    """`skills doctor` fails a manifest carrying a key the contract does not name, and nothing read it."""
    manifest = json.loads((ROOT / "skills" / UNIT / "manifest.json").read_text(encoding="utf-8"))
    assert "extract" not in manifest
    for name in ("SKILL.md", "INSTALL.md"):
        text = (ROOT / "skills" / UNIT / name).read_text(encoding="utf-8")
        assert '"extract"' not in text and "fill the manifest" not in text, name  # no doc shows or asks for the key
        assert "references/sites.json" in text, name


def test_the_stages_speak_the_new_contract():
    text = (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r'^argument-hint: "ticket=<id> stage=harvest\|process"$', text, re.M)
    for gone in ("<job.", "harvest_apply", "out_of_scope`", "scaffold", "intake.py", "job.py", "watch.py", "drain_pending"):
        assert gone not in text, gone
    for kept in ("ticket.json", "capture.json", "report.json", "data-hsv-src", "stream.mux.com", "verifi.podscribe.com",
                 "$ARGUMENTS", "stage=harvest|process", "### harvest", "### process", "page create", "to_markdown.py"):
        assert kept in text, kept
    # Harvest is bytes: no page is rendered there, and no body is one.
    harvest = text.split("### harvest", 1)[1].split("### process", 1)[0]
    assert "page.md" not in harvest and "page create" not in harvest
    # One sentence settles the step, and the reasoning stays in its one home.
    opening = text.split("## Stages", 1)[1].split("### harvest", 1)[0]
    assert " ".join(opening.split()) == "`stage=` in `$ARGUMENTS` is the step, `harvest` or `process`; the two sections below are those steps."


def test_the_skill_stays_under_its_budget():
    """A skill is a command sequence, and this one is a platform template an
    installing agent reads end to end."""
    assert len((ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8").splitlines()) <= 204


def test_every_script_this_unit_ships_is_named_where_a_worker_would_run_it():
    """A script no SKILL.md line runs, and no sibling runs, is dead surface."""
    skill = (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8")
    runs = set(re.findall(r"llm-wiki-ops run \S*/([\w-]+\.py)", skill))
    siblings = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS.glob("*.py"))
    for script in sorted(SCRIPTS.glob("*.py")):
        named = script.name in runs or re.search(rf"\b{re.escape(script.name)}\b", siblings.replace(script.read_text(encoding="utf-8"), ""))
        assert named, f"{script.name} is named on no `llm-wiki-ops run` line and no sibling runs it"


def test_the_quirks_log_holds_venue_facts_and_nothing_else():
    """The log is what this VENUE does, dated. Facts about the plugin, the
    harness or the port belong in the change record, not here."""
    log = (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8").split("## Quirks log", 1)[1]
    assert "2026-07-31" in log  # the oldest entry is still there
    for stale in ("user-invocable", "known[]", "key=value", "stage=", "Ported", "the port", "until the plugin", "Review fixes"):
        assert stale not in log, stale


# ------------------------------------------------------------ page names


def test_the_page_key_is_the_hosts_filename_rule_plus_what_a_filesystem_folds():
    """`page/note.py::filename_for` is `title.strip() + ".md"` and nothing else:
    outer whitespace is all the HOST folds; case is what a case-insensitive
    filesystem folds under it."""
    assert leaves.page_key("Introduction") == leaves.page_key(" Introduction\n") == leaves.page_key("INTRODUCTION")
    assert leaves.page_key("Introduction") != leaves.page_key("Introduction?")  # nothing else is dropped
    assert leaves.TITLE_ILLEGAL == '/\\:*?"<>|'  # `page/note.py::ILLEGAL` — a title carrying one is refused


def test_a_namesake_is_qualified_by_the_url_segment_that_tells_it_apart():
    def leaf(path, **over):
        return {"item": f"https://x.example{path}", **over}

    taken = {}
    first, second, third = leaf("/learn/module-1/intro"), leaf("/learn/module-2/intro"), leaf("/learn/module-2/intro?page=2")
    assert leaves.leaf_qualifiers(second) == ["intro", "module-2", "learn", _hash8(second["item"])]
    assert leaves.unique_title("Introduction ", leaves.leaf_qualifiers(first), taken) == "Introduction "  # the first: untouched
    assert leaves.unique_title("introduction", leaves.leaf_qualifiers(second), taken) == "introduction (module-2)"
    # Nothing in the path tells it apart (module-1's segments are the holder's, module-2 is taken): the hash.
    assert leaves.unique_title("Introduction", leaves.leaf_qualifiers(third), taken) == f"Introduction ({_hash8(third['item'])})"
    assert leaves.unique_title("Introduction", ["module-2"], taken) == "Introduction (module-2) (2)"
    # A segment never smuggles a character the host refuses into a title.
    escaped = leaf("/learn/what%3F/a%2Fb")
    assert leaves.leaf_qualifiers(escaped)[:2] == ["a/b", "what?"]
    assert leaves.unique_title("Introduction", leaves.leaf_qualifiers(escaped), taken) == "Introduction (a-b)"


def _held_leaf(root: Path, item: str, title, body="page.html", **over) -> dict:
    rel = f"_raw/site-learn/{leaves.leaf_name(item)}"
    (root / rel).mkdir(parents=True, exist_ok=True)
    (root / rel / body).write_text("x\n", encoding="utf-8")
    (root / rel / "capture.json").write_text(json.dumps({"item": item, "title": title, "body": body}), encoding="utf-8")
    return {"item": item, "dir": rel, "lastmod": None, **over}


def test_settling_titles_is_idempotent_and_leaves_the_first_leaf_alone(tmp_path):
    one, two = f"{SECTION}/module-1/intro", f"{SECTION}/module-2/intro"
    planned = [
        _held_leaf(tmp_path, one, "Intro"), _held_leaf(tmp_path, two, "Intro"),
        _held_leaf(tmp_path, f"{SECTION}/a", None), _held_leaf(tmp_path, f"{SECTION}/b", None),  # no title: filed as the body's stem
        {"item": f"{SECTION}/never-rendered", "dir": "_raw/site-learn/never--00000000", "lastmod": None},
    ]
    want = ["Intro", "Intro (module-2)", None, "page (b)"]
    for _ in range(2):
        leaves.settle_titles(tmp_path, planned)
        assert [json.loads((tmp_path / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))["title"] for leaf in planned[:-1]] == want
    # `record` run again writes the plain title back; the next report ends on the SAME names.
    (tmp_path / planned[1]["dir"] / "capture.json").write_text(json.dumps({"item": two, "title": "Intro", "body": "page.html"}), encoding="utf-8")
    leaves.settle_titles(tmp_path, planned)
    assert [json.loads((tmp_path / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))["title"] for leaf in planned[:-1]] == want


def test_a_qualifier_never_carries_a_settled_title_past_the_filename_cap(tmp_path):
    """`settle_titles` appends `(<url segment>)` and the process step a ` (video)`:
    the BASE is cut, the qualifier and the suffix kept."""
    base = leaves.safe_title("価" * 100)
    final = f"{base} ({'節' * 30})"
    fitted = leaves.fit_title(final, [base])
    assert len(fitted.encode()) <= leaves.FILENAME_TITLE_MAX_BYTES < len(final.encode())
    assert fitted.endswith(f"… ({'節' * 30})") and fitted.startswith("価")
    assert leaves.fit_title("Intro (module-2)", ["Intro"]) == "Intro (module-2)"  # under the cap: untouched
    # The cap leaves room for the transcript stub's own ` (video).md` too.
    assert len(f"{'x' * leaves.FILENAME_TITLE_MAX_BYTES} (video).md".encode()) <= 255

    seg = "%E7%AF%80" * 30
    one, two = f"{SECTION}/a/{seg}", f"{SECTION}/b/{seg}"
    planned = [_held_leaf(tmp_path, one, base), _held_leaf(tmp_path, two, base)]
    for _ in range(2):  # a second report renames nothing twice
        before = leaves.titles_on_disk(tmp_path, planned)
        leaves.settle_titles(tmp_path, planned)
        leaves.fit_titles(tmp_path, planned, before)
        titles = [json.loads((tmp_path / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))["title"] for leaf in planned]
        assert len({leaves.page_key(t) for t in titles}) == 2, titles
        assert all(len(f"{t} (video).md".encode()) <= 255 for t in titles), [len(t.encode()) for t in titles]
        assert titles[0] == base


# ------------------------------------------------------------ end to end, harvest then process


def _fresh(wiki: Path, slug: str) -> None:
    """The session wiki is shared, and a finished capture on disk is now a
    `landed` leaf: every end-to-end case starts from an empty `_raw/<slug>/`."""
    shutil.rmtree(wiki / "_raw" / slug, ignore_errors=True)


def _captured_lesson(ops, env, wiki, *extra, lesson=LESSON, section=SECTION, slug=None, meta_over=None):
    """One lesson harvested into a real wiki: the ticket a spawner would have
    left, a plan, the rendered fixture, and the flat capture."""
    job = declared_job(ops, env, wiki, UNIT, section, slug=slug)
    assert job.record["harvest"]["scope"] == "section"
    _fresh(wiki, job.slug)
    shutil.rmtree(wiki / job.dest, ignore_errors=True)
    cap = ticket_in(wiki, job, f"root--{_hash8(section)}", unit=UNIT, item=section)
    urls = cap / "urls.json"
    urls.write_text(json.dumps([{"url": lesson + "?hsLang=en", "lastmod": "2026-07-15"}]), encoding="utf-8")
    done = _run("plan", str(cap), "--urls", str(urls), "--sites", str(FIX / "sites.json"))
    assert done.returncode == 0, done.stderr
    (leaf,) = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"]
    assert leaf == {"item": lesson, "dir": f"_raw/{job.slug}/{leaves.leaf_name(lesson)}", "lastmod": "2026-07-15"}
    lesson_dir = wiki / leaf["dir"]
    _fill(lesson_dir)
    if meta_over:
        meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), **meta_over}
        (lesson_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    done = _run("record", str(cap), str(lesson_dir), *extra)
    assert done.returncode == 0, done.stderr
    return job, cap, lesson_dir


def test_the_downloaded_video_is_read_off_the_asset_manifest(tmp_path):
    """No flag needed: the asset `patch-assets` appended, once `assets.py download`
    marks it `downloaded`, is placed in the leaf — and `--no-media` declines it."""
    root, cap = _fake_wiki(tmp_path)
    (cap / "urls.json").write_text(json.dumps([LESSON]), encoding="utf-8")  # one page: a `--limit` that left others would be `partial`
    assert _run("plan", str(cap), "--urls", str(cap / "urls.json"), "--sites", str(FIX / "sites.json")).returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    store = root / "_raw" / "site-learn" / "assets"
    store.mkdir()
    (store / "0123456789ab-lesson.m4a").write_bytes(b"audio, by courtesy")
    manifest = [
        {"id": "asset-001", "type": "image", "src_url": "https://www.example-hubspot.invalid/hubfs/a.png", "status": "downloaded", "local_path": "../assets/a.png"},
        {"id": "asset-video-001", "type": "hls", "src_url": STREAM, "status": "pending", "local_path": None},
    ]
    (leaf / "assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert leaves.downloaded_media(leaf, json.loads((FIX / "meta.json").read_text(encoding="utf-8"))) is None  # pending is not downloaded

    manifest[1].update(status="downloaded", local_path="../assets/0123456789ab-lesson.m4a")
    (leaf / "assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert _run("record", str(cap), str(leaf), "--no-media").returncode == 0
    assert json.loads((leaf / "capture.json").read_text(encoding="utf-8"))["body"] == "page.html"
    assert not list(leaf.glob("media.*"))
    done = _run("record", str(cap), str(leaf))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["captured"]["media"] == "media.m4a"
    assert (leaf / "media.m4a").read_bytes() == b"audio, by courtesy"
    assert _run("report", str(cap)).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and len(report["captured"]) == 1


@pytest.mark.parametrize("bad", ["notes.txt", "missing.mp4"])
def test_a_media_file_the_transcriber_could_not_read_is_refused(tmp_path, bad):
    root, cap = _fake_wiki(tmp_path)
    assert _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml")).returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    done = _run("record", str(cap), str(leaf), "--media-file", str(tmp_path / bad))
    assert done.returncode == 2 and "not a media file" in done.stderr
    assert not (leaf / "capture.json").exists()  # refused before anything was written


# ------------------------------------------------------------ Rule 1: the title is a filename


def test_safe_title_is_a_title_the_hosts_filename_rule_holds():
    """`page/note.py::filename_for` refuses ILLEGAL, a control character and a leading dot, and checks no length."""
    assert leaves.safe_title('Lesson 3: What is "A/B" pricing?') == "Lesson 3 - What is ’A-B’ pricing"
    assert leaves.safe_title(".hidden <draft> | v2*") == "hidden (draft) - v2"
    assert leaves.safe_title("a\x00b\nc\td") == "a b c d"
    assert leaves.safe_title("") == leaves.safe_title(None) == leaves.safe_title(" .. ") == "Untitled"
    assert leaves.safe_title("", fallback="lesson-two") == "lesson-two"
    long = leaves.safe_title("x" * 300)
    assert len(long) == leaves.TITLE_MAX + 1 and long.endswith("…")
    cjk = leaves.safe_title("価" * 100)  # 300 bytes: the host lets it through and the write dies `File name too long`
    assert len(cjk.encode()) <= leaves.TITLE_MAX_BYTES + len("…".encode()) and cjk.endswith("…")
    for title in ('Lesson 3: What is "A/B" pricing?', ".hidden", "価" * 100, "a\nb"):
        safe = leaves.safe_title(title)
        assert not set(safe) & set(leaves.TITLE_ILLEGAL) and not safe.startswith(".") and all(ord(ch) >= 32 for ch in safe)
    assert leaves.safe_title("Pricing the offer") == "Pricing the offer"  # a legal title is left alone


def _with_h2(leaf: Path, title: str) -> None:
    import html as html_mod

    page = (leaf / "page.html").read_text(encoding="utf-8")
    assert page.count("<h2>Pricing the offer</h2>") == 1
    (leaf / "page.html").write_text(page.replace("<h2>Pricing the offer</h2>", f"<h2>{html_mod.escape(title)}</h2>"), encoding="utf-8")


# ------------------------------------------------------------ Rule 2: venue text forges nothing


def test_venue_text_cannot_forge_a_heading_a_rule_or_an_attribute(tmp_path):
    root, cap = _fake_wiki(tmp_path)
    assert _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--limit", "1").returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    meta = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
    meta.update(
        title="Pricing\n---\n# Forged heading\n```",
        embed_url='https://play.hubspotvideo.com/v/1/id/2?x="><script>alert(1)</script>',
        player_url="https://evil.example/play.hubspotvideo.com/v/1/id/2",
        final_url="https://www.example-hubspot.invalid/other\n# Forged canonical",
    )
    (leaf / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (leaf / "published.txt").write_text("2026-07-15\n# Forged by a date\n", encoding="utf-8")
    (leaf / "external_url.txt").write_text("javascript:alert(1)\n", encoding="utf-8")
    assert _run("record", str(cap), str(leaf)).returncode == 0
    record = json.loads((leaf / "capture.json").read_text(encoding="utf-8"))
    assert record["title"] == "Pricing --- # Forged heading ```"  # one line, and the host's filename rule holds it
    assert all(isinstance(value, str) or value is None for value in record.values())

    # …and what `record` hands the process step is CHECKED, so the hostile
    # embed and the forged canonical never reach a page.
    said = json.loads(_run("record", str(cap), str(leaf)).stdout)["captured"]["video"]
    assert said == {"embed_url": None, "player_url": None, "stream_url": STREAM,
                    "mux_playback_id": "AbCdEfGhIjKlMnOpQrStUvWx0123456789"}
    assert all(value is None or "\n" not in value for value in said.values())


def test_the_embed_and_the_stream_are_checked_before_a_worker_can_copy_them():
    meta = leaves.clean_meta(json.loads((FIX / "meta.json").read_text(encoding="utf-8")))
    assert meta["embed_url"].endswith("&renderContext=iframe")
    for bad in ("http://play.hubspotvideo.com/v/1/id/2", "https://play.hubspotvideo.com.evil.example/v/1/id/2",
                "https://play.hubspotvideo.com/v/1/id/2/../../x", 'https://play.hubspotvideo.com/v/1/id/2?a="b', None, 7):
        assert leaves.player_url(bad, query=True) is None, bad
    assert leaves.player_url("https://play.hubspotvideo.com/v/1/id/2?a=b", query=False) is None
    assert leaves.clean_meta({"stream_url": "https://stream.mux.com/short.m3u8", "mux_playback_id": "x y"}) | {"title": None} == {
        "title": None, "stream_url": None, "mux_playback_id": None, "player_url": None, "embed_url": None, "final_url": None, "status": None, "fetched_at": None}
    assert leaves.fold("a\nb\tc") == "a b c" and leaves.safe_url("javascript:alert(1)") is None


# ------------------------------------------------------------ S8: a mis-rooted job is not "nothing new"


def test_nothing_in_scope_is_a_failure_that_names_the_scope_and_the_target():
    """`apply` lands `skipped` as done: a job rooted at a leaf (`every: once`, the manifest default)
    closed silently having captured nothing."""
    rows = [{"url": f"https://www.example-hubspot.invalid/p{n}", "why": "scope"} for n in range(9)]
    job = {"scope": "section", "target": LESSON, "slug": "site-learn", "ticket": "t1"}
    report = leaves.build_report(ticket="t1", planned=[], captured=[], skipped=[*rows, {"url": "u", "why": "excluded"}], missing=[], job=job)
    assert report["outcome"] == "failed" and "harvest.scope" in report["reason"] and LESSON in report["reason"] and "scope: 9" in report["reason"]
    for closes in ("known", "older_than_min_date"):
        held = leaves.build_report(ticket="t1", planned=[], captured=[], skipped=[*rows, {"url": "u", "why": closes}], missing=[], job=job)
        assert held["outcome"] == "skipped", closes


def test_a_job_rooted_at_a_leaf_fails_out_loud(tmp_path):
    root, cap = _fake_wiki(tmp_path)
    ticket = json.loads((cap / "ticket.json").read_text(encoding="utf-8"))
    ticket.update(item=LESSON + "/deeper", target=LESSON + "/deeper")
    (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    rel = str(cap.relative_to(root))
    assert _documented(root, "plan", rel, "--urls", f"{rel}/../../../sitemap.xml").returncode == 2  # not there: a clear refusal
    shutil.copy(FIX / "sitemap.xml", cap / "sitemap.xml")
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml").returncode == 0
    done = _documented(root, "report", rel)
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert done.returncode == 1 and report["outcome"] == "failed" and "harvest.scope" in report["reason"] and LESSON + "/deeper" in report["reason"]


# ------------------------------------------------------------ S5: the clock, and what a second run does


def _documented_section(tmp_path, **ticket_over):
    root, cap = _fake_wiki(tmp_path)
    if ticket_over:
        ticket = {**json.loads((cap / "ticket.json").read_text(encoding="utf-8")), **ticket_over}
        (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    shutil.copy(FIX / "sitemap.xml", cap / "sitemap.xml")
    shutil.copy(FIX / "sites.json", cap / "sites.json")
    return root, cap, str(cap.relative_to(root))


def test_plan_record_and_report_the_documented_way_from_the_wiki_root(tmp_path):
    """Rule 3: `run` starts a script at the WIKI ROOT, so every path is the ticket's wiki-relative `capture_dir`."""
    root, cap, rel = _documented_section(tmp_path)
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json")
    assert done.returncode == 0, done.stderr
    said = json.loads(done.stdout)
    assert [leaf["n"] for leaf in said["leaves"]] == [0, 1, 2] and said["stop"] is False and said["limit"] == leaves.DOWNLOAD_LIMIT
    assert said["skipped"]["unsafe_url"] == 2
    nxt = _documented(root, "next", rel)
    assert nxt.returncode == 0 and json.loads(nxt.stdout)["n"] == 0 and json.loads(nxt.stdout)["item"] == LESSON
    _fill(root / said["leaves"][0]["dir"])
    done = _documented(root, "record", rel, "--leaf", "0")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["captured"]["dir"] == said["leaves"][0]["dir"]
    record = json.loads((root / said["leaves"][0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert record["body"] == "page.html" and record["content_type"] == "text/html"
    assert json.loads(_documented(root, "next", rel).stdout)["n"] == 1  # the captured leaf is not offered again
    done = _documented(root, "report", rel)
    assert done.returncode == 0, done.stderr
    assert json.loads((cap / "report.json").read_text(encoding="utf-8"))["outcome"] == "partial"
    assert sorted(p.name for p in root.iterdir()) == ["_raw"]  # nothing was written at the wiki root
    # A mistyped or absolute-elsewhere directory is refused with the reason, not a traceback.
    wrong = _documented(root, "report", "_raw/site-learn/nope")
    assert wrong.returncode == 2 and "wiki-relative" in wrong.stderr and "Traceback" not in wrong.stderr


def test_past_the_deadline_the_worker_is_told_to_stop_and_the_report_says_how_to_go_on(tmp_path):
    """The broker's slice-cap kill fails the ticket WITHOUT reading a report, so the run ends itself first.
    The deadline is keyed to the SPAWN: `ticket.json`'s mtime — the ticket id is the same on every pull."""
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json").returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert plan["deadline_epoch"] == pytest.approx((cap / "ticket.json").stat().st_mtime + 20 * 60)
    assert plan["hard_stop_epoch"] < (cap / "ticket.json").stat().st_mtime + 30 * 60
    _fill(root / plan["leaves"][0]["dir"])

    spawned_long_ago = time.time() - 25 * 60
    os.utime(cap / "ticket.json", (spawned_long_ago, spawned_long_ago))
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json").returncode == 0
    done = _documented(root, "record", rel, "--leaf", "0")
    assert done.returncode == 5 and json.loads(done.stdout)["stop"] is True  # written, AND told to stop
    assert (root / plan["leaves"][0]["dir"] / "capture.json").is_file()
    nxt = _documented(root, "next", rel)
    assert nxt.returncode == 5 and json.loads(nxt.stdout) == {"stop": True, "why": "deadline", "left": 2, "seconds_left": 0}
    done = _documented(root, "report", rel)
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert done.returncode == 0 and report["outcome"] == "partial" and "1 of 3 pages captured, 2 left" in report["reason"]
    assert "every: once" in report["reason"] and "pipeline queue retry 0123456789ab" in report["reason"]


def test_a_second_run_does_not_redo_what_a_killed_run_finished(tmp_path):
    """A killed slice mints no process tickets, so `known[]` never grows: without `landed`, the retry
    re-planned every leaf in the same order and hit the same cap."""
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1").returncode == 0
    first = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [leaf["item"] for leaf in first["leaves"]] == [LESSON] and first["limit"] == 1
    _fill(root / first["leaves"][0]["dir"])
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    assert _run("record", rel, "--leaf", "0", "--media-file", str(video), cwd=root).returncode == 0
    assert _documented(root, "report", rel).returncode == 0
    limited = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert limited["outcome"] == "partial" and "1 of 3" in limited["reason"]  # every PLANNED page landed, and the limit left two: not `ok`

    # …the slice is killed; the report is never applied; the same ticket is dispatched again.
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1")
    assert done.returncode == 0 and not (cap / "report.json").exists()  # Rule 4: the run before's report is gone first
    second = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [(leaf["item"], bool(leaf.get("landed"))) for leaf in second["leaves"]] == [
        (LESSON, True), ("https://www.example-hubspot.invalid/learn/offers/lesson-one", False)]  # landed is outside the limit
    nxt = json.loads(_documented(root, "next", rel).stdout)
    assert nxt["item"].endswith("/lesson-one") and nxt["n"] == 1
    assert _documented(root, "report", rel).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert [row["item"] for row in report["captured"]] == [LESSON]  # what the killed run finished is reported by this one


def test_the_limit_defaults_by_what_a_leaf_costs(tmp_path):
    harvest = {"scope": "section", "access": "free", "exclude_urls": [], "assets": "reference"}
    root, cap, rel = _documented_section(tmp_path, harvest=harvest)
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml")
    assert json.loads(done.stdout)["limit"] is None
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--limit", "0")
    assert json.loads(done.stdout)["limit"] is None


# ------------------------------------------------------------ S7: a refresh ticket


def test_a_refresh_ticket_plans_exactly_its_resource_and_captures_it_again(tmp_path):
    """It used to drop the refreshed page as `known`, walk the rest of the section, and report `failed`."""
    rel = f"_raw/site-learn/{leaves.leaf_name(LESSON)}"  # `jobs.capture_dir_for(slug, resource)`
    cap = tmp_path / rel
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps({
        "v": 1, "ticket": "feedfacecafe", "unit": UNIT, "slug": "site-learn", "item": LESSON, "target": LESSON,
        "capture_dir": rel, "dest": "sources/courses/site-learn", "hosts": ["www.example-hubspot.invalid"],
        "harvest": {"scope": "section", "access": "free", "exclude_urls": [], "assets": "download"},
        "options": {}, "credential": None, "min_date": None,
        "known": [{"resource": LESSON, "harvested_at": "2026-08-30T09:12:04Z"}, {"resource": SECTION, "harvested_at": None}],
        "refresh": True, "resource": LESSON, "prev_harvested_at": "2026-08-30T09:12:04Z",
    }), encoding="utf-8")
    for stale in ("capture.json", "page.html", "report.json"):  # the first pull's, in the SAME directory
        (cap / stale).write_text('{"outcome": "ok", "body": "page.html"}', encoding="utf-8")
    done = _documented(tmp_path, "plan", rel)  # no --urls: there is nothing to enumerate
    assert done.returncode == 0, done.stderr
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert plan["leaves"] == [{"item": LESSON, "dir": rel, "lastmod": None}] and plan["skipped"] == []
    assert not any((cap / stale).exists() for stale in ("capture.json", "page.html", "report.json"))  # forced: `apply` hashes THIS run's body
    _fill(cap)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    done = _run("record", rel, "--leaf", "0", "--media-file", str(video), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    # No second transcript stub over a video the wiki already transcribed.
    assert json.loads(done.stdout)["captured"]["media"] is None and not list(cap.glob("media.*"))
    assert _documented(tmp_path, "report", rel).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and report["captured"] == [{"item": LESSON, "dir": rel, "title": "Pricing the offer"}]
    # 404/410: the render's status refuses the capture, and `--gone` is the answer.
    meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), "status": 410}
    (cap / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    gone = _documented(tmp_path, "record", rel, "--leaf", "0")
    assert gone.returncode == 2 and "--gone" in gone.stderr
    assert _documented(tmp_path, "report", rel, "--gone").returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert (report["outcome"], report["captured"]) == ("gone", [])


def test_gone_is_a_refresh_tickets_alone_and_a_media_stub_is_not_refreshed_alone(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml").returncode == 0
    (cap / "report.json").write_text('{"outcome": "ok"}', encoding="utf-8")
    refused = _documented(root, "report", rel, "--gone")
    assert refused.returncode == 2 and "refresh" in refused.stderr
    assert not (cap / "report.json").exists()  # a refusal never leaves the run before's `ok` for `apply` to read

    ticket = {**json.loads((cap / "ticket.json").read_text(encoding="utf-8")), "refresh": True, "resource": STREAM, "item": STREAM, "target": STREAM}
    (cap / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    refused = _documented(root, "plan", rel)
    assert refused.returncode == 2 and "media" in refused.stderr
    assert _documented(root, "report", rel, "--failed", "--reason", "refresh_unsupported:media").returncode == 1
    assert json.loads((cap / "report.json").read_text(encoding="utf-8"))["outcome"] == "failed"


# ------------------------------------------------------------ S11: no venue url ever reaches a shell


def _stub_front_door(tmp_path: Path) -> tuple[dict, Path]:
    """An `llm-wiki-ops` first on PATH that records each argv it was started with and plays the plugin's
    `assets.py`: `detect` writes an empty manifest, `download` marks the Mux master downloaded."""
    bin_dir, seen = tmp_path / "stub-bin", tmp_path / "seen.jsonl"
    bin_dir.mkdir()
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}) + '\\n')\n"
        "argv = sys.argv[1:]\n"
        "if argv[2] == 'detect':\n"
        "    Path(argv[argv.index('--out') + 1]).write_text('[]')\n"
        "elif '--mode' not in argv:\n"
        "    manifest = Path(argv[3]); assets = json.loads(manifest.read_text())\n"
        "    store = Path(argv[argv.index('--dest') + 1]); store.mkdir(parents=True, exist_ok=True)\n"
        "    (store / 'abc-lesson.mp4').write_bytes(b'video')\n"
        "    assets[-1].update(status='downloaded', local_path='../assets/abc-lesson.mp4')\n"
        "    manifest.write_text(json.dumps(assets))\n"
        "print('the plugin script said this on stdout')\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return env, seen


def test_the_asset_steps_carry_the_pages_url_as_argv_and_never_through_a_shell(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json").returncode == 0
    leaf = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]
    _fill(root / leaf["dir"])
    (root / leaf["dir"] / "net.json").write_text("[]", encoding="utf-8")
    env, seen = _stub_front_door(tmp_path)
    done = _documented(root, "assets", rel, "--leaf", "0", env=env)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["video"] == "downloaded" and "the plugin script said" not in done.stdout
    detect, download = (json.loads(line) for line in seen.read_text(encoding="utf-8").splitlines())
    script = "skills/harvest/scripts/assets.py"
    assert detect["argv"] == ["run", script, "detect", f"{leaf['dir']}/page.html", "--base-url", LESSON, "--out", f"{leaf['dir']}/assets.json",
                              "--network-log", f"{leaf['dir']}/net.json"]
    assert download["argv"] == ["run", script, "download", f"{leaf['dir']}/assets.json", "--dest", "_raw/site-learn/assets", "--referer", LESSON]
    assert Path(detect["cwd"]) == root.resolve()  # the nested call is bound by cwd
    # …and `record` then finds the file with no flag at all.
    done = _documented(root, "record", rel, "--leaf", "0")
    assert done.returncode == 0 and json.loads(done.stdout)["captured"]["media"] == "media.mp4"
    assert not list(root.rglob("PWNED")) and not (Path.cwd() / "PWNED").exists()

    job = {"slug": "s", "assets": "download-audio"}
    assert leaves.assets_steps(job, leaf)[1][-3:] == ["--referer", LESSON, "--audio-only"]
    assert leaves.assets_steps({"slug": "s", "assets": "reference"}, leaf)[1][-2:] == ["--mode", "reference"]


def test_a_missing_url_is_named_by_leaf_or_by_bare_host_never_typed(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json").returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    _fill(root / plan["leaves"][0]["dir"])
    assert _documented(root, "record", rel, "--leaf", "0").returncode == 0
    done = _documented(root, "report", rel, "--missing-leaf", "denied", "0", "--missing-leaf", "timeout", "1", "--missing-host", "denied", "Chunk.Mux.com")
    assert done.returncode == 0, done.stderr
    assert json.loads((cap / "report.json").read_text(encoding="utf-8"))["missing"] == [
        {"host": "stream.mux.com", "url": STREAM, "why": "denied"},
        {"host": "www.example-hubspot.invalid", "url": plan["leaves"][1]["item"], "why": "timeout"},
        {"host": "chunk.mux.com", "url": "https://chunk.mux.com/", "why": "denied"},
    ]
    assert _documented(root, "report", rel, "--missing-host", "denied", "x;$(touch${IFS}PWNED)").returncode == 2
    assert _documented(root, "report", rel, "--missing", "denied", STREAM).returncode == 2  # the url-taking flag is gone


def test_render_reads_its_url_off_the_plan(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--limit", "1").returncode == 0
    (leaf,) = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"]
    assert capturer.planned_leaf(cap, 0) == (leaf["item"], root.resolve() / leaf["dir"])
    assert capturer.planned_leaf(cap, 1) is None and capturer.planned_leaf(cap, -1) is None and capturer.planned_leaf(tmp_path, 0) is None
    assert "--leaf" in subprocess.run([sys.executable, str(CAPTURE), "render", "-h"], capture_output=True, text=True, check=False).stdout


def test_venue_text_on_a_command_line_is_quoted_verbatim_and_never_retyped():
    """The harvest half never lets a url near a shell at all (`--leaf <n>`);
    the process half has the worker paste venue text, under one stated rule."""
    skill = (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8")
    harvest = skill.split("### harvest", 1)[1].split("### process", 1)[0]
    assert "--leaf <n>" in harvest
    for typed in ("--external-url", "--base-url", "--referer", "render <item>", "render <url>", "--missing <", "--url <"):
        assert typed not in harvest, typed
    process = skill.split("### process", 1)[1].split("## ", 1)[0]
    assert "VERBATIM and\nSINGLE-QUOTED" in process or "VERBATIM and SINGLE-QUOTED" in " ".join(process.split())
    assert "unquotable_title" in process
    # Every venue value in a process command sits inside single quotes.
    venue = ("<item>", "<stream_url>", "<content_selector>", "<title_selector>",
             "<drop selector>", "<the capture title>", "<title>")
    for line in process.splitlines():
        if not line.lstrip().startswith("llm-wiki-ops"):
            continue
        bare = re.sub(r"'[^']*'", "", line)
        assert not [one for one in venue if one in bare], line.strip()


# ------------------------------------------------------------ robustness: say it, do not trace it


@pytest.mark.parametrize("served", ["<urlset><url><loc>https://x.example/a</loc>", "[not json", "<!DOCTYPE html><html><body>Sign in</body></html"])
def test_a_malformed_enumeration_is_a_clear_refusal_and_the_failure_can_still_be_reported(tmp_path, served):
    root, cap, rel = _documented_section(tmp_path)
    (cap / "sitemap.xml").write_text(served, encoding="utf-8")
    (cap / "report.json").write_text('{"outcome": "ok"}', encoding="utf-8")
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml")
    assert done.returncode == 2 and "Traceback" not in done.stderr and "--urls" in done.stderr and "report --failed" in done.stderr
    assert not (cap / "report.json").exists() and not (cap / "plan.json").exists()
    missing = _documented(root, "plan", rel, "--urls", f"{rel}/never-saved.xml")
    assert missing.returncode == 2 and "Traceback" not in missing.stderr and "never-saved.xml" in missing.stderr
    done = _documented(root, "report", rel, "--failed", "--reason", "sitemap_unreadable")
    assert done.returncode == 1
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert (report["ticket"], report["outcome"], report["reason"], report["captured"]) == ("0123456789ab", "failed", "sitemap_unreadable", [])


def test_fetched_at_is_the_renders_time_not_the_time_record_ran(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1").returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), "fetched_at": "2026-09-01T08:00:00Z"}
    (leaf / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    done = _documented(root, "record", rel, "--leaf", "0", "--no-media")
    assert json.loads(done.stdout)["captured"]["fetched_at_from"] == "meta.json"
    assert json.loads((leaf / "capture.json").read_text(encoding="utf-8"))["fetched_at"] == "2026-09-01T08:00:00Z"


def test_what_the_partial_reason_tells_the_operator_to_do_is_real(ops, env, wiki):
    """`every` is not an identity key, so a `once` job can be given a period while a section fills, and back."""
    job = declared_job(ops, env, wiki, UNIT, "https://www.example-hubspot.invalid/continue", slug="port-channel-hubspot-continue")
    assert job.record["every"] == "once"
    for cadence in ("1h", "once"):
        done = run(ops, at(env, wiki), "--json", "pipeline", "edit", job.slug, f"every={cadence}")
        assert done.returncode == 0, done.stdout + done.stderr
        assert run(ops, at(env, wiki), "--json", "pipeline", "show", job.slug).data["job"]["every"] == cadence
    refused = run(ops, at(env, wiki), "--json", "pipeline", "queue", "retry", "0123456789ab")
    assert refused.returncode != 0 and "no finished item" in refused.stdout + refused.stderr  # the verb exists; this ticket never ran


def test_a_sitemap_index_offers_only_children_that_are_safe_and_on_the_targets_host(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    (cap / "sitemap.xml").write_text(
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://www.example-hubspot.invalid/sitemap-1.xml</loc></sitemap>"
        "<sitemap><loc>https://www.example-hubspot.invalid/s.xml;$(touch${IFS}PWNED)</loc></sitemap>"
        "<sitemap><loc>https://elsewhere.example/sitemap.xml</loc></sitemap></sitemapindex>", encoding="utf-8")
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["sitemaps"] == ["https://www.example-hubspot.invalid/sitemap-1.xml"]


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
        done = subprocess.run(["/bin/sh", "-c", line], env=at(env, wiki), capture_output=True, text=True, check=False)
        assert done.returncode == 0, line + "\n" + done.stdout + done.stderr
        assert (wiki / json.loads(done.stdout)["path"]).is_file()
