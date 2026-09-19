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
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import declared_job, extracted, ticket_in

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "channel-circle" / "scripts"
PLAN = SCRIPTS / "section_plan.py"
CAPTURE = SCRIPTS / "capture_lesson.py"
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
        # THE DOCUMENTED WAY: a lesson is `--leaf N`, never a url on a command line.
        (wiki / leaf["dir"]).mkdir(parents=True, exist_ok=True)
        for name in ("page.html", "meta.json"):
            shutil.copy(FIX / fixture / name, wiki / leaf["dir"] / name)
        number = str(leaf["order"])
        rendered = subprocess.run([sys.executable, str(PLAN), "render", str(rel), "--leaf", number],
                                  cwd=wiki, capture_output=True, text=True)
        assert rendered.returncode == 0, rendered.stderr
        assert json.loads(rendered.stdout.strip().splitlines()[-1])["page"] == f"{leaf['dir']}/page.md"
        if fixture == "lesson-1":  # what the plugin's format_transcript.py leaves from the caption track
            (wiki / leaf["dir"] / "transcript.md").write_text("#### [00:00]\nWelcome to the quokka lesson.\n", encoding="utf-8")
        (wiki / leaf["dir"] / "author.txt").write_text("Ada Example\n", encoding="utf-8")  # a file, never a shell word
        for _ in range(2):  # a respawned worker records again: nothing may stack
            done = subprocess.run([sys.executable, str(PLAN), "record", str(rel), "--leaf", number],
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


# =============================================================================
# Review fixes (2026-09-19). Each case below fails without the fix it names.
# =============================================================================

HOSTILE_HREF = f"{TARGET}/sections/111/lessons/2003;$(touch${{IFS}}PWNED)"  # the review's exact href


def load_capture():
    spec_c = importlib.util.spec_from_file_location("circle_capture_lesson_fixes", CAPTURE)
    capture = importlib.util.module_from_spec(spec_c)
    spec_c.loader.exec_module(capture)  # playwright is imported inside main(), after everything tested here
    return capture


def cli(script: Path, *args, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """A unit script THE DOCUMENTED WAY: cwd is the wiki root — what
    `llm-wiki-ops run` gives it — and every path argument is wiki-relative."""
    assert not any(os.path.isabs(str(a)) for a in args), "the documented form takes wiki-relative paths"
    return subprocess.run([sys.executable, str(script), *map(str, args)], cwd=cwd, env=env, capture_output=True, text=True)


def last_json(done: subprocess.CompletedProcess) -> dict:
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture
def wiki_root(tmp_path) -> tuple[Path, str]:
    """A bare wiki root holding one spawned ticket and its root capture:
    `(root, the ticket's wiki-relative capture_dir)`."""
    rel = "_raw/course/c-course-one--aaaaaaaa"
    cap = tmp_path / rel
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(ticket()), encoding="utf-8")
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / "root" / name, cap / name)
    return tmp_path, rel


def fill(root: Path, leaf: dict, fixture: str = "lesson-1", body: str | None = None) -> Path:
    """What `capture_lesson.py --leaf` leaves in a leaf's dir — and, given a
    `body`, what `render` would."""
    directory = root / leaf["dir"]
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / fixture / name, directory / name)
    if body is not None:
        (directory / "page.md").write_text(body, encoding="utf-8")
    return directory


# --- Rule 1: the title is a legal filename -------------------------------------


def test_safe_title_is_what_the_hosts_filename_rule_accepts():
    illegal = set('/\\:*?"<>|')  # `page/note.py::ILLEGAL`
    assert mod.safe_title("Lesson 3: Pricing") == "Lesson 3 - Pricing"
    assert mod.safe_title('What is "X"? A/B <test> | more*') == "What is 'X' A-B (test) - more"
    assert mod.safe_title(".hidden. ") == "hidden" and mod.safe_title(" . ..dots") == "dots"
    assert mod.safe_title("a\x00b\tc\nd\x7fe f") == "a b c d e f"  # control chars, newlines, tabs: a space
    for empty in ("", None, "   ", "???", "\n", "..."):
        assert mod.safe_title(empty) == "Untitled" and mod.safe_title(empty, fallback="Untitled lesson") == "Untitled lesson"
    long = mod.safe_title("x" * 500)
    assert len(long) == mod.TITLE_MAX + 1 and long.endswith("…")
    cjk = mod.safe_title("課" * 100)  # 300 bytes: past what a filename holds, though only 100 characters
    assert len(cjk.encode("utf-8")) <= mod.TITLE_MAX_BYTES + len("…".encode("utf-8")) and cjk.endswith("…")
    assert len((cjk + ".md").encode("utf-8")) <= 255
    assert mod.safe_title("Plain title") == "Plain title"  # untouched under every cap
    for hostile in ('a/b\\c:d*e?f"g<h>i|j', "\x01.x", "..\\..\\etc"):
        got = mod.safe_title(hostile)
        assert not set(got) & illegal and not got.startswith(".") and all(ord(ch) >= 32 for ch in got)


