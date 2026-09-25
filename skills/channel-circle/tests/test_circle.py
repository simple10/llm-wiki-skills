"""channel-circle on the rebuilt worker contract: one ticket walks a section.

Two steps. Harvest (`section_plan.py`) is the deterministic half — scope,
exclusions, `known[]`, leaf directories, the flat capture records and the one
posted update — so it is tested as pure functions everywhere, and its
CLI-touching cases run through a stand-in front door (`_stub_ops`, the same
stub-front-door pattern this rework's other units use) that answers
`tickets open`/`tickets update`. Process has no script of its
own: the cases below run SKILL.md's own three lines — `to_markdown.py`, then
`page create`, then `page edit` — against the real CLI where one is at hand.
Playwright cannot run here: the fixtures under `fixtures/` are what
`capture_lesson.py` leaves (`page.html`, `meta.json`) for a space root and
two lessons.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


UNIT_DIR = Path(__file__).resolve().parents[1]  # the unit, wherever its tree sits
SCRIPTS = UNIT_DIR / "scripts"
PLAN = SCRIPTS / "section_plan.py"
CAPTURE = SCRIPTS / "capture_lesson.py"
TO_MARKDOWN = SCRIPTS / "to_markdown.py"
FIX = Path(__file__).resolve().parent / "fixtures"

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

# Keys a host verb owns: a flat `capture.json` never carries one.
HOST_KEYS = ("status", "resource", "harvested", "extracted", "document_id", "document_revision")
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nWelcome to the quokka lesson.\n"


def root_meta() -> dict:
    return json.loads((FIX / "root" / "meta.json").read_text(encoding="utf-8"))


def ticket(**over) -> dict:
    """The ticket `open_ticket` would answer (A-1)."""
    base = {
        "ticket": "0123456789ab", "slug": "course", "item": TARGET, "target": TARGET,
        "capture_dir": "_raw/course/c-course-one--aaaaaaaa", "dest": None, "hosts": ["community.example.invalid"],
        "harvest": {"scope": "section", "access": "licensed", "exclude_urls": [], "assets": "reference"},
        "options": {}, "credential": None, "min_date": None, "known": [], "refresh": False, "resource": None,
    }
    base.update(over)
    return base


def plan_of(**over) -> dict:
    t = ticket(**over)
    return mod.build_plan(t, root_meta(), capture_rel=t["capture_dir"])


def why(plan: dict) -> dict:
    return {d["url"]: d["why"] for d in plan["dropped"]}


# ------------------------------------------------------------ the ticket stub


def _stub_ops(tmp_path: Path, ticket_dict: dict | None) -> str:
    """A stand-in front door: `pipeline tickets open` answers `ticket_dict`
    (refused, naming nothing, where it is None); `pipeline tickets update` is
    recorded to `update-calls.jsonl` and answers a bare 0.

    Written under a dot-directory, never directly in `tmp_path` — several
    callers use `tmp_path` itself as the fake wiki root."""
    home = tmp_path / ".ops-stub"
    home.mkdir(exist_ok=True)
    stub = home / "ops_stub.py"
    updates = home / "update-calls.jsonl"
    stub.write_text(
        "import json, pathlib, sys\n"
        "argv = [a for a in sys.argv[1:] if a != '--json']\n"
        f"TICKET = json.loads({json.dumps(json.dumps(ticket_dict))})\n"
        "if argv[:3] == ['pipeline', 'tickets', 'open']:\n"
        "    if TICKET is None:\n"
        "        sys.exit('ops_stub: no ticket')\n"
        "    print(json.dumps({'ticket': TICKET}))\n"
        "    sys.exit(0)\n"
        "if argv[:3] == ['pipeline', 'tickets', 'update']:\n"
        f"    pathlib.Path({str(updates)!r}).open('a').write(json.dumps(argv) + '\\n')\n"
        "    sys.exit(0)\n"
        "sys.exit('ops_stub: unhandled ' + repr(argv))\n"
    )
    return shlex.join([sys.executable, str(stub)])


def _updates(tmp_path: Path) -> list:
    path = tmp_path / ".ops-stub" / "update-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _kv(argv: list) -> dict:
    return dict(a.split("=", 1) for a in argv if "=" in a and not a.startswith("--"))


def run_plan(*args, tmp_path=None, ticket_dict=None) -> subprocess.CompletedProcess:
    """`section_plan.py` over absolute paths, no cwd — for the pure-over-files
    cases that do not care where they run from."""
    argv = list(args)
    env = dict(os.environ)
    if tmp_path is not None:
        env["LLM_WIKI_OPS"] = _stub_ops(tmp_path, ticket_dict)
        if argv and argv[0] in ("plan", "report") and ticket_dict is not None and "--ticket" not in argv:
            argv += ["--ticket", ticket_dict["ticket"]]
    return subprocess.run([sys.executable, str(PLAN), *map(str, argv)], capture_output=True, text=True, env=env)


def bytes_in(directory: Path, fixture: str = "lesson-1", title: str | None = None) -> Path:
    """What `capture_lesson.py --leaf` leaves: the venue's own bytes, nothing
    rendered. `title` stands in for the browser title a real capture records."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / fixture / name, directory / name)
    if title is not None:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        (directory / "meta.json").write_text(json.dumps({**meta, "title": title}), encoding="utf-8")
    return directory


@pytest.fixture
def slice_dir(tmp_path) -> tuple[Path, dict]:
    """A job's `_raw/<slug>/` with the root capture on disk, and the ticket
    `tickets open` would answer for it."""
    cap = tmp_path / "_raw" / "course" / "c-course-one--aaaaaaaa"
    cap.mkdir(parents=True)
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / "root" / name, cap / name)
    return cap, ticket()


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


