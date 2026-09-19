"""channel-hubspot-video on the rebuilt worker contract.

One download ticket captures a whole section: `leaves.py plan` filters the
enumeration and names one capture directory per page, `leaves.py page` turns a
rendered `page.html` into the `page.md` + `capture.json` the generic extractor
reads — with the SITE's selectors, which now live in the unit's own
`references/sites.json` because nothing reads a manifest `extract` block any
more — and `leaves.py report` writes the report `apply` reads.

The pure logic is imported and runs everywhere. The end-to-end cases go
through the real `pipeline extract` and skip where no CLI is at hand. Nothing
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
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ROOT, declared_job, extracted, ticket_in

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
    """The unit's script from the WORKING TREE, its PEP 723 deps resolved by uv
    exactly as `llm-wiki-ops run` would."""
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


def test_the_video_lands_where_the_player_stood_and_survives_embeds_false():
    meta = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
    body = leaves.place_video("intro\n\n<!-- media:embed:1 -->\n\noutro\n", meta)
    assert body.index("intro") < body.index("<iframe") < body.index(f"[Video: {STREAM}]") < body.index("outro")
    assert "media:embed" not in body
    on_top = leaves.place_video("no player in the content root\n", meta)
    assert on_top.startswith("<iframe") and "no player" in on_top
    assert leaves.place_video("a pointer page\n", {}) == "a pointer page\n"


def test_facts_never_carry_a_key_the_host_owns():
    meta = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
    facts = leaves.facts_of(LESSON, meta, published="2026-07-15", external_url=None)
    assert facts == {
        "type": "video", "venue": "hubspot-cms", "published": "2026-07-15",
        "mux_playback_id": "AbCdEfGhIjKlMnOpQrStUvWx0123456789", "video_url": STREAM,
        "player_url": "https://play.hubspotvideo.com/v/1234567/id/987654321",
    }
    assert not set(facts) & leaves.HOST_KEYS
    assert all(isinstance(value, str) for value in facts.values())
    pointer = leaves.facts_of(LESSON, {}, published=None, external_url="https://podcasts.example/ep/1")
    assert pointer == {"type": "page", "venue": "hubspot-cms", "external_url": "https://podcasts.example/ep/1"}


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


def test_a_media_leaf_is_a_sibling_at_the_depth_apply_accepts():
    rel = leaves.media_leaf_dir(f"_raw/site-learn/learn-offers-lesson-two--{_hash8(LESSON)}", STREAM)
    assert rel == f"_raw/site-learn/learn-offers-lesson-two-video--{_hash8(STREAM)}"


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
    """A capture directory with the ticket a spawner would have left, in a tree shaped like a wiki's."""
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


def test_plan_page_report_from_a_ticket_with_files_only(tmp_path):
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
    done = _run("page", str(cap), str(lesson_dir), "--published", "2026-07-15", "--sites", str(FIX / "sites.json"))
    assert done.returncode == 0, done.stderr

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
    assert re.search(r'^argument-hint: "ticket=<id>"$', text, re.M)
    for gone in ("--stage", "<job.", "harvest_apply", "out_of_scope`", "scaffold", "intake.py", "job.py", "watch.py", "drain_pending", "--exclude-url '"):
        assert gone not in text, gone
    for kept in ("ticket.json", "capture.json", "report.json", "page.md", "data-hsv-src", "stream.mux.com", "verifi.podscribe.com"):
        assert kept in text, kept


# ------------------------------------------------------------ end to end, the real extractor


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
    # A media leaf is qualified by its PAGE's url, and a segment never smuggles in a refused character.
    media = leaf("/v.m3u8", item="https://stream.example/v.m3u8", media_of="https://x.example/learn/what%3F/a%2Fb")
    assert leaves.leaf_qualifiers(media)[:2] == ["a/b", "what?"]
    assert leaves.unique_title("Introduction", leaves.leaf_qualifiers(media), taken) == "Introduction (a-b)"


def _held_leaf(root: Path, item: str, title, body="page.md", **over) -> dict:
    rel = f"_raw/site-learn/{leaves.leaf_name(item + over.get('media_of', ''))}"
    (root / rel).mkdir(parents=True)
    (root / rel / body).write_text("x\n", encoding="utf-8")
    (root / rel / "capture.json").write_text(json.dumps({"item": item, "title": title, "body": body}), encoding="utf-8")
    return {"item": item, "dir": rel, "lastmod": None, **over}


