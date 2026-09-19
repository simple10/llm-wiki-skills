"""channel-circle on the rebuilt worker contract: one ticket walks a section.

The planner (`section_plan.py`) is the deterministic half — scope, exclusions,
`known[]`, leaf directories, the capture records and the one report — so it is
tested as pure functions everywhere, and END TO END through the real extractor
where an ops CLI is at hand. Playwright cannot run here: the fixtures under
`fixtures/circle/` are what `capture_lesson.py` leaves (`page.html`,
`meta.json`) for a space root and two lessons.
"""

from __future__ import annotations

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
SCRIPTS = ROOT / "skills" / "channel-circle" / "scripts"
PLAN = SCRIPTS / "section_plan.py"
TO_MARKDOWN = SCRIPTS / "to_markdown.py"
FIX = Path(__file__).resolve().parent / "fixtures" / "circle"

UNIT = "channel-circle"
BASE = "https://community.example.invalid"
TARGET = f"{BASE}/c/course-one"
L1 = f"{TARGET}/sections/111/lessons/2001"
L2 = f"{TARGET}/sections/111/lessons/2002"
OTHER_SPACE = f"{BASE}/c/course-two/sections/900/lessons/9001"
OFF_HOST = "https://elsewhere.example.invalid/c/course-one/sections/1/lessons/1"