def landed_leaf(parent: Path, name: str, url: str, title) -> dict:
    """What harvest leaves in a leaf's dir: the venue's bytes, and the flat
    record naming them."""
    leaf = parent / name
    leaf.mkdir(parents=True)
    (leaf / "page.html").write_text(f"<html><body><h1>{title}</h1><p>Body of {url}.</p></body></html>", encoding="utf-8")
    (leaf / "capture.json").write_text(json.dumps(
        {"v": 1, "slug": "course", "item": url, "title": title, "body": "page.html",
         "content_type": "text/html", "fetched_at": "2026-09-19T00:00:00Z"}), encoding="utf-8")
    return {"url": url, "dir": f"_raw/course/{name}", "title": title, "section": None, "root": False}


def titles_on_disk(parent: Path, leaves) -> list:
    return [json.loads((parent / leaf["dir"].split("/")[-1] / "capture.json").read_text(encoding="utf-8")).get("title")
            for leaf in leaves if (parent / leaf["dir"].split("/")[-1] / "capture.json").is_file()]


def test_settling_titles_is_stable_however_often_and_in_whatever_state_it_runs(tmp_path):
    cap = tmp_path / "_raw" / "course" / "c-course-one--aaaaaaaa"
    cap.mkdir(parents=True)
    a = landed_leaf(cap.parent, "a--00000001", f"{TARGET}/sections/1/lessons/1", "Welcome")
    b = landed_leaf(cap.parent, "b--00000002", f"{TARGET}/sections/2/lessons/2", "Welcome")
    c = landed_leaf(cap.parent, "c--00000003", f"{TARGET}/sections/2/lessons/3", "welcome ")
    a["section"], b["section"], c["section"] = "Module One", "Module Two", "Module Two"
    # Planned, never landed: it still holds the name the sidebar gave it.
    ghost = {"url": f"{TARGET}/sections/0/lessons/0", "dir": "_raw/course/ghost--00000000", "title": "Welcome",
             "section": "Module Zero", "root": False}
    plan = {"leaves": [ghost, a, b, c]}
    # The section tells the first two apart; lesson 3 shares lesson 2's, so its url's hash does.
    third = f"welcome ({hashlib.sha1(c['url'].encode()).hexdigest()[:8]})"
    for _ in range(2):  # idempotent: a second report renames nothing a second time
        mod.settle_titles(cap, plan)
        assert titles_on_disk(cap.parent, [a, b, c]) == ["Welcome (Module One)", "Welcome (Module Two)", third]
    # `record` run again puts the plain title back; the next report settles it to the SAME name.
    landed_leaf(cap.parent, "b2--00000002", b["url"], "Welcome")
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