def test_the_plan_carries_the_safe_title_and_keeps_the_venues_own_beside_it():
    meta = {"title": "C", "discovered_lesson_links": [
        {"href": L1, "text": "Lesson 3: Pricing?\n\n04:07"}, {"href": L2, "text": "Reading the Room\n\n12:30"}]}
    t = ticket()
    leaves = mod.build_plan(t, meta, capture_rel=t["capture_dir"])["leaves"]
    assert [(leaf["title"], leaf["source_title"]) for leaf in leaves] == [
        ("Lesson 3 - Pricing", "Lesson 3: Pricing?"), ("Reading the Room", None)]


def test_titles_differing_only_in_a_refused_character_collide_once_safe_and_are_told_apart(wiki_root):
    """`A/B` and `A-B` are two titles at the venue and ONE filename: the safe
    form is what `capture.json` holds BEFORE titles are settled, and what a
    lesson that never landed reserves."""
    root, rel = wiki_root
    three = f"{TARGET}/sections/222/lessons/2003"
    (root / rel / "meta.json").write_text(json.dumps({"title": "C", "discovered_lesson_links": [
        {"href": f"{TARGET}/sections/111", "text": "Module One"}, {"href": L1, "text": "A/B testing\n\n01:00"},
        {"href": L2, "text": "A-B testing\n\n01:00"},
        {"href": f"{TARGET}/sections/222", "text": "Module Two"}, {"href": three, "text": "A:B testing\n\n01:00"},
    ]}), encoding="utf-8")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    # Leaf 1 never lands: it still RESERVES `A-B testing`, its safe form.
    for leaf in plan["leaves"][1:]:
        fill(root, leaf, body=f"# {leaf['source_title'] or leaf['title']}\n\nBody.\n")
        assert cli(PLAN, "record", rel, "--leaf", leaf["order"], cwd=root).returncode == 0
    report = json.loads(cli(PLAN, "report", rel, cwd=root).stdout)
    titles = [c["title"] for c in report["captured"]]
    # Lesson 2 shares lesson 1's section, so the section tells nothing apart: its url's hash does.
    assert titles == [f"A-B testing ({hashlib.sha1(L2.encode()).hexdigest()[:8]})", "A -B testing"], (
        "the unlanded namesake's SAFE title was not reserved")
    assert not any(set(t) & set(mod.TITLE_ILLEGAL) for t in titles)


def last_json_plan(done: subprocess.CompletedProcess) -> dict:
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_qualifier_never_pushes_a_title_back_over_the_byte_cap():
    taken: dict = {}
    base = mod.safe_title("課" * 100)
    first = mod.fitted_title(base, ["Module One", "aaaaaaaa"], taken)
    second = mod.fitted_title(base, ["Module Two — 第二部分の長い名前", "bbbbbbbb"], taken)
    assert first == base and second != first and mod.page_key(first) != mod.page_key(second)
    assert second.endswith("(Module Two — 第二部分の長い名前)"), "the QUALIFIER is kept; the base is what is trimmed"
    assert len(second.encode("utf-8")) <= mod.TITLE_MAX_BYTES + len("…".encode("utf-8"))  # safe_title's own ceiling
    assert len((second + ".md").encode("utf-8")) <= 255
    # Settled once, it stays settled: the next pass hands every title back as it is.
    again: dict = {}
    assert [mod.fitted_title(t, ["x"], again) for t in (first, second)] == [first, second]
    # A short title is exactly what `unique_title` answers.
    a, b = {}, {}
    assert mod.fitted_title("Intro", ["M1"], a) == mod.unique_title("Intro", ["M1"], b)
    assert mod.fitted_title("Intro", ["M2"], a) == mod.unique_title("Intro", ["M2"], b) == "Intro (M2)" and a == b