def test_settling_titles_is_idempotent_and_a_media_leaf_follows_its_page(tmp_path):
    one, two = f"{SECTION}/module-1/intro", f"{SECTION}/module-2/intro"
    planned = [
        _held_leaf(tmp_path, one, "Intro"), _held_leaf(tmp_path, two, "Intro"),
        _held_leaf(tmp_path, f"{SECTION}/a", None), _held_leaf(tmp_path, f"{SECTION}/b", None),  # no title: filed as `page`
        _held_leaf(tmp_path, STREAM, "Intro (video)", "media.mp4", media_of=one),
        _held_leaf(tmp_path, STREAM, "Intro (video)", "media.mp4", media_of=two),
        {"item": f"{SECTION}/never-rendered", "dir": "_raw/site-learn/never--00000000", "lastmod": None},
    ]
    want = ["Intro", "Intro (module-2)", None, "page (b)", "Intro (video)", "Intro (module-2) (video)"]
    for _ in range(2):
        leaves.settle_titles(tmp_path, planned)
        assert [json.loads((tmp_path / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))["title"] for leaf in planned[:-1]] == want
    # `page` run again writes the plain titles back; the next report ends on the SAME names.
    for leaf, plain in ((planned[1], "Intro"), (planned[5], "Intro (video)")):
        (tmp_path / leaf["dir"] / "capture.json").write_text(json.dumps({"item": leaf["item"], "title": plain, "body": "page.md" if plain == "Intro" else "media.mp4"}), encoding="utf-8")
    leaves.settle_titles(tmp_path, planned)
    assert [json.loads((tmp_path / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))["title"] for leaf in planned[:-1]] == want


def _fresh(wiki: Path, slug: str) -> None:
    """The session wiki is shared, and a finished capture on disk is now a
    `landed` leaf: every end-to-end case starts from an empty `_raw/<slug>/`."""
    shutil.rmtree(wiki / "_raw" / slug, ignore_errors=True)


def _captured_lesson(ops, env, wiki, *extra_page_args, lesson=LESSON, meta_over=None):
    job = declared_job(ops, env, wiki, UNIT, SECTION)
    assert job.record["harvest"]["scope"] == "section"
    _fresh(wiki, job.slug)
    cap = ticket_in(wiki, job, f"learn--{_hash8(SECTION)}", unit=UNIT, item=SECTION)
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
    done = _run("page", str(cap), str(lesson_dir), "--published", "2026-07-15", "--sites", str(FIX / "sites.json"), *extra_page_args)
    assert done.returncode == 0, done.stderr
    return job, cap, lesson_dir


def test_a_section_page_reaches_the_jobs_dest_with_the_sites_chrome_stripped(ops, env, wiki):
    job, cap, lesson_dir = _captured_lesson(ops, env, wiki)

    record = json.loads((lesson_dir / "capture.json").read_text(encoding="utf-8"))
    assert {k: record[k] for k in ("slug", "item", "title", "body", "content_type")} == {
        "slug": job.slug, "item": LESSON, "title": "Pricing the offer", "body": "page.md", "content_type": "text/markdown"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["fetched_at"])
    assert record["frontmatter"]["type"] == "video" and record["frontmatter"]["published"] == "2026-07-15"
    assert not (lesson_dir / "page.md").read_text(encoding="utf-8").startswith("---")

    assert _run("report", str(cap)).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok"
    assert report["captured"] == [{"item": LESSON, "dir": str(lesson_dir.relative_to(wiki)), "title": "Pricing the offer"}]

    (page,) = extracted(ops, env, wiki, lesson_dir)  # the REAL `pipeline extract`, over the leaf `apply` would mint a ticket for
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest)
    assert page.name == "Pricing the offer.md"
    front, _, body = text[4:].partition("\n---\n")
    assert text.startswith("---\n") and "\n---\n" not in body  # one frontmatter block, the extractor's own
    assert "title: Pricing the offer" in front and "status: draft" in front and LESSON in front
    # The site's selectors did their work: the theme's chrome is gone, the lesson is not.
    assert "how to price the offer" in body
    for chrome in ("Course modules", "Module one", "GET YOUR COPY NOW", "Results are not typical", "FREE ADVANCED TRAINING", "all rights reserved", "Start Here"):
        assert chrome not in text, chrome
    # The unit's exact facts survive today, in the body, until the extractor merges `frontmatter`.
    assert f"- source: <{LESSON}>" in body and "- type: video" in body and "- published: 2026-07-15" in body
    assert "- mux_playback_id: AbCdEfGhIjKlMnOpQrStUvWx0123456789" in body
    # The video, referenced the unit's way: the live iframe, and a link that outlives `process.embeds: false`.
    assert '<iframe src="https://play.hubspotvideo.com/v/1234567/id/987654321?parentOrigin=' in body
    assert f"[Video: {STREAM}]({STREAM})" in body
    assert "https://www.example-hubspot.invalid/hubfs/worksheets/pricing.pdf" in body  # relative links made absolute
    # Rule 1: the venue's TRUE title is the body's one H1 (the frontmatter's is the filename-safe one).
    assert body.lstrip("\n").startswith("# Pricing the offer\n") and body.count("Pricing the offer") == 1
    assert "source_title" not in record["frontmatter"]  # only where the two differ
    # `fetched_at` is when the page was RENDERED — `meta.json` carries none here, so `page.html`'s mtime.
    assert record["fetched_at"] == leaves._iso((lesson_dir / "page.html").stat().st_mtime)