def test_report_is_partial_until_every_planned_leaf_is_on_disk(slice_dir, tmp_path):
    cap, t = slice_dir
    assert run_plan("plan", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    first = cap.parent / plan["leaves"][0]["dir"].split("/")[-1]
    bytes_in(first, "lesson-1")
    assert run_plan("record", cap, L1).returncode == 0

    done = run_plan("report", cap, tmp_path=tmp_path, ticket_dict=t)
    assert done.returncode == 0
    call = _updates(tmp_path)[-1]
    kv = _kv(call)
    assert kv["status"] == "partial" and "1 of 2" in kv["reason"]
    assert call[3] == "0123456789ab" and kv["captured"] == plan["leaves"][0]["dir"]


def test_auth_expiry_names_every_unreached_lesson_as_auth(slice_dir, tmp_path):
    cap, t = slice_dir
    assert run_plan("plan", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    done = run_plan("report", cap, "--auth-expired", tmp_path=tmp_path, ticket_dict=t)
    kv = _kv(_updates(tmp_path)[-1])
    assert done.returncode == 1 and kv["status"] == "failed"
    assert kv["reason"].startswith("auth_expired:community.example.invalid")
    call = _updates(tmp_path)[-1]
    missing = [a.split("=", 1)[1] for a in call if a.startswith("missing=")]
    assert missing == [f"community.example.invalid,{url},auth" for url in (L1, L2)]


def test_a_root_that_never_rendered_still_posts_an_update(tmp_path):
    cap = tmp_path / "_raw" / "course" / "c-course-one--aaaaaaaa"
    cap.mkdir(parents=True)
    t = ticket()
    assert run_plan("plan", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 1  # no meta.json: nothing to plan from
    done = run_plan("report", cap, "--auth-expired", tmp_path=tmp_path, ticket_dict=t)
    kv = _kv(_updates(tmp_path)[-1])
    assert done.returncode == 1 and kv["status"] == "failed" and "captured" not in kv
    call = _updates(tmp_path)[-1]
    missing = [a.split("=", 1)[1] for a in call if a.startswith("missing=")]
    assert missing == [f"community.example.invalid,{TARGET},auth"]


def test_everything_already_held_is_ok_not_failed(slice_dir, tmp_path):
    cap, _ = slice_dir
    t = ticket(known=[{"resource": L1, "harvested_at": None}, {"resource": L2, "harvested_at": None}])
    assert run_plan("plan", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    done = run_plan("report", cap, tmp_path=tmp_path, ticket_dict=t)
    # P-4: nothing new is `ok`, never a worker's `skipped`.
    assert done.returncode == 0 and _kv(_updates(tmp_path)[-1])["status"] == "ok"


def test_record_refuses_a_url_the_plan_does_not_hold(slice_dir, tmp_path):
    cap, t = slice_dir
    assert run_plan("plan", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    done = run_plan("record", cap, OTHER_SPACE)
    assert done.returncode == 1 and "not a leaf" in done.stderr


def test_a_missing_asset_host_leaves_a_full_capture_ok(slice_dir, tmp_path):
    """P-5: a denied media host is a LASTING shortfall — the section still
    landed in full, so it is `ok`, never `partial`."""
    cap, t = slice_dir
    assert run_plan("plan", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    for leaf, fixture in zip(plan["leaves"], ("lesson-1", "lesson-2")):
        bytes_in(cap.parent / leaf["dir"].split("/")[-1], fixture)
        assert run_plan("record", cap, leaf["url"]).returncode == 0
    assert run_plan("report", cap, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    assert _kv(_updates(tmp_path)[-1])["status"] == "ok"
    denied = run_plan("report", cap, "--missing", "fast.wistia.com", "https://fast.wistia.com/embed/medias/x.m3u8", "denied",
                      tmp_path=tmp_path, ticket_dict=t)
    kv = _kv(_updates(tmp_path)[-1])
    assert denied.returncode == 0 and kv["status"] == "ok"
    call = _updates(tmp_path)[-1]
    captured = [a.split("=", 1)[1] for a in call if a.startswith("captured=")]
    assert len(captured) == 2
    missing = [a.split("=", 1)[1] for a in call if a.startswith("missing=")]
    assert missing == ["fast.wistia.com,https://fast.wistia.com/embed/medias/x.m3u8,denied"]
    assert run_plan("report", cap, "--missing", "h", "https://h/x", "paywalled", tmp_path=tmp_path, ticket_dict=t).returncode == 2


# --- capture_lesson: the url off the ticket when none is given ----------------


def test_capture_lesson_takes_its_url_off_the_ticket_when_none_is_given(tmp_path):
    spec_c = importlib.util.spec_from_file_location("circle_capture_lesson", SCRIPTS / "capture_lesson.py")
    capture = importlib.util.module_from_spec(spec_c)
    spec_c.loader.exec_module(capture)  # playwright is imported inside main(), never here
    assert capture.ticket_target(tmp_path, None) is None
    assert capture.ticket_target(tmp_path, "no-such-ticket") is None


# =============================================================================
# Review fixes (2026-09-19). Each case below fails without the fix it names.
# =============================================================================

HOSTILE_HREF = f"{TARGET}/sections/111/lessons/2003;$(touch${{IFS}}PWNED)"  # the review's exact href


def load_capture():
    spec_c = importlib.util.spec_from_file_location("circle_capture_lesson_fixes", CAPTURE)
    capture = importlib.util.module_from_spec(spec_c)
    spec_c.loader.exec_module(capture)  # playwright is imported inside main(), after everything tested here
    return capture


def cli(script: Path, *args, cwd: Path, env: dict | None = None, tmp_path=None, ticket_dict=None) -> subprocess.CompletedProcess:
    """A unit script THE DOCUMENTED WAY: cwd is the wiki root — what
    `llm-wiki-ops run` gives it — and every path argument is wiki-relative.
    `tmp_path` stands up the ticket stub front door and `--ticket` is
    appended to `plan`/`report` when `ticket_dict` names one."""
    assert not any(os.path.isabs(str(a)) for a in args), "the documented form takes wiki-relative paths"
    e = {**os.environ, **(env or {})}
    argv = list(args)
    if tmp_path is not None:
        e["LLM_WIKI_OPS"] = _stub_ops(tmp_path, ticket_dict)
        if argv and argv[0] in ("plan", "report") and ticket_dict is not None and "--ticket" not in argv:
            argv += ["--ticket", ticket_dict["ticket"]]
    return subprocess.run([sys.executable, str(script), *map(str, argv)], cwd=cwd, env=e, capture_output=True, text=True)


def last_json(done: subprocess.CompletedProcess) -> dict:
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture
def wiki_root(tmp_path) -> tuple[Path, str]:
    """A bare wiki root holding one root capture: `(root, the ticket's
    wiki-relative capture_dir)`. The ticket itself is `ticket()`."""
    rel = "_raw/course/c-course-one--aaaaaaaa"
    cap = tmp_path / rel
    cap.mkdir(parents=True)
    for name in ("page.html", "meta.json"):
        shutil.copy(FIX / "root" / name, cap / name)
    return tmp_path, rel


def fill(root: Path, leaf: dict, fixture: str = "lesson-1", title: str | None = None) -> Path:
    return bytes_in(root / leaf["dir"], fixture, title)


# --- Rule 1: the title is a legal filename -------------------------------------


def test_safe_title_is_what_the_hosts_filename_rule_accepts():
    illegal = set('/\\:*?"<>|')  # `page/note.py::ILLEGAL`
    assert mod.safe_title("Lesson 3: Pricing") == "Lesson 3 - Pricing"
    assert mod.safe_title('What is "X"? A/B <test> | more*') == "What is \u2019X\u2019 A-B (test) - more"
    assert mod.safe_title(".hidden. ") == "hidden" and mod.safe_title(" . ..dots") == "dots"
    assert mod.safe_title("a\x00b\tc\nd\x7fe f") == "a b c d e f"  # control chars, newlines, tabs: a space
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


def test_titles_differing_only_in_a_refused_character_collide_once_safe_and_are_told_apart(wiki_root, tmp_path):
    """`A/B` and `A-B` are two titles at the venue and ONE filename: the safe
    form is what `capture.json` holds BEFORE titles are settled, and what a
    lesson that never landed reserves."""
    root, rel = wiki_root
    t = ticket()
    three = f"{TARGET}/sections/222/lessons/2003"
    (root / rel / "meta.json").write_text(json.dumps({"title": "C", "discovered_lesson_links": [
        {"href": f"{TARGET}/sections/111", "text": "Module One"}, {"href": L1, "text": "A/B testing\n\n01:00"},
        {"href": L2, "text": "A-B testing\n\n01:00"},
        {"href": f"{TARGET}/sections/222", "text": "Module Two"}, {"href": three, "text": "A:B testing\n\n01:00"},
    ]}), encoding="utf-8")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t))
    # Leaf 1 never lands: it still RESERVES `A-B testing`, its safe form.
    for leaf in plan["leaves"][1:]:
        fill(root, leaf)
        assert cli(PLAN, "record", rel, "--leaf", leaf["order"], cwd=root).returncode == 0
    assert cli(PLAN, "report", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    call = _updates(tmp_path)[-1]
    captured = [a.split("=", 1)[1] for a in call if a.startswith("captured=")]
    titles = [json.loads((root / d / "capture.json").read_text(encoding="utf-8"))["title"] for d in captured]
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


# --- S11: a venue url is data, never shell --------------------------------------
def test_a_hostile_href_never_reaches_a_plan(wiki_root, tmp_path):
    root, rel = wiki_root
    t = ticket()
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
    done = cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t)
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
    skill = (UNIT_DIR / "SKILL.md").read_text(encoding="utf-8")
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
        f"('CLAUDE_PROJECT_DIR',) if k in os.environ)}}, open({str(seen)!r}, 'w'))\n"
        f"sys.stdout.write({json.dumps(answer)!r})\nsys.exit({rc})\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "LLM_WIKI_OPS": str(stub), "CLAUDE_PROJECT_DIR": str(tmp_path / "another-wiki")}
    return env, seen


def stub_capture_front_door(tmp_path: Path, ticket_dict: dict, profile_answer: dict, rc: int = 0) -> tuple[dict, Path]:
    """`capture_lesson.py`'s own front door reaches two verbs: `tickets open`
    (its `--ticket`'s target) and `credential profile-dir` (the auth
    profile). This stub answers both for real and records the LAST argv."""
    home = tmp_path / ".ops-stub"
    home.mkdir(exist_ok=True)
    stub, seen = home / "ops_stub.py", home / "seen.json"
    stub.write_text(
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        f"json.dump({{'argv': argv}}, open({str(seen)!r}, 'w'))\n"
        "argv = [a for a in argv if a != '--json']\n"
        f"TICKET = json.loads({json.dumps(json.dumps(ticket_dict))})\n"
        f"PROFILE = json.loads({json.dumps(json.dumps(profile_answer))})\n"
        "if argv[:3] == ['pipeline', 'tickets', 'open']:\n"
        "    print(json.dumps({'ticket': TICKET}))\n"
        "    sys.exit(0)\n"
        "if argv[:2] == ['credential', 'profile-dir']:\n"
        f"    print(json.dumps(PROFILE))\n"
        f"    sys.exit({rc})\n"
        "sys.exit('ops_stub: unhandled ' + repr(argv))\n"
    )
    return {**os.environ, "LLM_WIKI_OPS": shlex.join([sys.executable, str(stub)])}, seen


def test_detect_hands_the_plugins_asset_script_the_planned_url_as_an_argument_list(wiki_root, tmp_path_factory):
    root, rel = wiki_root
    t = ticket()
    door_home = tmp_path_factory.mktemp("ticket-door")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=door_home, ticket_dict=t))
    leaf = plan["leaves"][1]
    env, seen = stub_front_door(tmp_path_factory.mktemp("door"), {})
    assert cli(PLAN, "detect", rel, "--leaf", 2, cwd=root, env=env).returncode == 0
    got = json.loads(seen.read_text(encoding="utf-8"))
    assert got["argv"] == ["run", "scripts/assets.py", "detect", f"{leaf['dir']}/page.html", "--base-url", L2,
                           "--network-log", f"{leaf['dir']}/net.json", "--out", f"{leaf['dir']}/assets.json"]
    assert got["inherited"] == [] and Path(got["cwd"]) == root.resolve()  # the harness's project dir is dropped; the call binds by cwd
    assert cli(PLAN, "detect", rel, "--leaf", 9, cwd=root, env=env).returncode == 1  # not a leaf of this plan
    # A plan.json somebody tampered with is still not a way to a shell word.
    plan["leaves"][0]["url"] = HOSTILE_HREF
    (root / rel / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    seen.unlink()
    assert cli(PLAN, "detect", rel, "--leaf", 1, cwd=root, env=env).returncode == 1 and not seen.exists()


def test_capture_lesson_reads_a_leafs_url_and_dir_off_the_plan(wiki_root, tmp_path_factory, monkeypatch):
    root, rel = wiki_root
    t = ticket()
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("door"), ticket_dict=t))
    capture = load_capture()
    url, out, is_target, refused = capture.resolve_job(root, plan=f"{rel}/plan.json", leaf=2)
    assert (url, out, is_target, refused) == (L2, root / plan["leaves"][1]["dir"], False, None)
    # With neither url nor leaf: --ticket's own target, into the ticket's own dir, resolved against the ROOT.
    monkeypatch.setenv("LLM_WIKI_OPS", _stub_ops(tmp_path_factory.mktemp("ticket-door"), t))
    assert capture.resolve_job(root, out=rel, ticket=t["ticket"]) == (TARGET, root / rel, True, None)
    monkeypatch.delenv("LLM_WIKI_OPS", raising=False)
    assert capture.resolve_job(root, url=L1, out="_raw/course/by-hand")[:3] == (L1, root / "_raw/course/by-hand", False)
    for kwargs, code in (({"plan": f"{rel}/plan.json", "leaf": 9}, 4), ({"leaf": 1}, 4), ({"plan": f"{rel}/nope.json", "leaf": 1}, 4),
                         ({}, 4), ({"out": "_raw/course/empty"}, 4), ({"url": "file:///etc/passwd", "out": rel}, 4),
                         ({"plan": f"{rel}/plan.json", "leaf": 1, "out": "_raw/course/elsewhere"}, 4)):
        assert capture.resolve_job(root, **kwargs)[3][0] == code, kwargs


def test_capture_lesson_the_documented_way_reads_the_profile_answer(wiki_root, tmp_path_factory):
    """No browser here — and none is needed to reach the answer that matters:
    `<root> --out <capture_dir>`, cwd the wiki root, relative paths."""
    root, rel = wiki_root
    t = ticket()
    env, seen = stub_capture_front_door(
        tmp_path_factory.mktemp("door"), t, {"domain": "community.example.invalid", "path": "/nowhere/profile", "exists": False},
    )
    done = cli(CAPTURE, ".", "--out", rel, "--ticket", t["ticket"], cwd=root, env=env)
    assert done.returncode == 2 and "no auth profile" in done.stderr, done.stderr  # absent: a login is what fixes it
    assert json.loads(seen.read_text(encoding="utf-8"))["argv"] == ["--json", "credential", "profile-dir", "community.example.invalid"]
    # The store unreachable — what a jail with no grant on it answers: 5, never 2.
    env, _ = stub_capture_front_door(tmp_path_factory.mktemp("door5"), t, {"error": "permission denied"}, rc=1)
    assert cli(CAPTURE, ".", "--out", rel, "--ticket", t["ticket"], cwd=root, env=env).returncode == 5
    # `--leaf`, the documented way.
    t = ticket()
    assert cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("ticket-door"), ticket_dict=t).returncode == 0
    env, seen = stub_front_door(tmp_path_factory.mktemp("door2"), {"domain": "community.example.invalid", "path": "/nowhere", "exists": False})
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("ticket-door2"), ticket_dict=t))
    done = cli(CAPTURE, ".", "--plan", f"{rel}/plan.json", "--leaf", 1, cwd=root, env=env)
    assert done.returncode == 2 and (root / plan["leaves"][0]["dir"]).is_dir()
    assert cli(CAPTURE, ".", "--plan", f"{rel}/plan.json", "--leaf", 7, cwd=root, env=env).returncode == 4