def test_refused_titles_and_a_long_cjk_title_all_land_through_the_real_extractor(ops, env, wiki):
    """THE BLOCKER: `Lesson 3: Pricing` was written raw, harvest said ok, and
    the process ticket was refused — `a title cannot carry ':'`."""
    target = f"{BASE}/c/course-titles"
    urls = [f"{target}/sections/1/lessons/{n}" for n in (1, 2, 3)]
    venue = ['Lesson 3: Pricing? A/B "tests" <now>', ".hidden: a leading dot|pipe\\slash*", "課" * 100]
    job = declared_job(ops, env, wiki, UNIT, target, slug="port-channel-circle-titles")
    cap = ticket_in(wiki, job, f"c-course-titles--{hashlib.sha1(target.encode()).hexdigest()[:8]}", unit=UNIT, item=target)
    (cap / "meta.json").write_text(json.dumps({"url": target, "title": "Course Titles", "discovered_lesson_links": [
        {"href": urls[0], "text": f"{venue[0]}\n\n01:00"}, {"href": urls[1], "text": f"{venue[1]}\n\n02:00"},
        {"href": urls[2], "text": ("課" * 100)[:80]},  # the capture's 80-char cap: it names no title; the H1 does
    ]}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=wiki))
    for leaf, title in zip(plan["leaves"], venue):
        fill(wiki, leaf, body=f"# {title}\n\nThe body of {leaf['order']}.\n")
        assert cli(PLAN, "record", rel, "--leaf", leaf["order"], cwd=wiki).returncode == 0
    report = json.loads(cli(PLAN, "report", rel, cwd=wiki).stdout)
    assert report["outcome"] == "ok" and len(report["captured"]) == 3

    for captured, title in zip(report["captured"], venue):
        record = json.loads((wiki / captured["dir"] / "capture.json").read_text(encoding="utf-8"))
        assert record["title"] == captured["title"] == mod.safe_title(title)
        assert record["frontmatter"]["source_title"] == title  # the venue's own title is kept…
        (page,) = extracted(ops, env, wiki, wiki / captured["dir"])  # …and the page LANDS
        assert page.is_file() and page.is_relative_to(wiki / job.dest) and page.name == f"{record['title']}.md"
        assert f"\n# {title}\n" in page.read_text(encoding="utf-8")  # …and stays the H1
    assert [c["title"] for c in report["captured"]][:2] == [
        "Lesson 3 - Pricing A-B 'tests' (now)", "hidden - a leading dot-pipe-slash"]


# --- S11: a venue url is data, never shell --------------------------------------


