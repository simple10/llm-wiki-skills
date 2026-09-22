"""channel-substack, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-substack/tests/test_substack.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import hashlib
import json
import pytest
import shlex
import shutil
import subprocess

from pathlib import Path

from harness import declared_job, rooted, ticket_in, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-substack", "test_substack"))


def _script(name, *argv, cwd=None, env=None):
    """A unit script from THIS working tree (the session wiki installs from
    git HEAD), under `uv run` so its PEP 723 dependencies resolve. `cwd` is
    the wiki root when a case drives the script the documented way."""
    return subprocess.run(["uv", "run", "-q", str(SCRIPTS / name), *argv], capture_output=True, text=True, check=False, cwd=cwd, env=env)


def _paged(ops, env, wiki, capture_dir, dest, *keys, body=None):
    """The process step exactly as SKILL.md prescribes it, against the REAL
    CLI: convert the captured `page.html`, `page create` with that markdown on
    stdin, then `page edit … extracted=true`. Returns the page."""
    record = json.loads((capture_dir / "capture.json").read_text(encoding="utf-8"))
    said = json.loads((capture_dir / "leaf.json").read_text(encoding="utf-8"))
    if body is None:
        converted = _script(
            "to_markdown.py", str(capture_dir / record["body"]), "--out", "-",
            "--selector", ".available-content", "--base-url", record["item"], cwd=wiki,
        )
        assert converted.returncode == 0, converted.stderr
        body = converted.stdout
    page = f"{dest}/{record['title'].strip()}.md"
    keys = [f"resource={record['item']}", "type=article", *keys]
    keys += [f"published={said['published']}"] if said.get("published") else []
    keys += [f"audience={said['audience']}"] if said.get("audience") else []
    # The documented lines, as a worker TYPES them: every venue value single-quoted,
    # so every content case is a quoting case too.
    quoted = [f"'{k}'" for k in keys]

    def sh(*words, stdin=body):
        return subprocess.run(["/bin/sh", "-c", " ".join(words)], input=stdin, capture_output=True, text=True, cwd=wiki, env=env)

    created = sh(shlex.join([*ops, "page", "create"]), f"'title={record['title']}'", f"'dest={dest}'", *quoted, "--stdin")
    if created.returncode != 0:
        # The one refusal SKILL.md names: that title is already filed, so edit its page.
        assert created.returncode == 2 and "already exists" in created.stderr, created.stdout + created.stderr
        created = sh(shlex.join([*ops, "page", "edit"]), f"'{page}'", *quoted, "--stdin")
        assert created.returncode == 0, created.stdout + created.stderr
    done = sh(shlex.join([*ops, "page", "edit"]), f"'{page}'", "extracted=true", stdin=None)
    assert done.returncode == 0, done.stdout + done.stderr
    return wiki / page


def _hostile_page():
    html = (FIX / "post-the-newest-one.html").read_text(encoding="utf-8")
    return html.replace('name="author" content="Ada Example"', 'name="author" content="Ada Example&#10;&#10;## Forged by the author&#10;&#10;```"')


def test_one_ticket_lands_every_free_post_as_a_staged_page(ops, env, wiki, monkeypatch, capsys):
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    job = declared_job(ops, env, wiki, UNIT, ARCHIVE)
    assert job.record["harvest"]["scope"] == "domain"  # the manifest's default, which the unit applies itself
    cap = ticket_in(
        wiki, job, "archive--4fbca949", unit=UNIT, item=ARCHIVE,
        # licensed, so the paid post is PLANNED and its preview has to be refused at capture
        harvest={**job.record["harvest"], "access": "licensed", "exclude_urls": [f"{HOST}/p/sponsored-roundup", f"{HOST}/p/founders-letter"]},
        min_date="2026-08-12",
    )

    plan = _plan(monkeypatch, capsys, cap)
    assert _slugs(plan) == ["the-newest-one", "members-only", "a-podcast-episode"]

    # What the worker's fetch leaves: each post's rendered DOM in ITS leaf dir.
    for leaf in plan["leaves"]:
        directory = wiki / leaf["dir"]
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / f"post-{leaf['item'].rsplit('/', 1)[-1]}.html", directory / "page.html")

    # THE DOCUMENTED WAY: cwd is the wiki root (what `llm-wiki-ops run` gives a
    # script) and `--capture-dir` is the ticket's wiki-relative `capture_dir`.
    rel = json.loads((cap / "ticket.json").read_text(encoding="utf-8"))["capture_dir"]
    assert not Path(rel).is_absolute() and (wiki / rel) == cap
    done = _py("capture_posts.py", "--capture-dir", rel, cwd=wiki)  # no --fetch: no network
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["states"] == {"captured": 2, "paywalled": 1}

    wrote = _py("write_report.py", "--capture-dir", rel, cwd=wiki)
    assert wrote.returncode == 0, wrote.stderr
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "partial" and report["ticket"] == "0123456789ab"
    assert [c["dir"] for c in report["captured"]] == [
        f"_raw/{job.slug}/p-the-newest-one--1e31d334",
        f"_raw/{job.slug}/p-a-podcast-episode--{hashlib.sha1(f'{HOST}/p/a-podcast-episode'.encode()).hexdigest()[:8]}",
    ]
    assert report["missing"] == [{"host": "example-newsletter.invalid", "url": f"{HOST}/p/members-only", "why": "auth"}]
    assert not (wiki / plan["leaves"][1]["dir"] / "capture.json").exists()  # a preview is never the article

    record = json.loads((wiki / report["captured"][0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    # Harvest is BYTES: the page as it arrived, and no page's keys on the record.
    assert record["body"] == "page.html" and record["content_type"] == "text/html" and record["slug"] == job.slug
    assert set(record) == {"v", "slug", "item", "title", "body", "content_type", "fetched_at"}
    said = json.loads((wiki / report["captured"][0]["dir"] / "leaf.json").read_text(encoding="utf-8"))
    assert (said["title"], said["published"], said["audience"]) == ("The Newest One", "2026-09-10", "everyone")

    # `apply` mints one process ticket per captured dir; each is one build.
    pages = [_paged(ops, env, wiki, wiki / c["dir"], job.dest) for c in report["captured"]]
    assert len(pages) == 2 and len(set(pages)) == 2
    assert all(page.is_relative_to(wiki / job.dest) and page.is_file() for page in pages)

    newest, podcast = (page.read_text(encoding="utf-8") for page in pages)
    # the page is FILED under the archive's calm title, not the clickbait og:title
    assert newest.splitlines()[1] == "title: The Newest One" and pages[0].name == "The Newest One.md"
    assert f"resource: {HOST}/p/the-newest-one" in newest and "status: draft" in newest
    assert "published: '2026-09-10'" in newest
    assert "lighthouses" in newest and "## A section about lighthouses" in newest
    for chrome in ("SITE-NAV-CHROME", "SUBSCRIBE-BUTTON-CHROME", "COMMENTS-ARE-NOT-THE-ARTICLE", "FOOTER-CHROME"):
        assert chrome not in newest  # `.available-content` is the content root
    # exactly ONE frontmatter block: the page's own
    assert newest.startswith("---\n") and [line for line in newest.splitlines() if line == "---"] == ["---", "---"]
    assert "extracted: 'true'" in newest and "audience: everyone" in newest
    assert "tide tables" in podcast  # the podcast post's own prose, from its content root

    # A slice that died before reporting: the next plan re-lists what is on
    # disk without spending the cap on it, and re-fetches nothing.
    again = _plan(monkeypatch, capsys, cap, None, "--max-leaves", "1")
    assert [(leaf["item"].rsplit("/", 1)[-1], leaf["on_disk"]) for leaf in again["leaves"]] == [
        ("the-newest-one", True), ("members-only", False), ("a-podcast-episode", True)]


def test_two_posts_with_one_title_land_as_two_pages(ops, env, wiki):
    """A page is filed under its title, and the second write of a name takes the
    first's file: before the report settled titles, a newsletter's second "Open
    Thread" WAS the first one's page, and both process tickets said ok."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    host = "https://second-newsletter.invalid"
    job = declared_job(ops, env, wiki, UNIT, f"{host}/archive", slug="port-channel-substack-names")
    cap = ticket_in(wiki, job, "archive--0badc0de", unit=UNIT, item=f"{host}/archive")
    leaves = []
    for name, published, fixture in (("open-thread-2", "2026-09-10", "the-newest-one"), ("open-thread", "2026-08-13", "a-podcast-episode")):
        item = f"{host}/p/{name}"
        rel = f"_raw/{job.slug}/p-{name}--{hashlib.sha1(item.encode()).hexdigest()[:8]}"
        (wiki / rel).mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / f"post-{fixture}.html", wiki / rel / "page.html")
        leaves.append({"item": item, "dir": rel, "title": "Open Thread", "published": published, "audience": "everyone", "on_disk": False})
    (cap / "leaves.json").write_text(json.dumps({
        "v": 1, "ticket": "0123456789ab", "slug": job.slug, "newsletter": "second-newsletter.invalid",
        "capture_dir": str(cap.relative_to(wiki)), "leaves": leaves, "summary": {"truncated": False}}), encoding="utf-8")

    rel = str(cap.relative_to(wiki))
    done = _py("capture_posts.py", "--capture-dir", rel, cwd=wiki)
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 2}, done.stderr
    reports = []
    for _ in range(2):  # a respawned worker reports again: same names
        wrote = _py("write_report.py", "--capture-dir", rel, cwd=wiki)
        assert wrote.returncode == 0, wrote.stderr
        reports.append(json.loads((cap / "report.json").read_text(encoding="utf-8")))
    report = reports[-1]
    assert report["outcome"] == "ok"

    pages = [_paged(ops, env, wiki, wiki / c["dir"], job.dest) for c in report["captured"]]
    assert len({page.resolve() for page in pages}) == 2 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    assert [page.name for page in pages] == ["Open Thread.md", "Open Thread (2026-08-13).md"]
    assert [[c["title"] for c in r["captured"]] for r in reports] == [["Open Thread", "Open Thread (2026-08-13)"]] * 2
    newest, older = (page.read_text(encoding="utf-8") for page in pages)
    assert f"resource: {host}/p/open-thread-2" in newest and "lighthouses" in newest  # still the FIRST post's page
    assert f"resource: {host}/p/open-thread\n" in older and "tide tables" in older