# --- S5: stop before the cap, and make "resume" real ----------------------------


def backdate_plan(cap: Path, seconds: float) -> None:
    """A plan whose deadline is `seconds` further in the past than it really
    is — what a real spawn that long ago would have left. There is no file to
    backdate any more (P-8): the deadline fields themselves are moved."""
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    plan["deadline_epoch"] -= seconds
    (cap / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def test_the_deadline_is_keyed_to_the_spawn_and_a_hand_run_has_none(wiki_root, tmp_path, tmp_path_factory):
    root, rel = wiki_root
    t = ticket()
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t))
    assert abs(plan["deadline_epoch"] - (time.time() + 1500)) < 5 and plan["deadline"].endswith("Z")
    door2 = tmp_path_factory.mktemp("deadline-door2")
    budgeted = last_json_plan(cli(PLAN, "plan", rel, "--budget-s", 60, cwd=root, tmp_path=door2, ticket_dict=t))
    assert abs(budgeted["deadline_epoch"] - (time.time() + 60)) < 5
    hand = tmp_path_factory.mktemp("deadline-hand") / "_raw" / "course" / "r--00000000"
    hand.mkdir(parents=True)
    shutil.copy(FIX / "root" / "meta.json", hand / "meta.json")
    by_hand = subprocess.run(
        [sys.executable, str(PLAN), "plan", "_raw/course/r--00000000", "--target", TARGET, "--slug", "course"],
        cwd=hand.parents[2], capture_output=True, text=True,
    )
    assert last_json_plan(by_hand)["deadline"] is None and not mod.past_deadline(last_json_plan(by_hand))