spec = importlib.util.spec_from_file_location("circle_section_plan", PLAN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def root_meta() -> dict:
    return json.loads((FIX / "root" / "meta.json").read_text(encoding="utf-8"))


def ticket(**over) -> dict:
    base = {
        "v": 1, "ticket": "0123456789ab", "unit": UNIT, "slug": "course", "item": TARGET, "target": TARGET,
        "capture_dir": "_raw/course/c-course-one--aaaaaaaa", "dest": None, "hosts": ["community.example.invalid"],
        "harvest": {"scope": "section", "access": "licensed", "exclude_urls": [], "assets": "reference"},
        "options": {}, "credential": None, "min_date": None, "known": [],
    }
    base.update(over)
    return base


def plan_of(**over) -> dict:
    t = ticket(**over)
    return mod.build_plan(t, root_meta(), capture_rel=t["capture_dir"])


def why(plan: dict) -> dict:
    return {d["url"]: d["why"] for d in plan["dropped"]}


# --- the planner, pure ---------------------------------------------------------


def test_section_scope_keeps_the_lessons_under_the_target_in_course_order():
    plan = plan_of()
    assert [leaf["url"] for leaf in plan["leaves"]] == [L1, L2]  # fragment dropped, duplicate folded
    assert [leaf["order"] for leaf in plan["leaves"]] == [1, 2]
    assert why(plan) == {f"{TARGET}/sections/111": "not_lesson", OTHER_SPACE: "scope", OFF_HOST: "scope"}
    assert (plan["space"], plan["course"]) == ("course-one", "Course One | Example Community")
    assert [(leaf["title"], leaf["duration"]) for leaf in plan["leaves"]] == [
        ("Getting the Frame Right", "04:07"), ("Reading the Room", "12:30")]


def test_a_space_root_is_the_listing_not_a_leaf_but_a_page_scoped_target_is_the_one_leaf():
    assert not any(leaf["root"] for leaf in plan_of()["leaves"])
    page = plan_of(harvest={"scope": "page"})
    assert [(leaf["url"], leaf["dir"], leaf["root"]) for leaf in page["leaves"]] == [
        (TARGET, "_raw/course/c-course-one--aaaaaaaa", True)]  # the ticket's own dir, never composed


def test_domain_scope_takes_the_whole_host_and_nothing_off_it():
    plan = plan_of(harvest={"scope": "domain"})
    assert [leaf["url"] for leaf in plan["leaves"]] == [L1, L2, OTHER_SPACE]
    assert why(plan)[OFF_HOST] == "scope"


def test_a_lesson_target_under_section_scope_is_its_own_only_leaf():
    """The manifest's note, made true by the planner: from a lesson url every
    sibling lesson is outside the section."""
    plan = plan_of(target=L1, item=L1)
    assert [(leaf["url"], leaf["root"]) for leaf in plan["leaves"]] == [(L1, True)]
    assert why(plan)[L2] == "scope"


def test_known_and_excluded_lessons_are_dropped_by_the_unit_itself():
    plan = plan_of(
        known=[{"resource": L1 + "/", "harvested_at": "2026-09-01T00:00:00Z"}],
        harvest={"scope": "domain", "exclude_urls": [f"{BASE}/c/course-two"]},
    )
    assert [leaf["url"] for leaf in plan["leaves"]] == [L2]
    assert why(plan)[L1] == "known" and why(plan)[OTHER_SPACE] == "excluded"


def test_a_free_job_plans_nothing_because_every_lesson_is_behind_the_login():
    plan = plan_of(harvest={"scope": "section", "access": "free"})
    assert plan["leaves"] == [] and set(why(plan).values()) >= {"access"}


def test_a_leaf_dir_is_the_hosts_own_shape():
    """`pipeline/jobs.py::capture_dir_for`: slugified path, `--`, first 8 hex of
    sha1(item url), 60-char cap — and exactly three components, which is all
    `apply` accepts."""
    got = mod.leaf_dir("course", L1)
    assert got == f"_raw/course/c-course-one-sections-111-lessons-2001--{hashlib.sha1(L1.encode()).hexdigest()[:8]}"
    long = mod.leaf_dir("course", f"{BASE}/c/" + "x" * 200 + "/lessons/1")
    name = long.split("/")[-1]
    assert len(long.split("/")) == 3 and re.fullmatch(r"[a-z0-9-]{1,60}--[0-9a-f]{8}", name)


def test_a_truncated_link_text_names_no_title():
    assert mod.link_facts("x" * 80) == (None, None)
    assert mod.link_facts("A Lesson\n\n1:02:03") == ("A Lesson", "1:02:03")


def test_a_bad_scope_is_refused_not_guessed():
    with pytest.raises(ValueError, match="harvest.scope"):
        plan_of(harvest={"scope": "everything"})


def test_the_facts_block_lands_under_the_heading_once_however_often_it_is_written():
    block = mod.facts_block({"type": "lesson", "position": "Topic 1 of 2"}, L1)
    once = mod.with_facts("# A Lesson\n\nTopic 1 of 2\n\nBody.\n", "A Lesson", block)
    assert once == mod.with_facts(once, "A Lesson", block)
    assert once.startswith("# A Lesson\n\n- **Type**: lesson\n- **Position**: Topic 1 of 2\n- **Source**: <")
    assert once.count("- **Type**") == 1 and once.rstrip().endswith("Body.")
    # A body with no heading — or one opening with a rule — still opens with `# …`.
    assert mod.with_facts("---\n\nBody.\n", "Named", block).startswith("# Named\n\n- **Type**")


def test_a_formatted_transcript_closes_the_body_once():
    """The extractor reads the body and nothing beside it: captions left as a
    sibling file would never reach the page."""
    once = mod.with_transcript("# A Lesson\n\nBody.\n", "#### [00:00]\nhello there\n", "captions/en.vtt")
    assert once == mod.with_transcript(once, "#### [00:00]\nhello there\n", "captions/en.vtt")
    assert once.count("## Transcript") == 1 and once.rstrip().endswith("hello there") and "`captions/en.vtt`" in once
    assert mod.with_transcript(once, "", None) == "# A Lesson\n\nBody.\n"


def test_only_downloaded_media_is_named_and_never_by_a_path_into_raw(tmp_path):
    (tmp_path / "assets.json").write_text(json.dumps([
        {"type": "hls", "url": "https://cdn-media.circle.so/bcdn_token=t/x/hls/playlist.m3u8",
         "status": "downloaded", "local_path": "../assets/0123456789ab-lesson.mp4"},
        {"type": "hls", "url": "https://cdn-media.circle.so/bcdn_token=t/y/hls/playlist.m3u8", "status": "referenced", "local_path": None},
        {"type": "image", "url": "https://assets-v2.circle.so/avatar", "status": "downloaded", "local_path": "../assets/aa-avatar.png"},
        {"type": "file", "url": "https://assets-v2.circle.so/abc", "status": "downloaded", "local_path": "../../../outside.pdf"},
    ]), encoding="utf-8")
    assert mod.downloaded_media(tmp_path, "_raw/course/leaf--00000000") == ["0123456789ab-lesson.mp4"]
    assert mod.downloaded_media(tmp_path / "nowhere", "_raw/course/leaf--00000000") == []


# --- page names: one page per lesson, whatever two lessons are called ----------


def test_the_page_key_is_the_hosts_filename_rule_plus_what_a_filesystem_folds():
    """`page/note.py::filename_for` is `title.strip() + ".md"` and nothing else,
    so outer whitespace is the one thing the HOST folds; case is what a
    case-insensitive filesystem folds under it."""
    assert mod.page_key("Introduction") == mod.page_key("  Introduction\t") == mod.page_key("INTRODUCTION")
    assert mod.page_key("Introduction") != mod.page_key("Introduction.")  # nothing else is dropped
    assert mod.TITLE_ILLEGAL == '/\\:*?"<>|'  # `page/note.py::ILLEGAL` — a title carrying one is refused


def test_a_later_namesake_is_qualified_and_the_first_is_never_touched():
    taken = {}
    assert mod.unique_title("Introduction ", ["Module One", "aaaaaaaa"], taken) == "Introduction "  # untouched
    assert mod.unique_title("introduction", ["Module Two", "bbbbbbbb"], taken) == "introduction (Module Two)"
    # A qualifier the holder shares tells nothing apart; the next one is used.
    assert mod.unique_title("Introduction", ["Module One", "Topic 3 of 5", "cccccccc"], taken) == "Introduction (Topic 3 of 5)"
    # Nothing meaningful left: the hash. And a name that is itself taken is never handed out.
    assert mod.unique_title("Introduction", ["Module One", "dddddddd"], taken) == "Introduction (dddddddd)"
    assert mod.unique_title("Introduction", ["Module Two"], taken) == "Introduction (Module Two) (2)"
    assert mod.unique_title("Introduction", [], taken) == "Introduction (2)"
    assert len(taken) == 6
    # Venue text never smuggles in a character the host refuses a title for.
    assert mod.unique_title("Introduction", ['Module 3: "Basics" / Extras\n'], taken) == "Introduction (Module 3- -Basics- - Extras)"
    assert not set(mod.qualifier("a/b\\c:d*e?f\"g<h>i|j\x00k")) & set(mod.TITLE_ILLEGAL + "\x00")


def landed_leaf(parent: Path, name: str, url: str, title, position=None) -> dict:
    leaf = parent / name
    leaf.mkdir(parents=True)
    (leaf / "page.md").write_text(f"# {title}\n\nBody of {url}.\n", encoding="utf-8")
    front = {"type": "lesson", **({"position": position} if position else {})}
    (leaf / "capture.json").write_text(json.dumps(
        {"v": 1, "slug": "course", "item": url, "title": title, "body": "page.md", "content_type": "text/markdown",
         "fetched_at": "2026-09-19T00:00:00Z", "frontmatter": front}), encoding="utf-8")
    return {"url": url, "dir": f"_raw/course/{name}", "title": title, "section": None, "root": False}


def titles_on_disk(parent: Path, leaves) -> list:
    return [json.loads((parent / leaf["dir"].split("/")[-1] / "capture.json").read_text(encoding="utf-8")).get("title")
            for leaf in leaves if (parent / leaf["dir"].split("/")[-1] / "capture.json").is_file()]


def test_settling_titles_is_stable_however_often_and_in_whatever_state_it_runs(tmp_path):
    cap = tmp_path / "_raw" / "course" / "c-course-one--aaaaaaaa"
    cap.mkdir(parents=True)
    a = landed_leaf(cap.parent, "a--00000001", f"{TARGET}/sections/1/lessons/1", "Welcome", "Topic 1 of 3")
    b = landed_leaf(cap.parent, "b--00000002", f"{TARGET}/sections/2/lessons/2", "Welcome", "Topic 1 of 3")
    c = landed_leaf(cap.parent, "c--00000003", f"{TARGET}/sections/2/lessons/3", "welcome ", "Topic 2 of 3")
    a["section"], b["section"], c["section"] = "Module One", "Module Two", "Module Two"
    # Planned, never landed: it still holds the name the sidebar gave it.
    ghost = {"url": f"{TARGET}/sections/0/lessons/0", "dir": "_raw/course/ghost--00000000", "title": "Welcome",
             "section": "Module Zero", "root": False}
    plan = {"leaves": [ghost, a, b, c]}
    for _ in range(2):  # idempotent: a second report renames nothing a second time
        mod.settle_titles(cap, plan)
        assert titles_on_disk(cap.parent, [a, b, c]) == [
            "Welcome (Module One)", "Welcome (Module Two)", "welcome (Topic 2 of 3)"]
    # `record` run again puts the plain title back; the next report settles it to the SAME name.
    landed_leaf(cap.parent, "b2--00000002", b["url"], "Welcome", "Topic 1 of 3")
    shutil.copy(cap.parent / "b2--00000002" / "capture.json", cap.parent / "b--00000002" / "capture.json")
    mod.settle_titles(cap, plan)
    assert titles_on_disk(cap.parent, [a, b, c])[1] == "Welcome (Module Two)"
    # Without the ghost the first LANDED leaf keeps its title untouched; no section, no position -> the url's hash.
    solo = tmp_path / "_raw" / "other" / "t--aaaaaaaa"
    solo.mkdir(parents=True)
    x = landed_leaf(solo.parent, "x--00000001", f"{BASE}/c/o/sections/1/lessons/1", "Welcome")
    y = landed_leaf(solo.parent, "y--00000002", f"{BASE}/c/o/sections/1/lessons/2", "Welcome")
    z = landed_leaf(solo.parent, "z--00000003", f"{BASE}/c/o/sections/1/lessons/3", None)  # filed as `page`, its body's stem
    w = landed_leaf(solo.parent, "w--00000004", f"{BASE}/c/o/sections/1/lessons/4", None)
    mod.settle_titles(solo, {"leaves": [x, y, z, w]})
    assert titles_on_disk(solo.parent, [x, y, z, w]) == [
        "Welcome", f"Welcome ({hashlib.sha1(y['url'].encode()).hexdigest()[:8]})", None,
        f"page ({hashlib.sha1(w['url'].encode()).hexdigest()[:8]})"]


def test_the_plan_names_each_lessons_section_off_the_sidebar():
    assert [leaf["section"] for leaf in plan_of()["leaves"]] == ["Section One", "Section One"]


# --- plan → record → report over files, no CLI needed -------------------------


def run_plan(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PLAN), *map(str, args)], capture_output=True, text=True, check=False)