def test_a_downloaded_video_becomes_a_queued_transcription_stub(ops, env, wiki, tmp_path):
    """The extractor queues a transcription only for a capture whose BODY is media, so the
    downloaded file rides out as a sibling leaf of its own."""
    video = tmp_path / "abc123-lesson.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 not really a video")
    job, cap, lesson_dir = _captured_lesson(ops, env, wiki, "--media-file", str(video))

    media_dir = wiki / f"_raw/{job.slug}/learn-offers-lesson-two-video--{_hash8(STREAM)}"
    record = json.loads((media_dir / "capture.json").read_text(encoding="utf-8"))
    assert (record["item"], record["title"], record["body"]) == (STREAM, "Pricing the offer (video)", "media.mp4")
    assert (media_dir / "media.mp4").read_bytes() == video.read_bytes()

    assert _run("report", str(cap)).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok"
    assert [row["dir"] for row in report["captured"]] == [str(lesson_dir.relative_to(wiki)), str(media_dir.relative_to(wiki))]

    (stub,) = extracted(ops, env, wiki, media_dir)
    text = stub.read_text(encoding="utf-8")
    assert stub.is_relative_to(wiki / job.dest)
    assert "extracted: queued" in text and "media.mp4" in text and STREAM in text
    (page,) = extracted(ops, env, wiki, lesson_dir)
    assert page != stub  # two titles, two pages: the stub never overwrites the lesson


def test_two_pages_with_one_title_land_as_two_pages_and_so_do_their_videos(ops, env, wiki, tmp_path):
    """The extractor files a page under its title and overwrites what is there:
    before `report` settled titles, a section's second "Pricing the offer" WAS
    the first one's page — and its `(video)` stub the first one's stub."""
    section = "https://www.example-hubspot.invalid/academy"
    one, two = f"{section}/module-1/intro", f"{section}/module-2/intro"
    job = declared_job(ops, env, wiki, UNIT, section, slug="port-channel-hubspot-names")
    _fresh(wiki, job.slug)
    cap = ticket_in(wiki, job, f"academy--{_hash8(section)}", unit=UNIT, item=section)
    urls = cap / "urls.json"
    urls.write_text(json.dumps([{"url": one, "lastmod": "2026-07-15"}, {"url": two, "lastmod": "2026-07-01"}]), encoding="utf-8")
    assert _run("plan", str(cap), "--urls", str(urls), "--sites", str(FIX / "sites.json")).returncode == 0
    planned = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"]
    assert [leaf["item"] for leaf in planned] == [one, two]
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 not really a video")
    for leaf in planned:
        _fill(wiki / leaf["dir"])
        done = _run("page", str(cap), str(wiki / leaf["dir"]), "--sites", str(FIX / "sites.json"), "--media-file", str(video))
        assert done.returncode == 0, done.stderr

    reports = []
    for _ in range(2):  # a respawned worker reports again: same names
        assert _run("report", str(cap)).returncode == 0
        reports.append(json.loads((cap / "report.json").read_text(encoding="utf-8")))
    report = reports[-1]
    assert report["outcome"] == "ok" and len(report["captured"]) == 4

    pages = [extracted(ops, env, wiki, wiki / row["dir"])[0] for row in report["captured"]]
    assert len({page.resolve() for page in pages}) == 4 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    names = ["Pricing the offer", "Pricing the offer (module-2)", "Pricing the offer (video)", "Pricing the offer (module-2) (video)"]
    assert [page.name for page in pages] == [f"{name}.md" for name in names]
    assert [[row["title"] for row in r["captured"]] for r in reports] == [names] * 2
    first, second = pages[0].read_text(encoding="utf-8"), pages[1].read_text(encoding="utf-8")
    assert f"resource: {one}" in first and f"resource: {two}" in second  # still the FIRST page's page