def test_past_the_deadline_record_says_stop_and_no_new_lesson_is_started(wiki_root, tmp_path_factory):
    root, rel = wiki_root
    t = ticket()
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("ticket-door"), ticket_dict=t))
    fill(root, plan["leaves"][0])
    backdate_plan(root / rel, 1600)  # 100 s past the 1500 s budget, 200 s before the kill
    done = cli(PLAN, "record", rel, "--leaf", 1, cwd=root)
    answer = last_json(done)
    assert done.returncode == 3 and answer["stop"] is True and answer["deadline_passed"] is True and answer["remaining"] == [2]
    assert (root / plan["leaves"][0]["dir"] / "capture.json").is_file(), "exit 3 means recorded AND stop — the leaf landed"
    # …and the next lesson is refused before any browser (or credential lookup) is started.
    env, seen = stub_front_door(tmp_path_factory.mktemp("door"), {"path": "/x", "exists": True})
    refused = cli(CAPTURE, ".", "--plan", f"{rel}/plan.json", "--leaf", 2, cwd=root, env=env)
    assert refused.returncode == 6 and "deadline" in refused.stderr and not seen.exists()
    assert not (root / plan["leaves"][1]["dir"]).exists()

    report_door = tmp_path_factory.mktemp("report-door")
    done = cli(PLAN, "report", rel, cwd=root, tmp_path=report_door, ticket_dict=t)
    kv = _kv(_updates(report_door)[-1])
    assert kv["status"] == "partial"
    call = _updates(report_door)[-1]
    assert [a.split("=", 1)[1] for a in call if a.startswith("captured=")] == [plan["leaves"][0]["dir"]]
    # The reason tells the operator exactly what resumes an `every: once` job — nothing does by itself.
    for said in ("1 of 2", "deadline passed", "pipeline tickets retry 0123456789ab", "pipeline jobs edit course every=1d", "NOT pulled again"):
        assert said in kv["reason"], said

    # Inside the budget (a fresh plan) the same record is plain success.
    fresh_door = tmp_path_factory.mktemp("fresh-door")
    assert cli(PLAN, "plan", rel, cwd=root, tmp_path=fresh_door, ticket_dict=t).returncode == 0
    inside = cli(PLAN, "record", rel, "--leaf", 1, cwd=root)
    assert inside.returncode == 0 and last_json(inside)["stop"] is False and last_json(inside)["remaining"] == [2]