def converted(leaf: Path, fixture: str, url: str) -> None:
    """What `capture_lesson.py` leaves, then this unit's converter over it."""
    leaf.mkdir(parents=True, exist_ok=True)
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / fixture / name, leaf / name)
    subprocess.run(["uv", "run", "--script", str(TO_MARKDOWN), str(leaf / "page.html"), "--base-url", url],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def slice_dir(tmp_path) -> Path:
    """A job's `_raw/<slug>/` with the ticket's capture dir holding the ticket
    and the root capture."""
    cap = tmp_path / "_raw" / "course" / "c-course-one--aaaaaaaa"
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(ticket()), encoding="utf-8")
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / "root" / name, cap / name)
    return cap


def test_report_is_partial_until_every_planned_leaf_is_on_disk(slice_dir):
    assert run_plan("plan", slice_dir).returncode == 0
    plan = json.loads((slice_dir / "plan.json").read_text(encoding="utf-8"))
    first = slice_dir.parent / plan["leaves"][0]["dir"].split("/")[-1]
    converted(first, "lesson-1", L1)
    assert run_plan("record", slice_dir, L1).returncode == 0

    done = run_plan("report", slice_dir)
    report = json.loads((slice_dir / "report.json").read_text(encoding="utf-8"))
    assert done.returncode == 0 and report["outcome"] == "partial" and "1 of 2" in report["reason"]
    assert report["captured"] == [{"item": L1, "dir": plan["leaves"][0]["dir"], "title": "Getting the Frame Right"}]
    assert (report["v"], report["ticket"], report["written"], report["discovered"]) == (1, "0123456789ab", [], [])