def test_a_hostile_href_never_reaches_a_plan(wiki_root):
    root, rel = wiki_root
    meta = root_meta()
    meta["discovered_lesson_links"] += [
        {"href": HOSTILE_HREF, "text": "Pwn\n\n00:01"},
        {"href": f"{TARGET}/sections/111/lessons/2004?a=1&b=`id`", "text": "x"},
        {"href": "https://user:pw@community.example.invalid/c/course-one/sections/111/lessons/2005", "text": "x"},
        {"href": "javascript:alert(1)//lessons/1", "text": "x"},
        {"href": "http://[::1/c/course-one/sections/1/lessons/1", "text": "x"},  # urlsplit raises on it
        {"href": f"{BASE}:99999/c/course-one/sections/111/lessons/2006", "text": "x"},
        {"href": f"{TARGET}/sections/111/lessons/2007 8", "text": "x"},
    ]
    (root / rel / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    done = cli(PLAN, "plan", rel, cwd=root)
    plan = last_json_plan(done)
    assert [leaf["url"] for leaf in plan["leaves"]] == [L1, L2]
    unsafe = [d for d in plan["dropped"] if d["why"] == "unsafe_url"]
    assert len(unsafe) == 7
    # Nothing a plan WRITES DOWN is the hostile text either: not in plan.json, not on stdout.
    for text in ((root / rel / "plan.json").read_text(encoding="utf-8"), done.stdout):
        assert not re.search(r"[;$`(){}&]|\bid\b", "".join(d["url"] for d in json.loads(text)["dropped"]))
        assert "$(touch" not in text and "`id`" not in text
    for url in (L1, L2, f"{BASE}/c/x/sections/1/lessons/2?page=2"):
        assert mod.clean_url(url) == url
    for url in (HOSTILE_HREF, "ftp://h/x", "//h/x", "https:///nohost", "https://h/a b", "https://h/a'b", 'https://h/a"b',
                "https://h/a|b", "https://h/a>b", "https://h/a\\b", "https://h/a\nb", None, 7, "https://" + "h" * 3000):
        assert mod.clean_url(url) is None, url
    # A hostile TARGET is refused outright rather than planned around.
    with pytest.raises(ValueError, match="target"):
        mod.build_plan(ticket(target=HOSTILE_HREF, item=HOSTILE_HREF), root_meta(), capture_rel="_raw/course/x--00000000")


def test_no_documented_per_leaf_command_takes_a_url():
    """The worker's command line is a shell. SKILL.md's per-leaf commands name
    a lesson `--leaf <n>` and nothing in its code blocks interpolates a url."""
    skill = (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8")
    blocks = "\n".join(re.findall(r"^```[^\n]*\n(.*?)^```", skill, flags=re.M | re.S))
    assert "--leaf <n>" in blocks
    for placeholder in ("<leaf.url>", "<url>", "<lesson-url>", "--base-url", "<course-url>"):
        assert placeholder not in blocks, placeholder


def stub_front_door(tmp_path: Path, answer: dict, rc: int = 0) -> tuple[dict, Path]:
    """A recording `llm-wiki-ops` first on PATH, as `test_scripts_front_door.py` does it."""
    bin_dir, seen = tmp_path / "stub-bin", tmp_path / "seen.json"
    bin_dir.mkdir()
    stub = bin_dir / "llm-wiki-ops"
    stub.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'inherited': sorted(k for k in "
        f"('LLM_WIKI_OPS_DISPATCHED', 'CLAUDE_PROJECT_DIR') if k in os.environ)}}, open({str(seen)!r}, 'w'))\n"
        f"sys.stdout.write({json.dumps(answer)!r})\nsys.exit({rc})\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "LLM_WIKI_OPS_DISPATCHED": "1", "CLAUDE_PROJECT_DIR": str(tmp_path / "another-wiki")}
    return env, seen


def test_detect_hands_the_plugins_asset_script_the_planned_url_as_an_argument_list(wiki_root, tmp_path_factory):
    root, rel = wiki_root
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    leaf = plan["leaves"][1]
    env, seen = stub_front_door(tmp_path_factory.mktemp("door"), {})
    assert cli(PLAN, "detect", rel, "--leaf", 2, cwd=root, env=env).returncode == 0
    got = json.loads(seen.read_text(encoding="utf-8"))
    assert got["argv"] == ["run", "skills/harvest/scripts/assets.py", "detect", f"{leaf['dir']}/page.html", "--base-url", L2,
                           "--network-log", f"{leaf['dir']}/net.json", "--out", f"{leaf['dir']}/assets.json"]
    assert got["inherited"] == [] and Path(got["cwd"]) == root.resolve()  # a nested call is not a loop, and binds by cwd
    assert cli(PLAN, "detect", rel, "--leaf", 9, cwd=root, env=env).returncode == 1  # not a leaf of this plan
    # A plan.json somebody tampered with is still not a way to a shell word.
    plan["leaves"][0]["url"] = HOSTILE_HREF
    (root / rel / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    seen.unlink()
    assert cli(PLAN, "detect", rel, "--leaf", 1, cwd=root, env=env).returncode == 1 and not seen.exists()


def test_capture_lesson_reads_a_leafs_url_and_dir_off_the_plan(wiki_root):
    root, rel = wiki_root
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    capture = load_capture()
    url, out, is_target, refused = capture.resolve_job(root, plan=f"{rel}/plan.json", leaf=2)
    assert (url, out, is_target, refused) == (L2, root / plan["leaves"][1]["dir"], False, None)
    # With neither url nor leaf: the ticket's own target, into the ticket's own dir, resolved against the ROOT.
    assert capture.resolve_job(root, out=rel) == (TARGET, root / rel, True, None)
    assert capture.resolve_job(root, url=L1, out="_raw/course/by-hand")[:3] == (L1, root / "_raw/course/by-hand", False)
    for kwargs, code in (({"plan": f"{rel}/plan.json", "leaf": 9}, 4), ({"leaf": 1}, 4), ({"plan": f"{rel}/nope.json", "leaf": 1}, 4),
                         ({}, 4), ({"out": "_raw/course/empty"}, 4), ({"url": "file:///etc/passwd", "out": rel}, 4),
                         ({"plan": f"{rel}/plan.json", "leaf": 1, "out": "_raw/course/elsewhere"}, 4)):
        assert capture.resolve_job(root, **kwargs)[3][0] == code, kwargs


def test_capture_lesson_the_documented_way_clears_a_stale_report_and_reads_the_profile_answer(wiki_root, tmp_path_factory):
    """No browser here — and none is needed to reach the answer that matters:
    `<root> --out <capture_dir>`, cwd the wiki root, relative paths."""
    root, rel = wiki_root
    (root / rel / "report.json").write_text('{"outcome": "ok", "captured": [{"dir": "stale"}]}', encoding="utf-8")
    env, seen = stub_front_door(tmp_path_factory.mktemp("door"), {"domain": "community.example.invalid", "path": "/nowhere/profile", "exists": False})
    done = cli(CAPTURE, ".", "--out", rel, cwd=root, env=env)
    assert done.returncode == 2 and "no auth profile" in done.stderr, done.stderr  # absent: a login is what fixes it
    assert not (root / rel / "report.json").exists(), "a respawn must not be read as the success an earlier pull had"
    assert json.loads(seen.read_text(encoding="utf-8"))["argv"] == ["--json", "credential", "profile-dir", "community.example.invalid"]
    # The store unreachable — what a jail with no grant on it answers: 5, never 2.
    env, _ = stub_front_door(tmp_path_factory.mktemp("door5"), {"error": "permission denied"}, rc=1)
    assert cli(CAPTURE, ".", "--out", rel, cwd=root, env=env).returncode == 5
    # `--leaf`, the documented way.
    assert cli(PLAN, "plan", rel, cwd=root).returncode == 0
    env, seen = stub_front_door(tmp_path_factory.mktemp("door2"), {"domain": "community.example.invalid", "path": "/nowhere", "exists": False})
    done = cli(CAPTURE, ".", "--plan", f"{rel}/plan.json", "--leaf", 1, cwd=root, env=env)
    assert done.returncode == 2 and (root / last_json_plan(cli(PLAN, "plan", rel, cwd=root))["leaves"][0]["dir"]).is_dir()
    assert cli(CAPTURE, ".", "--plan", f"{rel}/plan.json", "--leaf", 7, cwd=root, env=env).returncode == 4


# --- S5: stop before the cap, and make "resume" real ----------------------------


def spawned_ago(root: Path, rel: str, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(root / rel / "ticket.json", (then, then))


def test_the_deadline_is_keyed_to_the_spawn_and_a_hand_run_has_none(wiki_root, tmp_path):
    root, rel = wiki_root
    spawned_ago(root, rel, 100)
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    assert abs(plan["deadline_epoch"] - (time.time() - 100 + 1500)) < 5 and plan["deadline"].endswith("Z")
    assert abs(last_json_plan(cli(PLAN, "plan", rel, "--budget-s", 60, cwd=root))["deadline_epoch"] - (time.time() - 40)) < 5
    # A re-dispatch rewrites ticket.json: the next slice's deadline is its own.
    spawned_ago(root, rel, 0)
    assert last_json_plan(cli(PLAN, "plan", rel, cwd=root))["deadline_epoch"] > time.time() + 1400
    hand = tmp_path / "hand" / "_raw" / "course" / "r--00000000"
    hand.mkdir(parents=True)
    shutil.copy(FIX / "root" / "meta.json", hand / "meta.json")
    by_hand = cli(PLAN, "plan", "_raw/course/r--00000000", "--target", TARGET, "--slug", "course", cwd=tmp_path / "hand")
    assert last_json_plan(by_hand)["deadline"] is None and not mod.past_deadline(last_json_plan(by_hand))


def test_past_the_deadline_record_says_stop_and_no_new_lesson_is_started(wiki_root, tmp_path_factory):
    root, rel = wiki_root
    spawned_ago(root, rel, 1600)  # 100 s past the 1500 s budget, 200 s before the kill
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    fill(root, plan["leaves"][0], body="# Getting the Frame Right\n\nBody.\n")
    done = cli(PLAN, "record", rel, "--leaf", 1, cwd=root)
    answer = last_json(done)
    assert done.returncode == 3 and answer["stop"] is True and answer["deadline_passed"] is True and answer["remaining"] == [2]
    assert (root / plan["leaves"][0]["dir"] / "capture.json").is_file(), "exit 3 means recorded AND stop — the leaf landed"
    # …and the next lesson is refused before any browser (or credential lookup) is started.
    env, seen = stub_front_door(tmp_path_factory.mktemp("door"), {"path": "/x", "exists": True})
    refused = cli(CAPTURE, ".", "--plan", f"{rel}/plan.json", "--leaf", 2, cwd=root, env=env)
    assert refused.returncode == 6 and "deadline" in refused.stderr and not seen.exists()
    assert not (root / plan["leaves"][1]["dir"]).exists()

    report = json.loads(cli(PLAN, "report", rel, cwd=root).stdout)
    assert report["outcome"] == "partial" and [c["item"] for c in report["captured"]] == [L1]
    # The reason tells the operator exactly what resumes an `every: once` job — nothing does by itself.
    for said in ("1 of 2", "deadline passed", "pipeline queue retry 0123456789ab", "pipeline edit course every=1d", "NOT pulled again"):
        assert said in report["reason"], said

    # Inside the budget the same record is plain success.
    spawned_ago(root, rel, 10)
    assert cli(PLAN, "plan", rel, cwd=root).returncode == 0
    inside = cli(PLAN, "record", rel, "--leaf", 1, cwd=root)
    assert inside.returncode == 0 and last_json(inside)["stop"] is False and last_json(inside)["remaining"] == [2]


def test_a_killed_slices_lessons_are_landed_in_the_next_plan_and_reported_by_it(wiki_root):
    """The cap kills a slice with NO report: nothing is minted, `known[]` does
    not grow, and the retry used to re-plan every lesson in the same order and
    die at the same place. What survives the kill is the disk."""
    root, rel = wiki_root
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    assert [leaf["landed"] for leaf in plan["leaves"]] == [False, False]
    fill(root, plan["leaves"][0], body="# Getting the Frame Right\n\nBody.\n")
    assert cli(PLAN, "record", rel, "--leaf", 1, cwd=root).returncode == 0
    # The report is cheap and re-runnable: written after EVERY leaf, it is already truthful when the kill comes.
    early = json.loads(cli(PLAN, "report", rel, cwd=root).stdout)
    assert early["outcome"] == "partial" and len(early["captured"]) == 1

    spawned_ago(root, rel, 0)  # …killed; the operator re-queues; the spawner rewrites ticket.json; a new slice plans:
    again = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    assert [leaf["landed"] for leaf in again["leaves"]] == [True, False]
    assert not (root / rel / "report.json").exists(), "`plan` clears the last slice's report before anything else"
    fill(root, again["leaves"][1], "lesson-2", body="# Reading the Room\n\nBody.\n")
    assert cli(PLAN, "record", rel, "--leaf", 2, cwd=root).returncode == 0
    final = json.loads(cli(PLAN, "report", rel, cwd=root).stdout)
    assert final["outcome"] == "ok" and [c["item"] for c in final["captured"]] == [L1, L2]
    # A record left in a leaf's dir by some OTHER url is not this lesson landed.
    record_path = root / again["leaves"][1]["dir"] / "capture.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record_path.write_text(json.dumps({**record, "item": OTHER_SPACE}), encoding="utf-8")
    assert [leaf["landed"] for leaf in last_json_plan(cli(PLAN, "plan", rel, cwd=root))["leaves"]] == [True, False]


# --- S7: a refresh ticket re-fetches exactly one lesson --------------------------


def refresh_ticket(**harvest) -> dict:
    leaf = mod.leaf_dir("course", L1)  # `mint_refresh`: the capture dir is the resource's own
    return ticket(target=L1, item=L1, capture_dir=leaf, refresh=True, resource=L1, prev_harvested_at="2026-09-01T00:00:00Z",
                  known=[{"resource": L1, "harvested_at": "2026-09-01T00:00:00Z"}, {"resource": L2, "harvested_at": None}],
                  harvest={"scope": "section", "access": "licensed", "exclude_urls": [], "refresh": "30d", **harvest})


@pytest.mark.parametrize("scope", ["section", "domain", "page"])
def test_a_refresh_plans_exactly_its_resource_though_known_holds_it(scope):
    t = refresh_ticket(scope=scope)
    plan = mod.build_plan(t, json.loads((FIX / "lesson-1" / "meta.json").read_text(encoding="utf-8")), capture_rel=t["capture_dir"])
    assert plan["refresh"] is True and plan["dropped"] == []  # the rest of the section is another ticket's
    assert [(leaf["url"], leaf["dir"], leaf["root"]) for leaf in plan["leaves"]] == [(L1, t["capture_dir"], True)]


def test_a_refresh_forces_the_recapture_and_reports_it_captured(tmp_path):
    t = refresh_ticket()
    cap = tmp_path / t["capture_dir"]
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(t), encoding="utf-8")
    # What the FIRST pull left here — the directory is the page's own, stable across pulls.
    (cap / "page.md").write_text("# Getting the Frame Right\n\nTHE OLD BODY.\n", encoding="utf-8")
    (cap / "capture.json").write_text(json.dumps({"item": L1, "title": "Getting the Frame Right", "body": "page.md"}), encoding="utf-8")
    (cap / "report.json").write_text('{"outcome": "ok"}', encoding="utf-8")
    shutil.copy(FIX / "lesson-1" / "meta.json", cap / "meta.json")  # this run's root capture IS the lesson
    plan = last_json_plan(cli(PLAN, "plan", t["capture_dir"], cwd=tmp_path))
    assert [leaf["landed"] for leaf in plan["leaves"]] == [False]
    assert not any((cap / name).exists() for name in ("capture.json", "page.md", "report.json"))
    # Nothing fetched again -> nothing captured -> failed; never the old record read as this run's.
    nothing = cli(PLAN, "report", t["capture_dir"], cwd=tmp_path)
    assert nothing.returncode == 1 and json.loads(nothing.stdout)["outcome"] == "failed"
    (cap / "page.md").write_text("# Getting the Frame Right\n\nThe body, fetched again.\n", encoding="utf-8")
    assert cli(PLAN, "record", t["capture_dir"], "--leaf", 1, cwd=tmp_path).returncode == 0
    report = json.loads(cli(PLAN, "report", t["capture_dir"], cwd=tmp_path).stdout)
    # `ok` WITH the capture: `unchanged` is `apply`'s verdict, by hashing this body — never this unit's word.
    assert report["outcome"] == "ok" and report["captured"] == [{"item": L1, "dir": t["capture_dir"], "title": "Getting the Frame Right"}]


def test_gone_is_a_refresh_tickets_answer_alone(tmp_path, wiki_root):
    t = refresh_ticket()
    cap = tmp_path / "w" / t["capture_dir"]
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps(t), encoding="utf-8")
    (cap / "meta.json").write_text(json.dumps({"url": L1, "title": "Not found", "http_status": 404}), encoding="utf-8")
    assert cli(PLAN, "plan", t["capture_dir"], cwd=tmp_path / "w").returncode == 0
    done = cli(PLAN, "report", t["capture_dir"], cwd=tmp_path / "w")
    assert done.returncode == 0 and json.loads(done.stdout)["outcome"] == "gone" and json.loads(done.stdout)["captured"] == []
    root, rel = wiki_root  # a first pull cannot say it
    assert cli(PLAN, "plan", rel, cwd=root).returncode == 0
    assert cli(PLAN, "report", rel, "--gone", cwd=root).returncode == 2 and not (root / rel / "report.json").exists()


# --- Rule 2: venue text forges nothing -------------------------------------------


def test_no_fact_value_forges_structure(wiki_root):
    root, rel = wiki_root
    meta = root_meta()
    meta["title"] = "Course One\n---\n# Forged Course"
    (root / rel / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    directory = fill(root, plan["leaves"][0], body="# Getting the Frame Right\n\nTopic 1 of 2\n\nBody.\n")
    (directory / "assets.json").write_text(json.dumps([{"type": "hls", "status": "downloaded",
                                                        "local_path": "../assets/a\n## Forged Media.mp4"}]), encoding="utf-8")
    hostile = "A\n## Injected\n\n```\nfence"
    done = subprocess.run([sys.executable, str(PLAN), "record", rel, "--leaf", "1", "--author", hostile], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    body = (directory / "page.md").read_text(encoding="utf-8")
    assert "- **Author**: A ## Injected ``` fence\n" in body and "- **Course**: Course One --- # Forged Course\n" in body
    assert not re.search(r"^(#{1,6} (Injected|Forged)|---|```)", body, flags=re.M)
    assert [line for line in body.splitlines() if line.startswith("#")] == ["# Getting the Frame Right"]
    front = json.loads((directory / "capture.json").read_text(encoding="utf-8"))["frontmatter"]
    assert all("\n" not in item for value in front.values() for item in (value if isinstance(value, list) else [value]))
    # The same name from `author.txt` — how a WORKER passes it, no shell between — is folded the same.
    (directory / "author.txt").write_text(hostile, encoding="utf-8")
    assert cli(PLAN, "record", rel, "--leaf", 1, cwd=root).returncode == 0
    assert "- **Author**: A ## Injected ``` fence\n" in (directory / "page.md").read_text(encoding="utf-8")


def test_a_title_cannot_forge_a_rule_or_a_heading_and_captions_stay_words():
    leaf = {"url": L1, "dir": "_raw/course/x--00000000", "title": None, "source_title": None}
    title, venue = mod.leaf_titles(leaf, "Body with no heading.\n", {"title": "Real\n---\n# Forged: title"})
    assert (title, venue) == ("Real --- # Forged - title", "Real --- # Forged: title")
    body = mod.with_facts("Body with no heading.\n", venue, mod.facts_block({"type": "lesson"}, L1))
    assert body.startswith("# Real --- # Forged: title\n\n- **Type**") and body.count("\n#") == 0 and "\n---" not in body
    assert mod.valid_date("2026-02-30") is None and mod.valid_date("2026-9-1") is None and mod.valid_date("2026-09-01") == "2026-09-01"
    assert mod.build_plan(ticket(min_date="2026-09-01\n# x"), root_meta(), capture_rel="_raw/course/r--00000000")["min_date"] is None
    out = mod.with_transcript("# T\n\nBody.\n", "#### [00:00]\nhello\n# Forged\n```\n---\n=====\nafter\n", "captions/en.vtt`\n# x")
    lines = out.split("## Transcript", 1)[1].splitlines()
    assert "#### [00:00]" in lines and "\\# Forged" in lines and "\\```" in lines and "\\---" in lines and "\\=====" in lines
    assert not any(re.match(r"(# |```|---$|=+$)", line) for line in lines) and "(`captions/en.vttx`)" in out


# --- scope, exclusions -------------------------------------------------------------


def test_www_is_not_a_second_host_and_a_path_prefix_is_whole_segments():
    www = "https://www.community.example.invalid"
    meta = {"title": "C", "discovered_lesson_links": [
        {"href": f"{www}/c/course-one/sections/1/lessons/1", "text": "One"},
        {"href": f"{www}/c/course-one-advanced/sections/1/lessons/2", "text": "Sibling space"},  # `/c/x` is not `/c/xy`
        {"href": "https://wwwcommunity.example.invalid/c/course-one/sections/1/lessons/3", "text": "Another host"}]}
    t = ticket()  # the APEX is the target
    plan = mod.build_plan(t, meta, capture_rel=t["capture_dir"])
    assert [leaf["url"] for leaf in plan["leaves"]] == [f"{www}/c/course-one/sections/1/lessons/1"]
    assert sorted(why(plan).values()) == ["scope", "scope"]
    back = ticket(target=f"{www}/c/course-one", item=f"{www}/c/course-one")  # and the other way round
    assert [leaf["url"] for leaf in mod.build_plan(back, root_meta(), capture_rel=t["capture_dir"])["leaves"]] == [L1, L2]
    assert mod.in_scope(f"{BASE}/c/course-one", TARGET, "section") and not mod.in_scope(f"{BASE}/c/course-on", TARGET, "section")


def test_an_exclusion_is_whole_segments_and_this_units_own_reading():
    assert not mod.excluded(f"{BASE}/c/ab/sections/1/lessons/1", [f"{BASE}/c/a"])
    assert mod.excluded(f"{BASE}/c/a/sections/1/lessons/1", [f"{BASE}/c/a"]) and mod.excluded(f"{BASE}/c/a", [f"{BASE}/c/a/"])
    assert mod.excluded(f"{BASE}/c/a/x", ["https://www.community.example.invalid/c/a"])  # `www.` told apart nowhere
    assert mod.excluded(f"{BASE}/c/a/x", ["community.example.invalid/c/a"]) and mod.excluded(f"{BASE}/c/a/x", ["/c/a"])
    assert not mod.excluded(f"{BASE}/c/a/x", ["/c/ab", "https://elsewhere.example.invalid/c/a", "", None, "http://[::1"])
    assert "this unit's reading" in mod.excluded.__doc__.lower()


# --- the report: what it says, and what it never leaves behind ------------------------


def test_a_report_that_refuses_leaves_no_earlier_report_behind(wiki_root):
    root, rel = wiki_root
    assert cli(PLAN, "plan", rel, cwd=root).returncode == 0
    stale = root / rel / "report.json"
    for bad in (["--missing", "h", "https://h/x", "paywalled"], ["--missing", "h", HOSTILE_HREF, "denied"],
                ["--missing-leaf", "9", "error"], ["--missing-host", "bad host;rm", "denied"], ["--gone"]):
        stale.write_text('{"outcome": "ok", "captured": [{"dir": "an-earlier-run"}]}', encoding="utf-8")
        done = cli(PLAN, "report", rel, *bad, cwd=root)
        assert done.returncode == 2 and not stale.exists(), bad
        assert "$(touch" not in done.stderr
    # A directory that is not a ticket's is refused — nothing is written at the wiki root (Rule 3).
    assert cli(PLAN, "report", ".", cwd=root).returncode == 2 and not (root / "report.json").exists()
    assert cli(PLAN, "report", "_raw/course/typo", cwd=root).returncode == 2 and not (root / "_raw/course/typo").exists()


def test_missing_is_named_by_leaf_number_or_by_host_never_by_a_typed_url(wiki_root):
    root, rel = wiki_root
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    fill(root, plan["leaves"][0], body="# Getting the Frame Right\n\nBody.\n")
    assert cli(PLAN, "record", rel, "--leaf", 1, cwd=root).returncode == 0
    report = json.loads(cli(PLAN, "report", rel, "--missing-leaf", 2, "error", "--missing-host", "Fast.Wistia.com", "denied", cwd=root).stdout)
    assert report["outcome"] == "partial" and report["missing"] == [
        {"host": "community.example.invalid", "url": L2, "why": "error"},
        {"host": "fast.wistia.com", "url": "https://fast.wistia.com/", "why": "denied"}]
    assert "not reached" not in report["reason"]  # lesson 2 is accounted for: it is missing, not unreached


def test_the_report_says_a_min_date_was_not_applied(wiki_root):
    root, rel = wiki_root
    (root / rel / "ticket.json").write_text(json.dumps(ticket(min_date="2026-01-01")), encoding="utf-8")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root))
    assert plan["min_date"] == "2026-01-01" and len(plan["leaves"]) == 2  # no lesson is dropped for it
    for leaf in plan["leaves"]:
        fill(root, leaf, body=f"# {leaf['title']}\n\nBody.\n")
        assert cli(PLAN, "record", rel, "--leaf", leaf["order"], cwd=root).returncode == 0
    report = json.loads(cli(PLAN, "report", rel, cwd=root).stdout)
    assert report["outcome"] == "ok" and "min_date 2026-01-01 NOT applied" in report["reason"]


def test_the_docs_name_media_by_file_name_and_captions_by_their_real_name():
    skill = (ROOT / "skills" / UNIT / "SKILL.md").read_text(encoding="utf-8")
    assert "wiki-relative paths of what" not in skill and "wiki-relative path" not in (mod.__doc__.split("record ")[1].split("report ")[0])
    assert "captions/en.vtt --out" not in skill  # the capture names the file by `srclang`
    assert mod.__doc__ in subprocess.run([sys.executable, str(PLAN), "-h"], capture_output=True, text=True).stdout


def test_the_outage_probe_takes_its_url_off_the_ticket_too(wiki_root):
    root, rel = wiki_root
    spec_p = importlib.util.spec_from_file_location("circle_outage_probe", SCRIPTS / "outage_probe.py")
    probe = importlib.util.module_from_spec(spec_p)
    spec_p.loader.exec_module(probe)  # playwright is imported inside main(), never here
    assert probe.ticket_target(root, rel) == TARGET  # wiki-relative, resolved against the ROOT
    assert probe.ticket_target(root, None) is None and probe.ticket_target(root, "_raw/course/none") is None
    (root / rel / "ticket.json").write_text(json.dumps(ticket(target="file:///etc/passwd")), encoding="utf-8")
    assert probe.ticket_target(root, rel) is None