def test_a_killed_slices_lessons_are_landed_in_the_next_plan_and_reported_by_it(wiki_root, tmp_path_factory):
    """The cap kills a slice with NO update posted: nothing is minted,
    `known[]` does not grow, and the retry used to re-plan every lesson in the
    same order and die at the same place. What survives the kill is the disk."""
    root, rel = wiki_root
    t = ticket()
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("door1"), ticket_dict=t))
    assert [leaf["landed"] for leaf in plan["leaves"]] == [False, False]
    fill(root, plan["leaves"][0])
    assert cli(PLAN, "record", rel, "--leaf", 1, cwd=root).returncode == 0
    # The update is cheap and re-postable: sent after EVERY leaf, it is already truthful when the kill comes.
    early_door = tmp_path_factory.mktemp("door2")
    assert cli(PLAN, "report", rel, cwd=root, tmp_path=early_door, ticket_dict=t).returncode == 0
    early = _kv(_updates(early_door)[-1])
    assert early["status"] == "partial"

    # …killed; the operator re-queues; a new slice plans:
    again = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("door3"), ticket_dict=t))
    assert [leaf["landed"] for leaf in again["leaves"]] == [True, False]
    fill(root, again["leaves"][1], "lesson-2")
    assert cli(PLAN, "record", rel, "--leaf", 2, cwd=root).returncode == 0
    final_door = tmp_path_factory.mktemp("door4")
    assert cli(PLAN, "report", rel, cwd=root, tmp_path=final_door, ticket_dict=t).returncode == 0
    final_call = _updates(final_door)[-1]
    final = _kv(final_call)
    assert final["status"] == "ok"
    assert [a.split("=", 1)[1] for a in final_call if a.startswith("captured=")] == [again["leaves"][0]["dir"], again["leaves"][1]["dir"]]
    # A record left in a leaf's dir by some OTHER url is not this lesson landed.
    record_path = root / again["leaves"][1]["dir"] / "capture.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record_path.write_text(json.dumps({**record, "item": OTHER_SPACE}), encoding="utf-8")
    replanned = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path_factory.mktemp("door5"), ticket_dict=t))
    assert [leaf["landed"] for leaf in replanned["leaves"]] == [True, False]


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


def test_a_refresh_forces_the_recapture_and_reports_it_captured(tmp_path, tmp_path_factory):
    t = refresh_ticket()
    cap = tmp_path / t["capture_dir"]
    cap.mkdir(parents=True)
    # What the FIRST pull left here — the directory is the page's own, stable across pulls.
    (cap / "page.html").write_text("<html><body>THE OLD BODY.</body></html>", encoding="utf-8")
    (cap / "capture.json").write_text(json.dumps({"item": L1, "title": "Getting the Frame Right", "body": "page.html"}), encoding="utf-8")
    shutil.copy(FIX / "lesson-1" / "meta.json", cap / "meta.json")  # this run's root capture IS the lesson
    plan = last_json_plan(cli(PLAN, "plan", t["capture_dir"], cwd=tmp_path, tmp_path=tmp_path_factory.mktemp("refresh-door1"), ticket_dict=t))
    assert [leaf["landed"] for leaf in plan["leaves"]] == [False]
    assert not any((cap / name).exists() for name in ("capture.json", "page.html"))
    # Nothing fetched again -> nothing captured -> failed; never the old record read as this run's.
    door2 = tmp_path_factory.mktemp("refresh-door2")
    nothing = cli(PLAN, "report", t["capture_dir"], cwd=tmp_path, tmp_path=door2, ticket_dict=t)
    assert nothing.returncode == 1 and _kv(_updates(door2)[-1])["status"] == "failed"
    shutil.copy(FIX / "lesson-1" / "page.html", cap / "page.html")
    assert cli(PLAN, "record", t["capture_dir"], "--leaf", 1, cwd=tmp_path).returncode == 0
    door3 = tmp_path_factory.mktemp("refresh-door3")
    assert cli(PLAN, "report", t["capture_dir"], cwd=tmp_path, tmp_path=door3, ticket_dict=t).returncode == 0
    call = _updates(door3)[-1]
    kv = _kv(call)
    # `ok` WITH the capture: `unchanged` is a later read's verdict, by hashing these bytes — never this unit's word.
    assert kv["status"] == "ok" and [a.split("=", 1)[1] for a in call if a.startswith("captured=")] == [t["capture_dir"]]