def test_auth_expiry_names_every_unreached_lesson_as_auth(slice_dir):
    assert run_plan("plan", slice_dir).returncode == 0
    done = run_plan("report", slice_dir, "--auth-expired")
    report = json.loads(done.stdout)
    assert done.returncode == 1 and report["outcome"] == "failed"
    assert report["reason"].startswith("auth_expired:community.example.invalid")
    assert report["missing"] == [{"host": "community.example.invalid", "url": url, "why": "auth"} for url in (L1, L2)]


def test_a_root_that_never_rendered_still_leaves_a_report(tmp_path):
    cap = tmp_path / "_raw" / "course" / "c-course-one--aaaaaaaa"
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(ticket()), encoding="utf-8")
    assert run_plan("plan", cap).returncode == 1  # no meta.json: nothing to plan from
    done = run_plan("report", cap, "--auth-expired")
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert done.returncode == 1 and report["outcome"] == "failed" and report["captured"] == []
    assert report["missing"] == [{"host": "community.example.invalid", "url": TARGET, "why": "auth"}]


def test_everything_already_held_is_skipped_not_failed(slice_dir):
    (slice_dir / "ticket.json").write_text(
        json.dumps(ticket(known=[{"resource": L1, "harvested_at": None}, {"resource": L2, "harvested_at": None}])),
        encoding="utf-8")
    assert run_plan("plan", slice_dir).returncode == 0
    done = run_plan("report", slice_dir)
    assert done.returncode == 0 and json.loads(done.stdout)["outcome"] == "skipped"