def test_the_downloaded_video_is_read_off_the_asset_manifest(tmp_path):
    """No flag needed: the asset `patch-assets` appended, once `assets.py download` marks it
    `downloaded`, is the media leaf's body — and `--no-media-leaf` declines it."""
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
    media_dir = root / leaves.media_leaf_dir(str(leaf.relative_to(root)), STREAM)
    assert _run("page", str(cap), str(leaf), "--sites", str(FIX / "sites.json"), "--no-media-leaf").returncode == 0
    assert not media_dir.exists()
    done = _run("page", str(cap), str(leaf), "--sites", str(FIX / "sites.json"))
    assert done.returncode == 0, done.stderr
    record = json.loads((media_dir / "capture.json").read_text(encoding="utf-8"))
    assert (record["body"], record["item"], record["frontmatter"]["page_url"]) == ("media.m4a", STREAM, LESSON)
    assert record["content_type"].startswith("audio/")
    assert (media_dir / "media.m4a").read_bytes() == b"audio, by courtesy"
    assert _run("report", str(cap)).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and len(report["captured"]) == 2  # the media leaf is not a second PAGE to have planned


@pytest.mark.parametrize("bad", ["notes.txt", "missing.mp4"])
def test_a_media_leaf_is_refused_for_a_file_the_extractor_would_not_queue(tmp_path, bad):
    root, cap = _fake_wiki(tmp_path)
    assert _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml")).returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    done = _run("page", str(cap), str(leaf), "--media-file", str(tmp_path / bad))
    assert done.returncode == 2 and "not a media file" in done.stderr


# ------------------------------------------------------------ Rule 1: the title is a filename


def test_safe_title_is_a_title_the_hosts_filename_rule_holds():
    """`page/note.py::filename_for` refuses ILLEGAL, a control character and a leading dot, and checks no length."""
    assert leaves.safe_title('Lesson 3: What is "A/B" pricing?') == "Lesson 3 - What is 'A-B' pricing"
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


@pytest.mark.parametrize(("venue_title", "safe"), [
    ('Lesson 3: What is "A/B" pricing?', "Lesson 3 - What is 'A-B' pricing"),
    (".Hidden: the offer?", "Hidden - the offer"),
])
def test_a_title_the_host_would_refuse_still_lands_as_a_page(ops, env, wiki, venue_title, safe):
    """BLOCKER: the extractor names the FILE from `capture.json`'s title and REFUSES the process
    ticket for `:` `?` `/` `"` or a leading dot — harvest said ok and the page never landed."""
    lesson = f"{SECTION}/offers/{leaves.slugify([safe])}"
    job, cap, lesson_dir = _captured_lesson(ops, env, wiki, "--no-media-leaf", lesson=lesson)
    _with_h2(lesson_dir, venue_title)
    done = _run("page", str(cap), str(lesson_dir), "--sites", str(FIX / "sites.json"), "--no-media-leaf")
    assert done.returncode == 0, done.stderr
    record = json.loads((lesson_dir / "capture.json").read_text(encoding="utf-8"))
    assert record["title"] == safe and record["frontmatter"]["source_title"] == venue_title
    assert _run("report", str(cap)).returncode == 0
    assert json.loads((cap / "report.json").read_text(encoding="utf-8"))["captured"][0]["title"] == safe
    (page,) = extracted(ops, env, wiki, lesson_dir)  # the REAL extractor: this is the line that failed
    assert page.name == f"{safe}.md" and page.is_relative_to(wiki / job.dest)
    body = page.read_text(encoding="utf-8")[4:].partition("\n---\n")[2]
    assert body.lstrip("\n").startswith("# " + venue_title.replace("<", "&lt;") + "\n")  # the TRUE title stays visible