def test_gone_is_a_refresh_tickets_answer_alone(tmp_path, wiki_root, tmp_path_factory):
    t = refresh_ticket()
    w = tmp_path_factory.mktemp("gone-wiki")
    cap = w / t["capture_dir"]
    cap.mkdir(parents=True)
    (cap / "meta.json").write_text(json.dumps({"url": L1, "title": "Not found", "http_status": 404}), encoding="utf-8")
    assert cli(PLAN, "plan", t["capture_dir"], cwd=w, tmp_path=tmp_path_factory.mktemp("gone-door1"), ticket_dict=t).returncode == 0
    door2 = tmp_path_factory.mktemp("gone-door2")
    done = cli(PLAN, "report", t["capture_dir"], cwd=w, tmp_path=door2, ticket_dict=t)
    kv = _kv(_updates(door2)[-1])
    assert done.returncode == 0 and kv["status"] == "gone" and "captured" not in kv
    root, rel = wiki_root  # a first pull cannot say it
    door3 = tmp_path_factory.mktemp("gone-door3")
    normal_t = ticket()
    assert cli(PLAN, "plan", rel, cwd=root, tmp_path=door3, ticket_dict=normal_t).returncode == 0
    assert cli(PLAN, "report", rel, "--gone", cwd=root, tmp_path=door3, ticket_dict=normal_t).returncode == 2


# --- Rule 2: venue text forges nothing -------------------------------------------


def test_harvest_folds_every_venue_value_it_writes_down(wiki_root, tmp_path):
    """`facts.json` is read by the process step and written into the page, so a
    course title carrying a rule and a heading must arrive as one line."""
    root, rel = wiki_root
    t = ticket()
    meta = root_meta()
    meta["title"] = "Course One\n---\n# Forged Course"
    meta["discovered_lesson_links"][1]["text"] = "Lesson 3: A\t## Injected ```fence\n\n04:07"
    (root / rel / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t))
    leaf = plan["leaves"][0]
    fill(root, leaf)
    assert cli(PLAN, "record", rel, "--leaf", 1, cwd=root).returncode == 0
    facts = json.loads((root / leaf["dir"] / "facts.json").read_text(encoding="utf-8"))
    assert facts["course"] == "Course One --- # Forged Course"
    assert facts["source_title"] == "Lesson 3: A ## Injected ```fence" and facts["duration"] == "04:07"
    assert all(value is None or "\n" not in value for value in facts.values())
    record = json.loads((root / leaf["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert record["title"] == "Lesson 3 - A ## Injected ```fence" and not set(record) & set(HOST_KEYS)


def test_record_refuses_a_leaf_whose_bytes_never_landed(wiki_root, tmp_path):
    root, rel = wiki_root
    t = ticket()
    assert cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    done = cli(PLAN, "record", rel, "--leaf", 1, cwd=root)
    assert done.returncode == 1 and "page.html" in done.stderr


def test_the_process_step_posts_the_pages_it_wrote_and_captures_nothing(wiki_root, tmp_path, tmp_path_factory):
    root, rel = wiki_root
    t = ticket()
    (root / rel / "written.json").write_text(json.dumps(["sources/courses/course/A Lesson.md"]), encoding="utf-8")
    done = cli(PLAN, "report", rel, "--stage", "process", "--written-from", "written.json", cwd=root, tmp_path=tmp_path, ticket_dict=t)
    call = _updates(tmp_path)[-1]
    kv = _kv(call)
    assert done.returncode == 0 and kv["status"] == "ok"
    assert kv["written_from"] == "written.json" and "captured" not in kv
    # A capture the ticket's own rules excluded: `ok`, nothing written, and the reason said.
    door2 = tmp_path_factory.mktemp("process-excl-door")
    excl = cli(PLAN, "report", rel, "--stage", "process", "--reason", "excluded by rule", cwd=root, tmp_path=door2, ticket_dict=t)
    kv2 = _kv(_updates(door2)[-1])
    assert excl.returncode == 0 and kv2["status"] == "ok" and kv2["reason"] == "excluded by rule"
    # A path carrying a TITLE is data: it is written down, never outside the wiki, and never something else.
    for bad in ("../../etc/passwd.md", "sources/courses/course/A Lesson.txt", ".hidden/x.md"):
        (root / rel / "bad.json").write_text(json.dumps([bad]), encoding="utf-8")
        bad_door = tmp_path_factory.mktemp(f"process-bad-{hashlib.sha1(bad.encode()).hexdigest()[:6]}")
        assert cli(PLAN, "report", rel, "--stage", "process", "--written-from", "bad.json", cwd=root, tmp_path=bad_door, ticket_dict=t).returncode == 2
    assert mod.page_path("/etc/passwd.md") is None and mod.page_path(" sources/a/b.md ") == "sources/a/b.md"


def test_a_title_cannot_forge_a_rule_or_a_heading():
    leaf = {"url": L1, "dir": "_raw/course/x--00000000", "title": None, "source_title": None}
    title, venue = mod.leaf_titles(leaf, {"title": "Real\n---\n# Forged: title"})
    assert (title, venue) == ("Real --- # Forged - title", "Real --- # Forged: title")
    assert mod.valid_date("2026-02-30") is None and mod.valid_date("2026-9-1") is None and mod.valid_date("2026-09-01") == "2026-09-01"
    assert mod.build_plan(ticket(min_date="2026-09-01\n# x"), root_meta(), capture_rel="_raw/course/r--00000000")["min_date"] is None


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


# --- the report: what it posts, and what it never leaves behind ------------------------


def test_a_report_that_refuses_posts_nothing(wiki_root, tmp_path, tmp_path_factory):
    root, rel = wiki_root
    t = ticket()
    assert cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t).returncode == 0
    for i, bad in enumerate((["--missing", "h", "https://h/x", "paywalled"], ["--missing", "h", HOSTILE_HREF, "denied"],
                             ["--missing-leaf", "9", "error"], ["--missing-host", "bad host;rm", "denied"], ["--gone"])):
        door = tmp_path_factory.mktemp(f"refuse-door-{i}")
        done = cli(PLAN, "report", rel, *bad, cwd=root, tmp_path=door, ticket_dict=t)
        assert done.returncode == 2 and not _updates(door), bad
        assert "$(touch" not in done.stderr
    # A directory that is not a ticket's has no plan.json: it posts `failed`,
    # never a claim of anything captured — and nothing is written at the wiki
    # root either way (Rule 3).
    door2 = tmp_path_factory.mktemp("refuse-door-dot")
    dotcall = cli(PLAN, "report", ".", cwd=root, tmp_path=door2, ticket_dict=t)
    assert dotcall.returncode == 1 and _kv(_updates(door2)[-1])["status"] == "failed"
    door3 = tmp_path_factory.mktemp("refuse-door-typo")
    typocall = cli(PLAN, "report", "_raw/course/typo", cwd=root, tmp_path=door3, ticket_dict=t)
    assert typocall.returncode == 1 and _kv(_updates(door3)[-1])["status"] == "failed"
    assert not (root / "_raw/course/typo").exists()


def test_missing_is_named_by_leaf_number_or_by_host_never_by_a_typed_url(wiki_root, tmp_path, tmp_path_factory):
    root, rel = wiki_root
    t = ticket()
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t))
    fill(root, plan["leaves"][0])
    assert cli(PLAN, "record", rel, "--leaf", 1, cwd=root).returncode == 0
    door2 = tmp_path_factory.mktemp("missing-door2")
    done = cli(PLAN, "report", rel, "--missing-leaf", 2, "error", "--missing-host", "Fast.Wistia.com", "denied",
              cwd=root, tmp_path=door2, ticket_dict=t)
    call = _updates(door2)[-1]
    kv = _kv(call)
    assert done.returncode == 0 and kv["status"] == "partial"
    missing = [a.split("=", 1)[1] for a in call if a.startswith("missing=")]
    assert missing == [f"community.example.invalid,{L2},error", "fast.wistia.com,https://fast.wistia.com/,denied"]
    assert "not reached" not in kv["reason"]  # lesson 2 is accounted for: it is missing, not unreached