def test_record_refuses_a_url_the_plan_does_not_hold(slice_dir):
    assert run_plan("plan", slice_dir).returncode == 0
    done = run_plan("record", slice_dir, OTHER_SPACE)
    assert done.returncode == 1 and "not a leaf" in done.stderr


def test_a_missing_asset_host_makes_a_full_capture_partial(slice_dir):
    assert run_plan("plan", slice_dir).returncode == 0
    plan = json.loads((slice_dir / "plan.json").read_text(encoding="utf-8"))
    for leaf, fixture in zip(plan["leaves"], ("lesson-1", "lesson-2")):
        converted(slice_dir.parent / leaf["dir"].split("/")[-1], fixture, leaf["url"])
        assert run_plan("record", slice_dir, leaf["url"]).returncode == 0
    assert json.loads(run_plan("report", slice_dir).stdout)["outcome"] == "ok"
    denied = run_plan("report", slice_dir, "--missing", "fast.wistia.com", "https://fast.wistia.com/embed/medias/x.m3u8", "denied")
    report = json.loads(denied.stdout)
    assert report["outcome"] == "partial" and len(report["captured"]) == 2 and report["missing"][0]["why"] == "denied"
    assert run_plan("report", slice_dir, "--missing", "h", "https://h/x", "paywalled").returncode == 2


# --- END TO END: the real job, the real extractor ------------------------------