def test_a_long_cjk_title_and_its_video_stub_both_land(ops, env, wiki, tmp_path):
    """100 CJK characters is 300 bytes: `filename_for` checks no length, and the real
    extractor died `OSError: [Errno 36] File name too long`. The ` (video)` suffix rides on the SAFE title."""
    venue_title = "価" * 100
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 not really a video")
    lesson = f"{SECTION}/offers/cjk"
    job, cap, lesson_dir = _captured_lesson(ops, env, wiki, "--no-media-leaf", lesson=lesson)
    _with_h2(lesson_dir, venue_title)
    done = _run("page", str(cap), str(lesson_dir), "--sites", str(FIX / "sites.json"), "--media-file", str(video))
    assert done.returncode == 0, done.stderr
    assert _run("report", str(cap)).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and len(report["captured"]) == 2
    page_title, media_title = (row["title"] for row in report["captured"])
    assert media_title == f"{page_title} (video)" and page_title.startswith("価") and page_title.endswith("…")
    for row in report["captured"]:
        assert len(f"{row['title']}.md".encode()) <= 255
        (landed,) = extracted(ops, env, wiki, wiki / row["dir"])
        assert landed.name == f"{row['title']}.md" and landed.is_file()


def test_a_qualifier_never_carries_a_settled_title_past_the_filename_cap(tmp_path):
    """`settle_titles` appends `(<url segment>)` and `page` a ` (video)`: the BASE is cut, the qualifier and the suffix kept."""
    base = leaves.safe_title("価" * 100)
    final = f"{base} ({'節' * 30}) (video)"
    fitted = leaves.fit_title(final, [base])
    assert len(fitted.encode()) <= leaves.FILENAME_TITLE_MAX_BYTES < len(final.encode())
    assert fitted.endswith(f"… ({'節' * 30}) (video)") and fitted.startswith("価")
    assert leaves.fit_title("Intro (module-2)", ["Intro"]) == "Intro (module-2)"  # under the cap: untouched

    seg = "%E7%AF%80" * 30
    one, two = f"{SECTION}/a/{seg}", f"{SECTION}/b/{seg}"
    planned = [_held_leaf(tmp_path, one, base), _held_leaf(tmp_path, two, base),
               _held_leaf(tmp_path, STREAM, f"{base} (video)", "media.mp4", media_of=one),
               _held_leaf(tmp_path, STREAM, f"{base} (video)", "media.mp4", media_of=two)]
    for _ in range(2):  # a second report renames nothing twice
        before = leaves.titles_on_disk(tmp_path, planned)
        leaves.settle_titles(tmp_path, planned)
        leaves.fit_titles(tmp_path, planned, before)
        titles = [json.loads((tmp_path / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))["title"] for leaf in planned]
        assert len({leaves.page_key(t) for t in titles}) == 4, titles
        assert all(len(f"{t}.md".encode()) <= 255 for t in titles), [len(t.encode()) for t in titles]
        assert titles[0] == base and titles[2] == f"{base} (video)" and titles[3].endswith(" (video)")


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
    # No sites file → no title_selector → the render's (hostile) title is the one used.
    forged = _run("page", str(cap), str(leaf), "--published", "2026-07-15\n# Forged by a flag")
    assert forged.returncode == 2 and "YYYY-MM-DD" in forged.stderr and not (leaf / "capture.json").exists()
    done = _run("page", str(cap), str(leaf))
    assert done.returncode == 0, done.stderr
    body = (leaf / "page.md").read_text(encoding="utf-8")
    record = json.loads((leaf / "capture.json").read_text(encoding="utf-8"))
    lines = body.splitlines()
    assert lines[0] == "# Pricing --- # Forged heading ```" and record["title"] == "Pricing --- # Forged heading ```"
    assert not any(line.strip() == "---" for line in lines) and sum(line.startswith("# ") for line in lines) == 1
    assert not any(line.startswith("```") for line in lines)
    assert "<iframe" not in body and "<script" not in body and "evil.example" not in body  # the embed is OMITTED…
    assert f"[Video: {STREAM}]({STREAM})" in body  # …and the plain Mux link stands
    assert "- published: 2026-07-15" in lines and "Forged by" not in body
    assert "external_url" not in record["frontmatter"] and "canonical_url" not in record["frontmatter"] and "player_url" not in record["frontmatter"]
    assert all("\n" not in value for value in record["frontmatter"].values())


