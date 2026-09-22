"""channel-circle, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-circle/tests/test_circle.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys

from pathlib import Path

from harness import declared_job, rooted, ticket_in, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-circle", "test_circle"))


def to_markdown(directory: Path) -> str:
    """SKILL.md's process step 1, over one capture's bytes."""
    done = subprocess.run(["uv", "run", "--script", str(TO_MARKDOWN), str(directory / "page.html"),
                           "--out", str(directory / "page.md")], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return (directory / "page.md").read_text(encoding="utf-8")


def paged(ops, env, wiki, capture_dir: Path, dest: str, body: str, verb: str = "create") -> subprocess.CompletedProcess:
    """SKILL.md's process step 3, as the shell line a worker TYPES: the title
    and the url off `capture.json`, each single-quoted as the documented line
    has them, the body on stdin — so every content case is a quoting case."""
    record = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    where = [f"'title={record['title']}'", f"'dest={dest}'"] if verb == "create" else [f"'{dest}/{record['title']}.md'"]
    line = " ".join([shlex.join([*ops, "--json", "page", verb]), *where, f"'resource={record['item']}'", "type=lesson", "extracted=true", "--stdin"])
    return subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), input=body, capture_output=True, text=True, check=False)


def written(ops, env, wiki, capture_dir: Path, dest: str, body: str) -> Path:
    """`create`, and on the host's `already exists` refusal, `edit` — the page."""
    done = paged(ops, env, wiki, capture_dir, dest, body)
    if done.returncode == 2 and "already exists" in done.stdout:
        done = paged(ops, env, wiki, capture_dir, dest, body, verb="edit")
    assert done.returncode == 0, done.stdout + done.stderr
    return wiki / json.loads(done.stdout)["path"]


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
        directory = bytes_in(wiki / leaf["dir"], fixture)
        if fixture == "lesson-1":  # the caption track the capture resolved in-browser
            (directory / "captions").mkdir(exist_ok=True)
            (directory / "captions" / "en.vtt").write_text(VTT, encoding="utf-8")
        number = str(leaf["order"])
        for _ in range(2):  # a respawned worker records again: nothing may stack
            done = subprocess.run([sys.executable, str(PLAN), "record", str(rel), "--leaf", number],
                                  cwd=wiki, capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
        record = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
        assert (record["slug"], record["item"], record["body"], record["content_type"]) == (
            job.slug, leaf["url"], "page.html", "text/html")
        assert "frontmatter" not in record and not set(record) & set(HOST_KEYS)
        assert not (directory / "page.md").exists(), "harvest renders no page"
    assert json.loads((wiki / plan["leaves"][1]["dir"] / "facts.json").read_text(encoding="utf-8")) == {
        "course": "Course One | Example Community", "space": "course-one", "section": "Section One",
        "duration": "12:30", "source_title": "Reading the Room"}

    reported = subprocess.run([sys.executable, str(PLAN), "report", str(rel)], cwd=wiki, capture_output=True, text=True)
    assert reported.returncode == 0, reported.stderr
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "ok" and report["ticket"] == "0123456789ab" and report["missing"] == []
    assert [c["dir"] for c in report["captured"]] == [leaf["dir"] for leaf in plan["leaves"]]
    assert str(rel) not in [c["dir"] for c in report["captured"]]  # the space root is the listing, not a page
    assert report["written"] == [], "harvest writes no page"

    # `apply` mints one process ticket per captured[].dir; each runs the process step over that leaf.
    texts = []
    for captured in report["captured"]:
        directory = wiki / captured["dir"]
        body = to_markdown(directory)  # step 1; step 2 is the agent's, so only its inputs are asserted
        facts = json.loads((directory / "facts.json").read_text(encoding="utf-8"))
        assert facts["course"] == "Course One | Example Community" and facts["section"] == "Section One"
        page = written(ops, env, wiki, directory, job.dest, body)
        assert page.is_relative_to(wiki / job.dest)
        texts.append(page.read_text(encoding="utf-8"))
    first, second = texts
    assert "title: Getting the Frame Right" in first and f"resource: {L1}" in first and "status: draft" in first
    assert "extracted: 'true'" in first
    assert "marmalade-sandwich rule" in first and "> A frame is a promise about what matters." in first
    assert "Topic 1 of 2" in first and "heliotrope question" in second
    assert "https://assets-v2.circle.so/abc123def" in second  # the extensionless Resources link survives
    for text in texts:
        assert len(re.findall(r"^---$", text, flags=re.M)) == 2, "one frontmatter block, the host's own"
        assert "Powered by a community platform" not in text and "logo123" not in text  # chrome stripped

    # A second process ticket for one lesson EDITS the page the first one wrote.
    directory = wiki / report["captured"][0]["dir"]
    assert written(ops, env, wiki, directory, job.dest, to_markdown(directory)).name == "Getting the Frame Right.md"
    assert sorted(page.name for page in (wiki / job.dest).glob("*.md")) == [
        "Getting the Frame Right.md", "Reading the Room.md"]


def test_two_lessons_with_one_title_land_as_two_pages(ops, env, wiki):
    """The page is filed under its title and overwrites what is there: before
    `report` settled titles, the second "Introduction" of a course WAS the
    first one's page, and both process tickets said ok."""
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
        bytes_in(wiki / leaf["dir"], fixture)
        plan_cli("record", rel, leaf["url"])
    reports = []
    for _ in range(2):  # the report is written again by a respawned worker: same names
        plan_cli("report", rel)
        reports.append(json.loads((cap / "report.json").read_text(encoding="utf-8")))
    report = reports[-1]

    pages = [written(ops, env, wiki, wiki / c["dir"], job.dest, to_markdown(wiki / c["dir"])) for c in report["captured"]]
    assert len({page.resolve() for page in pages}) == 2 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    assert [[c["title"] for c in r["captured"]] for r in reports] == [["Introduction", "Introduction (Module Two)"]] * 2
    assert [page.name for page in pages] == ["Introduction.md", "Introduction (Module Two).md"]
    first, second = (page.read_text(encoding="utf-8") for page in pages)
    assert f"resource: {one}" in first and "marmalade-sandwich rule" in first  # still the FIRST lesson's page
    assert f"resource: {two}" in second and "heliotrope question" in second


def test_refused_titles_and_a_long_cjk_title_all_land_through_the_real_page_verb(ops, env, wiki):
    """THE BLOCKER: `Lesson 3: Pricing` was written raw, harvest said ok, and
    the write was refused — `a title cannot carry ':'`."""
    target = f"{BASE}/c/course-titles"
    urls = [f"{target}/sections/1/lessons/{n}" for n in (1, 2, 3)]
    venue = ['Lesson 3: Pricing? A/B "tests" <now>', ".hidden: a leading dot|pipe\\slash*", "課" * 100]
    job = declared_job(ops, env, wiki, UNIT, target, slug="port-channel-circle-titles")
    cap = ticket_in(wiki, job, f"c-course-titles--{hashlib.sha1(target.encode()).hexdigest()[:8]}", unit=UNIT, item=target)
    (cap / "meta.json").write_text(json.dumps({"url": target, "title": "Course Titles", "discovered_lesson_links": [
        {"href": urls[0], "text": f"{venue[0]}\n\n01:00"}, {"href": urls[1], "text": f"{venue[1]}\n\n02:00"},
        {"href": urls[2], "text": ("課" * 100)[:80]},  # the capture's 80-char cap: it names no title; the browser's does
    ]}), encoding="utf-8")
    rel = str(cap.relative_to(wiki))
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=wiki))
    for leaf, title in zip(plan["leaves"], venue):
        fill(wiki, leaf, title=title)
        assert cli(PLAN, "record", rel, "--leaf", leaf["order"], cwd=wiki).returncode == 0
    report = json.loads(cli(PLAN, "report", rel, cwd=wiki).stdout)
    assert report["outcome"] == "ok" and len(report["captured"]) == 3

    for captured, title in zip(report["captured"], venue):
        record = json.loads((wiki / captured["dir"] / "capture.json").read_text(encoding="utf-8"))
        facts = json.loads((wiki / captured["dir"] / "facts.json").read_text(encoding="utf-8"))
        assert record["title"] == captured["title"] == mod.safe_title(title)
        assert facts["source_title"] == title  # the venue's own title is kept…
        directory = wiki / captured["dir"]  # …and the page LANDS
        page = written(ops, env, wiki, directory, job.dest, to_markdown(directory))
        assert "type: lesson" in page.read_text(encoding="utf-8"), "a lesson is not a `note`"
        assert page.is_file() and page.is_relative_to(wiki / job.dest) and page.name == f"{record['title']}.md"
    assert [c["title"] for c in report["captured"]][:2] == [
        "Lesson 3 - Pricing A-B ’tests’ (now)", "hidden - a leading dot-pipe-slash"]


def test_a_title_with_an_apostrophe_survives_the_documented_shell_line(ops, env, wiki):
    """The process step is a shell line a worker TYPES, single-quoting the title
    off `capture.json`. `safe_title` maps BOTH quote forms to U+2019, so no
    title it can produce breaks out of those quotes and loses its page."""
    dest = "sources/courses/port-circle-apostrophe"
    for venue in ("Don't Panic", 'He said "no" twice'):
        title = mod.safe_title(venue)
        assert "'" not in title, title
        line = (
            "printf '%s' 'body' | "
            + shlex.join([*ops, "--json", "page", "create"])
            + f" 'title={title}' 'dest={dest}' 'resource=https://example.invalid/x'"
            + f" 'extracted=true' 'type=lesson' --stdin"
        )
        done = subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), capture_output=True, text=True, check=False)
        assert done.returncode == 0, line + "\n" + done.stdout + done.stderr
        assert (wiki / json.loads(done.stdout)["path"]).is_file()