def test_one_ticket_walks_the_section_and_every_lesson_becomes_a_page(ops, env, wiki):
    job = declared_job(ops, env, wiki, UNIT, TARGET)
    assert job.record["harvest"]["scope"] == "section"  # the manifest's default, off the real record
    root_leaf = f"c-course-one--{hashlib.sha1(TARGET.encode()).hexdigest()[:8]}"
    cap = ticket_in(wiki, job, root_leaf, unit=UNIT, item=TARGET, hosts=["community.example.invalid"])
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / "root" / name, cap / name)

    # Paths are wiki-relative with the wiki root as cwd — what `llm-wiki-ops run` gives a script.
    rel = cap.relative_to(wiki)
    planned = subprocess.run([sys.executable, str(PLAN), "plan", str(rel)], cwd=wiki, capture_output=True, text=True)
    assert planned.returncode == 0, planned.stderr
    plan = json.loads(planned.stdout)
    assert [leaf["url"] for leaf in plan["leaves"]] == [L1, L2]

    for leaf, fixture in zip(plan["leaves"], ("lesson-1", "lesson-2")):
        assert leaf["dir"].startswith(f"_raw/{job.slug}/") and len(leaf["dir"].split("/")) == 3
        converted(wiki / leaf["dir"], fixture, leaf["url"])
        if fixture == "lesson-1":  # what the plugin's format_transcript.py leaves from captions/en.vtt
            (wiki / leaf["dir"] / "transcript.md").write_text("#### [00:00]\nWelcome to the quokka lesson.\n", encoding="utf-8")
        for _ in range(2):  # a respawned worker records again: nothing may stack
            done = subprocess.run([sys.executable, str(PLAN), "record", str(rel), leaf["url"], "--author", "Ada Example"],
                                  cwd=wiki, capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
        record = json.loads((wiki / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))
        assert (record["slug"], record["item"], record["body"], record["content_type"]) == (
            job.slug, leaf["url"], "page.md", "text/markdown")
        assert not set(record["frontmatter"]) & set(mod.HOST_OWNED)
    assert record["frontmatter"] == {
        "type": "lesson", "course": "Course One | Example Community", "space": "course-one", "section_id": "111",
        "lesson_id": "2002", "position": "Topic 2 of 2", "duration": "12:30", "author": "Ada Example"}

    reported = subprocess.run([sys.executable, str(PLAN), "report", str(rel)], cwd=wiki, capture_output=True, text=True)
    assert reported.returncode == 0, reported.stderr
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and report["ticket"] == "0123456789ab" and report["missing"] == []
    assert [c["dir"] for c in report["captured"]] == [leaf["dir"] for leaf in plan["leaves"]]
    assert str(rel) not in [c["dir"] for c in report["captured"]]  # the space root is the listing, not a page

    # `apply` mints one process ticket per captured[].dir; each is the real extractor over that leaf.
    texts = []
    for captured in report["captured"]:
        (page,) = extracted(ops, env, wiki, wiki / captured["dir"])
        assert page.is_relative_to(wiki / job.dest)
        texts.append(page.read_text(encoding="utf-8"))
    first, second = texts
    assert "title: Getting the Frame Right" in first and f"resource: {L1}" in first and "status: draft" in first
    assert "marmalade-sandwich rule" in first and "> A frame is a promise about what matters." in first
    assert "- **Position**: Topic 1 of 2" in first and "- **Captions**: captions/en.vtt" in first
    assert first.count("## Transcript") == 1 and "Welcome to the quokka lesson." in first and first.count("- **Type**") == 1
    assert "## Transcript" not in second
    assert "heliotrope question" in second and "- **Author**: Ada Example" in second
    assert "https://assets-v2.circle.so/abc123def" in second  # the extensionless Resources link survives
    for text in texts:
        assert len(re.findall(r"^---$", text, flags=re.M)) == 2, "one frontmatter block, the extractor's own"
        assert "Powered by a community platform" not in text and "logo123" not in text  # chrome stripped


def test_two_lessons_with_one_title_land_as_two_pages(ops, env, wiki):
    """The extractor files a page under its title and overwrites what is there:
    before `report` settled titles, the second "Introduction" of a course WAS
    the first one's page, and both process tickets said ok."""
    target = f"{BASE}/c/course-names"
    one, two = f"{target}/sections/111/lessons/3001", f"{target}/sections/222/lessons/3002"
    job = declared_job(ops, env, wiki, UNIT, target, slug="port-channel-circle-names")
    cap = ticket_in(wiki, job, f"c-course-names--{hashlib.sha1(target.encode()).hexdigest()[:8]}", unit=UNIT, item=target)
    (cap / "meta.json").write_text(json.dumps({"url": target, "title": "Course Names", "discovered_lesson_links": [
        {"href": f"{target}/sections/111", "text": "Module One"}, {"href": one, "text": "Introduction\n\n01:00"},
        {"href": f"{target}/sections/222", "text": "Module Two"}, {"href": two, "text": "Introduction\n\n02:00"},
    ]}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))

    def plan_cli(*args):
        done = subprocess.run([sys.executable, str(PLAN), *args], cwd=wiki, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return done

    plan = json.loads(plan_cli("plan", rel).stdout)
    assert [(leaf["title"], leaf["section"]) for leaf in plan["leaves"]] == [
        ("Introduction", "Module One"), ("Introduction", "Module Two")]
    for leaf, fixture in zip(plan["leaves"], ("lesson-1", "lesson-2")):
        converted(wiki / leaf["dir"], fixture, leaf["url"])
        plan_cli("record", rel, leaf["url"])
    reports = []
    for _ in range(2):  # the report is written again by a respawned worker: same names
        plan_cli("report", rel)
        reports.append(json.loads((cap / "report.json").read_text(encoding="utf-8")))
    report = reports[-1]

    pages = [extracted(ops, env, wiki, wiki / c["dir"])[0] for c in report["captured"]]
    assert len({page.resolve() for page in pages}) == 2 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    assert [[c["title"] for c in r["captured"]] for r in reports] == [["Introduction", "Introduction (Module Two)"]] * 2
    assert [page.name for page in pages] == ["Introduction.md", "Introduction (Module Two).md"]
    first, second = (page.read_text(encoding="utf-8") for page in pages)
    assert f"resource: {one}" in first and "marmalade-sandwich rule" in first  # still the FIRST lesson's page
    assert f"resource: {two}" in second and "heliotrope question" in second


def test_capture_lesson_takes_its_url_off_the_ticket_when_none_is_given(tmp_path):
    spec_c = importlib.util.spec_from_file_location("circle_capture_lesson", SCRIPTS / "capture_lesson.py")
    capture = importlib.util.module_from_spec(spec_c)
    spec_c.loader.exec_module(capture)  # playwright is imported inside main(), never here
    assert capture.ticket_target(tmp_path) is None
    (tmp_path / "ticket.json").write_text(json.dumps(ticket()), encoding="utf-8")
    assert capture.ticket_target(tmp_path) == TARGET
    (tmp_path / "ticket.json").write_text("[1, 2]", encoding="utf-8")
    assert capture.ticket_target(tmp_path) is None