def test_the_embed_is_checked_then_escaped_and_facts_are_folded():
    meta = leaves.clean_meta(json.loads((FIX / "meta.json").read_text(encoding="utf-8")))
    assert '?parentOrigin=https%3A%2F%2Fwww.example-hubspot.invalid&amp;renderContext=iframe"' in leaves.video_block(meta)
    for bad in ("http://play.hubspotvideo.com/v/1/id/2", "https://play.hubspotvideo.com.evil.example/v/1/id/2",
                "https://play.hubspotvideo.com/v/1/id/2/../../x", 'https://play.hubspotvideo.com/v/1/id/2?a="b', None, 7):
        assert leaves.player_url(bad, query=True) is None, bad
    assert leaves.player_url("https://play.hubspotvideo.com/v/1/id/2?a=b", query=False) is None
    assert leaves.clean_meta({"stream_url": "https://stream.mux.com/short.m3u8", "mux_playback_id": "x y"}) | {"title": None} == {
        "title": None, "stream_url": None, "mux_playback_id": None, "player_url": None, "embed_url": None, "final_url": None, "status": None, "fetched_at": None}
    facts = leaves.facts_of(LESSON, {}, published="2026-07-15T00:00", external_url="https://podcasts.example/ep/1", source_title="a\nb")
    assert facts == {"type": "page", "venue": "hubspot-cms", "external_url": "https://podcasts.example/ep/1", "source_title": "a b"}


def test_the_player_is_matched_by_origin_not_by_a_substring():
    assert "*=" not in capturer.IFRAME_SELECTOR and "^='https://play.hubspotvideo.com/'" in capturer.IFRAME_SELECTOR
    assert capturer.is_player("https://play.hubspotvideo.com/v/1234567/id/987654321?x=1")
    assert not capturer.is_player("https://evil.example/?next=play.hubspotvideo.com/v/1/id/2")
    mux = "AbCdEfGhIjKlMnOpQrStUvWx0123456789"
    assert capturer.find_mux_id([{"url": f"https://evil.example/?u=image.mux.com/{mux}/"}]) is None
    assert capturer.find_mux_id([{"url": f"https://image.mux.com/{mux}/storyboard.vtt"}]) == mux


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