def test_the_report_says_a_min_date_was_not_applied(wiki_root, tmp_path, tmp_path_factory):
    root, rel = wiki_root
    t = ticket(min_date="2026-01-01")
    plan = last_json_plan(cli(PLAN, "plan", rel, cwd=root, tmp_path=tmp_path, ticket_dict=t))
    assert plan["min_date"] == "2026-01-01" and len(plan["leaves"]) == 2  # no lesson is dropped for it
    for leaf in plan["leaves"]:
        fill(root, leaf)
        assert cli(PLAN, "record", rel, "--leaf", leaf["order"], cwd=root).returncode == 0
    door2 = tmp_path_factory.mktemp("mindate-door2")
    done = cli(PLAN, "report", rel, cwd=root, tmp_path=door2, ticket_dict=t)
    kv = _kv(_updates(door2)[-1])
    assert done.returncode == 0 and kv["status"] == "ok" and "min_date 2026-01-01 NOT applied" in kv["reason"]


def test_the_planners_help_is_its_own_docstring():
    assert mod.__doc__ in subprocess.run([sys.executable, str(PLAN), "-h"], capture_output=True, text=True).stdout


def test_the_outage_probe_takes_its_ticket_id_too(wiki_root, tmp_path_factory, monkeypatch):
    root, rel = wiki_root
    spec_p = importlib.util.spec_from_file_location("circle_outage_probe", SCRIPTS / "outage_probe.py")
    probe = importlib.util.module_from_spec(spec_p)
    spec_p.loader.exec_module(probe)  # playwright is imported inside main(), never here
    assert probe.ticket_target(root, None) is None  # no --ticket at all
    monkeypatch.setenv("LLM_WIKI_OPS", _stub_ops(tmp_path_factory.mktemp("probe-open"), ticket()))
    assert probe.ticket_target(root, "0123456789ab") == TARGET  # wiki-relative resolution is the CLI's, not this script's
    monkeypatch.setenv("LLM_WIKI_OPS", _stub_ops(tmp_path_factory.mktemp("probe-hostile"), ticket(target="file:///etc/passwd")))
    assert probe.ticket_target(root, "0123456789ab") is None


def test_the_documented_page_line_names_a_type():
    """`docs/contracts/note-format.md` requires `type` on every page and
    `page create` defaults a missing one to `note`. A lesson is not a note, and
    the page the SKILL writes is only as good as the line it prints."""
    skill = (UNIT_DIR / "SKILL.md").read_text(encoding="utf-8")
    lines = [line for line in skill.splitlines() if "llm-wiki-ops page create" in line or "llm-wiki-ops page edit" in line]
    assert lines, "no documented page line"
    for line in lines:
        assert "type=lesson" in line, line