# --- Rule 1: the title is a legal filename ------------------------------------
def test_a_title_the_host_would_refuse_still_lands_and_forges_nothing(ops, env, wiki):
    """Rule 1 + Rule 2, through the REAL `page create`. Before the fix the raw
    title went into `capture.json`, harvest said ok, and the process ticket was
    refused: "a title cannot carry ':'"."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    host = "https://third-newsletter.invalid"
    job = declared_job(ops, env, wiki, UNIT, f"{host}/archive", slug="port-channel-substack-titles")
    cap = ticket_in(wiki, job, "archive--0badf00d", unit=UNIT, item=f"{host}/archive")
    leaves = []
    # The second differs from the first ONLY in characters the host refuses:
    # they collide once both are made safe, which is why safe_title runs first.
    for name, title, published in (("lesson-3", HOSTILE_TITLE, "2026-09-10"), ("lesson-3b", 'Lesson 3: What is "A|B" testing*', "2026-09-03\n# Forged date")):
        item = f"{host}/p/{name}"
        rel = f"_raw/{job.slug}/p-{name}--{hashlib.sha1(item.encode()).hexdigest()[:8]}"
        (wiki / rel).mkdir(parents=True, exist_ok=True)
        (wiki / rel / "page.html").write_text(_hostile_page(), encoding="utf-8")
        leaves.append({"item": item, "dir": rel, "title": title, "published": published, "audience": "everyone", "on_disk": False})
    rel_cap = str(cap.relative_to(wiki))
    (cap / "leaves.json").write_text(json.dumps({
        "v": 1, "ticket": "0123456789ab", "slug": job.slug, "newsletter": "third-newsletter.invalid",
        "capture_dir": rel_cap, "leaves": leaves, "summary": {"truncated": False}}), encoding="utf-8")

    done = _py("capture_posts.py", "--capture-dir", rel_cap, cwd=wiki)
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 2}, done.stderr + done.stdout
    assert _py("write_report.py", "--capture-dir", rel_cap, cwd=wiki).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert [c["title"] for c in report["captured"]] == [f"Lesson 3 - What is ’A-B’ testing # Forged heading ---",
                                                        f"Lesson 3 - What is ’A-B’ testing"]

    record = json.loads((wiki / leaves[0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert record["title"] == report["captured"][0]["title"]
    said = json.loads((wiki / leaves[0]["dir"] / "leaf.json").read_text(encoding="utf-8"))
    assert said["title"] == '.Lesson 3: What is "A/B" testing? # Forged heading ---'  # the true one, on one line
    second = json.loads((wiki / leaves[1]["dir"] / "leaf.json").read_text(encoding="utf-8"))
    assert second["published"] is None  # `2026-09-03\n# Forged date` is no date: dropped, never passed on

    pages = [_paged(ops, env, wiki, wiki / c["dir"], job.dest) for c in report["captured"]]  # the pages LAND
    assert [page.name for page in pages] == [f"{c['title']}.md" for c in report["captured"]]
    lines = pages[0].read_text(encoding="utf-8").splitlines()
    # the venue's title reaches the page QUOTED, opening no heading and no rule of its own
    # Single-quoted by the emitter for the ` #`, and needing no `\'\'` doubling:
    # `safe_title` leaves no ASCII apostrophe in a title any more.
    assert lines[1] == f"title: 'Lesson 3 - What is ’A-B’ testing # Forged heading ---'"
    assert "# Forged heading" not in lines and "# Forged date" not in lines
    assert [line for line in lines if line.strip() == "---"] == ["---", "---"]  # the page's own block only
    assert "lighthouses" in "\n".join(lines)
    # the archive gave the second leaf no date, so its page declares none rather than a guess
    assert not any(line.startswith("published:") for line in pages[1].read_text(encoding="utf-8").splitlines())


def test_a_hundred_cjk_characters_land_and_so_does_their_namesake(ops, env, wiki):
    """The host's `filename_for` checks no LENGTH: 100 CJK characters are 300
    bytes and the write died `OSError: File name too long`. The cap is held in
    UTF-8 bytes — and the report's de-dup qualifier, added AFTER it, still
    fits: this unit's qualifiers are a date, a hash8 and a counter."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    host = "https://fourth-newsletter.invalid"
    job = declared_job(ops, env, wiki, UNIT, f"{host}/archive", slug="port-channel-substack-cjk")
    cap = ticket_in(wiki, job, "archive--0badcafe", unit=UNIT, item=f"{host}/archive")
    leaves = []
    for name, published in (("cjk-2", "2026-09-10"), ("cjk-1", "2026-09-03")):
        item = f"{host}/p/{name}"
        rel = f"_raw/{job.slug}/p-{name}--{hashlib.sha1(item.encode()).hexdigest()[:8]}"
        (wiki / rel).mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "post-the-newest-one.html", wiki / rel / "page.html")
        leaves.append({"item": item, "dir": rel, "title": "語" * 100, "published": published, "audience": "everyone", "on_disk": False})
    rel_cap = str(cap.relative_to(wiki))
    (cap / "leaves.json").write_text(json.dumps({
        "v": 1, "ticket": "0123456789ab", "slug": job.slug, "newsletter": "fourth-newsletter.invalid",
        "capture_dir": rel_cap, "leaves": leaves, "summary": {"truncated": False}}), encoding="utf-8")
    assert _py("capture_posts.py", "--capture-dir", rel_cap, cwd=wiki).returncode == 0
    assert _py("write_report.py", "--capture-dir", rel_cap, cwd=wiki).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    first, second = (c["title"] for c in report["captured"])
    assert first.endswith("…") and len(first.encode()) <= 203 and second == f"{first} (2026-09-03)"
    pages = [_paged(ops, env, wiki, wiki / c["dir"], job.dest) for c in report["captured"]]
    assert [page.name for page in pages] == [f"{first}.md", f"{second}.md"] and all(page.is_file() for page in pages)
    assert max(len(page.name.encode()) for page in pages) <= 255  # what the write dies on


def test_a_title_with_an_apostrophe_survives_the_documented_shell_line(ops, env, wiki):
    """The process step is a shell line a worker TYPES, single-quoting the title
    off `capture.json`. `safe_title` maps BOTH quote forms to U+2019, so no
    title it can produce breaks out of those quotes and loses its page."""
    dest = "sources/scrapes/port-substack-apostrophe"
    for venue in ("Don't Panic", 'He said "no" twice'):
        title = _module("capture_posts").safe_title(venue)
        assert "'" not in title, title
        line = (
            "printf '%s' 'body' | "
            + shlex.join([*ops, "--json", "page", "create"])
            + f" 'title={title}' 'dest={dest}' 'resource=https://example.invalid/x'"
            + f" 'extracted=true' 'type=article' --stdin"
        )
        done = subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), capture_output=True, text=True, check=False)
        assert done.returncode == 0, line + "\n" + done.stdout + done.stderr
        assert (wiki / json.loads(done.stdout)["path"]).is_file()