def test_plan_page_and_report_the_documented_way_from_the_wiki_root(tmp_path):
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
    (root / said["leaves"][0]["dir"] / "published.txt").write_text("2026-07-15\n", encoding="utf-8")
    (root / said["leaves"][0]["dir"] / "external_url.txt").write_text("https://podcasts.example/ep/1\n", encoding="utf-8")
    done = _documented(root, "page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["written"][0]["dir"] == said["leaves"][0]["dir"]
    record = json.loads((root / said["leaves"][0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert record["frontmatter"]["published"] == "2026-07-15" and record["frontmatter"]["external_url"] == "https://podcasts.example/ep/1"
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
    done = _documented(root, "page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json")
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
    assert _run("page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json", "--media-file", str(video), cwd=root).returncode == 0
    assert _documented(root, "report", rel).returncode == 0
    limited = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert limited["outcome"] == "partial" and "1 of 3" in limited["reason"]  # every PLANNED page landed, and the limit left two: not `ok`

    # …the slice is killed; the report is never applied; the same ticket is dispatched again.
    done = _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1")
    assert done.returncode == 0 and not (cap / "report.json").exists()  # Rule 4: the run before's report is gone first
    second = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [(leaf["item"], bool(leaf.get("landed"))) for leaf in second["leaves"] if not leaf.get("media_of")] == [
        (LESSON, True), ("https://www.example-hubspot.invalid/learn/offers/lesson-one", False)]  # landed is outside the limit
    assert [leaf["dir"] for leaf in second["leaves"] if leaf.get("media_of")] == [leaves.media_leaf_dir(first["leaves"][0]["dir"], STREAM)]
    nxt = json.loads(_documented(root, "next", rel).stdout)
    assert nxt["item"].endswith("/lesson-one") and nxt["n"] == 1
    assert _documented(root, "report", rel).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert [row["item"] for row in report["captured"]] == [LESSON, STREAM]  # what the killed run finished is reported by this one


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
    for stale in ("capture.json", "page.md", "report.json"):  # the first pull's, in the SAME directory
        (cap / stale).write_text('{"outcome": "ok", "body": "page.md"}', encoding="utf-8")
    done = _documented(tmp_path, "plan", rel)  # no --urls: there is nothing to enumerate
    assert done.returncode == 0, done.stderr
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert plan["leaves"] == [{"item": LESSON, "dir": rel, "lastmod": None}] and plan["skipped"] == []
    assert not any((cap / stale).exists() for stale in ("capture.json", "page.md", "report.json"))  # forced: `apply` hashes THIS run's body
    _fill(cap)
    shutil.copy(FIX / "sites.json", cap / "sites.json")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    done = _run("page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json", "--media-file", str(video), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert len(json.loads(done.stdout)["written"]) == 1  # no second stub over a video the wiki already transcribed
    assert _documented(tmp_path, "report", rel).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and report["captured"] == [{"item": LESSON, "dir": rel, "title": "Pricing the offer"}]
    # 404/410: the render's status refuses the capture, and `--gone` is the answer.
    meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), "status": 410}
    (cap / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    gone = _documented(tmp_path, "page", rel, "--leaf", "0")
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
        f"open({str(seen)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'guard': 'LLM_WIKI_OPS_DISPATCHED' in os.environ}}) + '\\n')\n"
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
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "LLM_WIKI_OPS_DISPATCHED": "1"}
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
    assert Path(detect["cwd"]) == root.resolve() and not detect["guard"]  # bound by cwd; the re-entry guard is not inherited
    # …and `page` then finds the file with no flag at all.
    done = _documented(root, "page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json")
    assert done.returncode == 0 and len(json.loads(done.stdout)["written"]) == 2
    assert not list(root.rglob("PWNED")) and not (Path.cwd() / "PWNED").exists()

    job = {"slug": "s", "assets": "download-audio"}
    assert leaves.assets_steps(job, leaf)[1][-3:] == ["--referer", LESSON, "--audio-only"]
    assert leaves.assets_steps({"slug": "s", "assets": "reference"}, leaf)[1][-2:] == ["--mode", "reference"]


def test_a_missing_url_is_named_by_leaf_or_by_bare_host_never_typed(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json").returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    _fill(root / plan["leaves"][0]["dir"])
    assert _documented(root, "page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json").returncode == 0
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


def test_no_doc_has_the_worker_type_a_venues_url_onto_a_command_line():
    for name in ("SKILL.md", "INSTALL.md"):
        text = (ROOT / "skills" / UNIT / name).read_text(encoding="utf-8")
        for typed in ("--external-url", "--base-url", "--referer", "render <item>", "render <url>", "--missing <", "--url <"):
            assert typed not in text, (name, typed)
    assert "--leaf <n>" in (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8")


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


def test_fetched_at_is_the_renders_time_not_the_time_page_ran(tmp_path):
    root, cap, rel = _documented_section(tmp_path)
    assert _documented(root, "plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1").returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), "fetched_at": "2026-09-01T08:00:00Z"}
    (leaf / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    done = _documented(root, "page", rel, "--leaf", "0", "--sites", f"{rel}/sites.json", "--no-media-leaf")
    assert json.loads(done.stdout)["written"][0]["fetched_at_from"] == "meta.json"
    assert json.loads((leaf / "capture.json").read_text(encoding="utf-8"))["fetched_at"] == "2026-09-01T08:00:00Z"


def test_what_the_partial_reason_tells_the_operator_to_do_is_real(ops, env, wiki):
    """`every` is not an identity key, so a `once` job can be given a period while a section fills, and back."""
    from conftest import at, run
    job = declared_job(ops, env, wiki, UNIT, "https://www.example-hubspot.invalid/continue", slug="port-channel-hubspot-continue")
    assert job.record["every"] == "once"
    for cadence in ("1h", "once"):
        done = run(ops, env, "--json", "pipeline", "edit", job.slug, f"every={cadence}", at(wiki))
        assert done.returncode == 0, done.stdout + done.stderr
        assert run(ops, env, "--json", "pipeline", "show", job.slug, at(wiki)).data["job"]["every"] == cadence
    refused = run(ops, env, "--json", "pipeline", "queue", "retry", "0123456789ab", at(wiki))
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
