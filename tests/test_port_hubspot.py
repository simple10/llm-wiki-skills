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
import re
import shutil
import subprocess
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


def _run(*args, cwd=None):
    """The unit's script from the WORKING TREE, its PEP 723 deps resolved by uv
    exactly as `llm-wiki-ops run` would."""
    return subprocess.run(["uv", "run", "-q", "--script", str(LEAVES), *args], capture_output=True, text=True, cwd=cwd, check=False)


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


def _captured_lesson(ops, env, wiki, *extra_page_args):
    job = declared_job(ops, env, wiki, UNIT, SECTION)
    assert job.record["harvest"]["scope"] == "section"
    cap = ticket_in(wiki, job, f"learn--{_hash8(SECTION)}", unit=UNIT, item=SECTION)
    urls = cap / "urls.json"
    urls.write_text(json.dumps([{"url": LESSON + "?hsLang=en", "lastmod": "2026-07-15"}]), encoding="utf-8")
    done = _run("plan", str(cap), "--urls", str(urls), "--sites", str(FIX / "sites.json"))
    assert done.returncode == 0, done.stderr
    (leaf,) = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"]
    assert leaf == {"item": LESSON, "dir": f"_raw/{job.slug}/learn-offers-lesson-two--{_hash8(LESSON)}", "lastmod": "2026-07-15"}
    lesson_dir = wiki / leaf["dir"]
    _fill(lesson_dir)
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
    assert page.name == "Pricing the offer.md" or "pricing" in page.name.lower()
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
    assert body.count("Pricing the offer") == 0  # the title is the frontmatter's; the body does not repeat it


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
    assert _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--sites", str(FIX / "sites.json"), "--limit", "1").returncode == 0
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
