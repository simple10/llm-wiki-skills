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

from harness import declared_job, landed, live_ticket, rooted, run, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-substack", "test_substack"))


def _needs_run_verb(ops, env, wiki):
    if run(ops, rooted(env, wiki), "pipeline", "tickets", "run", "--help").returncode != 0:
        pytest.skip("`pipeline tickets run` (spawn=self) is plugins PR 2 (#2486)")


def _script(name, *argv, cwd=None, env=None):
    """A unit script from THIS working tree (the session wiki installs from
    git HEAD), under `uv run` so its PEP 723 dependencies resolve. `cwd` is
    the wiki root when a case drives the script the documented way."""
    return subprocess.run(["uv", "run", "-q", str(SCRIPTS / name), *argv], capture_output=True, text=True, check=False, cwd=cwd, env=env)


def _write_leaves(cap, wiki, newsletter, slug, leaves):
    (cap / "leaves.json").write_text(json.dumps({
        "v": 1, "slug": slug, "newsletter": newsletter,
        "capture_dir": str(cap.relative_to(wiki)), "leaves": leaves, "summary": {"truncated": False}}), encoding="utf-8")


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
    """The whole point of the rework: a live harvest ticket, planned by the
    real enumerator, captured, and its `--report` posting `tickets update`
    through the REAL CLI (no stub — `run` exports `LLM_WIKI_OPS`)."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    _needs_run_verb(ops, env, wiki)
    job = declared_job(ops, env, wiki, UNIT, ARCHIVE)
    assert job.record["harvest"]["scope"] == "domain"  # the manifest's default, which the unit applies itself
    ticket_id, cap = live_ticket(ops, env, wiki, job)

    plan = _plan(monkeypatch, capsys, cap)
    assert set(_slugs(plan)) <= {"the-newest-one", "a-podcast-episode", "sponsored-roundup", "already-held", "ancient-history"}

    # What the worker's fetch leaves: each post's rendered DOM in ITS leaf dir.
    for leaf in plan["leaves"]:
        directory = wiki / leaf["dir"]
        directory.mkdir(parents=True, exist_ok=True)
        fixture = FIX / f"post-{leaf['item'].rsplit('/', 1)[-1]}.html"
        if fixture.is_file():
            shutil.copy(fixture, directory / "page.html")

    # THE DOCUMENTED WAY: cwd is the wiki root (what `llm-wiki-ops run` gives a
    # script) and `--capture-dir` is the ticket's wiki-relative `capture_dir`.
    rel = str(cap.relative_to(wiki))
    done = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py", "--capture-dir", rel, cwd=wiki)
    assert done.returncode == 0, done.stdout + done.stderr

    wrote = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py",
                "--capture-dir", rel, "--report", "--ticket", ticket_id, cwd=wiki)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr

    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", "partial", None), closed

    captured_dirs = [leaf["dir"] for leaf in plan["leaves"] if (wiki / leaf["dir"] / "capture.json").is_file()]
    assert captured_dirs, "nothing captured"
    record = json.loads((wiki / captured_dirs[0] / "capture.json").read_text(encoding="utf-8"))
    # Harvest is BYTES: the page as it arrived, and no page's keys on the record.
    assert record["body"] == "page.html" and record["content_type"] == "text/html" and record["slug"] == job.slug
    assert set(record) == {"v", "slug", "item", "title", "body", "content_type", "fetched_at"}

    # `close` mints one process ticket per captured dir; each is one build.
    pages = [_paged(ops, env, wiki, wiki / d, job.dest) for d in captured_dirs]
    assert pages and len(set(pages)) == len(pages)
    assert all(page.is_relative_to(wiki / job.dest) and page.is_file() for page in pages)


def test_two_posts_with_one_title_land_as_two_pages(ops, env, wiki):
    """A page is filed under its title, and the second write of a name takes the
    first's file: before the report settled titles, a newsletter's second "Open
    Thread" WAS the first one's page, and both process tickets said ok."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    _needs_run_verb(ops, env, wiki)
    host = "https://second-newsletter.invalid"
    job = declared_job(ops, env, wiki, UNIT, f"{host}/archive", slug="port-channel-substack-names")
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    leaves = []
    for name, published, fixture in (("open-thread-2", "2026-09-10", "the-newest-one"), ("open-thread", "2026-08-13", "a-podcast-episode")):
        item = f"{host}/p/{name}"
        rel = f"_raw/{job.slug}/p-{name}--{hashlib.sha1(item.encode()).hexdigest()[:8]}"
        (wiki / rel).mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / f"post-{fixture}.html", wiki / rel / "page.html")
        leaves.append({"item": item, "dir": rel, "title": "Open Thread", "published": published, "audience": "everyone", "on_disk": False})
    _write_leaves(cap, wiki, "second-newsletter.invalid", job.slug, leaves)

    rel = str(cap.relative_to(wiki))
    done = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py", "--capture-dir", rel, cwd=wiki)
    assert done.returncode == 0, done.stdout + done.stderr
    for _ in range(2):  # a respawned worker reports again: same names
        wrote = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py",
                    "--capture-dir", rel, "--report", "--ticket", ticket_id, cwd=wiki)
        assert wrote.returncode == 0, wrote.stdout + wrote.stderr
    closed = landed(ops, env, wiki, ticket_id)
    assert closed.get("status") in ("ok", None), closed

    captured_dirs = [leaf["dir"] for leaf in leaves if (wiki / leaf["dir"] / "capture.json").is_file()]
    pages = [_paged(ops, env, wiki, wiki / d, job.dest) for d in captured_dirs]
    assert len({page.resolve() for page in pages}) == 2 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    assert sorted(page.name for page in pages) == ["Open Thread (2026-08-13).md", "Open Thread.md"]


# --- Rule 1: the title is a legal filename ------------------------------------
def test_a_title_the_host_would_refuse_still_lands_and_forges_nothing(ops, env, wiki):
    """Rule 1 + Rule 2, through the REAL `page create`. Before the fix the raw
    title went into `capture.json`, harvest said ok, and the process ticket was
    refused: "a title cannot carry ':'"."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    _needs_run_verb(ops, env, wiki)
    host = "https://third-newsletter.invalid"
    job = declared_job(ops, env, wiki, UNIT, f"{host}/archive", slug="port-channel-substack-titles")
    ticket_id, cap = live_ticket(ops, env, wiki, job)
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
    _write_leaves(cap, wiki, "third-newsletter.invalid", job.slug, leaves)

    done = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py", "--capture-dir", rel_cap, cwd=wiki)
    assert done.returncode == 0, done.stdout + done.stderr
    wrote = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py",
                "--capture-dir", rel_cap, "--report", "--ticket", ticket_id, cwd=wiki)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr

    record0 = json.loads((wiki / leaves[0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert record0["title"] == "Lesson 3 - What is ’A-B’ testing # Forged heading ---"
    said = json.loads((wiki / leaves[0]["dir"] / "leaf.json").read_text(encoding="utf-8"))
    assert said["title"] == '.Lesson 3: What is "A/B" testing? # Forged heading ---'  # the true one, on one line
    second = json.loads((wiki / leaves[1]["dir"] / "leaf.json").read_text(encoding="utf-8"))
    assert second["published"] is None  # `2026-09-03\n# Forged date` is no date: dropped, never passed on

    captured_dirs = [leaf["dir"] for leaf in leaves if (wiki / leaf["dir"] / "capture.json").is_file()]
    pages = [_paged(ops, env, wiki, wiki / d, job.dest) for d in captured_dirs]
    assert pages and all(page.is_file() for page in pages)
    lines = pages[0].read_text(encoding="utf-8").splitlines()
    # the venue's title reaches the page QUOTED, opening no heading and no rule of its own
    assert lines[1] == "title: 'Lesson 3 - What is ’A-B’ testing # Forged heading ---'"
    assert "# Forged heading" not in lines and "# Forged date" not in lines
    assert [line for line in lines if line.strip() == "---"] == ["---", "---"]  # the page's own block only
    assert "lighthouses" in "\n".join(lines)


def test_a_hundred_cjk_characters_land_and_so_does_their_namesake(ops, env, wiki):
    """The host's `filename_for` checks no LENGTH: 100 CJK characters are 300
    bytes and the write died `OSError: File name too long`. The cap is held in
    UTF-8 bytes — and the report's de-dup qualifier, added AFTER it, still
    fits: this unit's qualifiers are a date, a hash8 and a counter."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: to_markdown.py carries PEP 723 dependencies")
    _needs_run_verb(ops, env, wiki)
    host = "https://fourth-newsletter.invalid"
    job = declared_job(ops, env, wiki, UNIT, f"{host}/archive", slug="port-channel-substack-cjk")
    ticket_id, cap = live_ticket(ops, env, wiki, job)
    leaves = []
    for name, published in (("cjk-2", "2026-09-10"), ("cjk-1", "2026-09-03")):
        item = f"{host}/p/{name}"
        rel = f"_raw/{job.slug}/p-{name}--{hashlib.sha1(item.encode()).hexdigest()[:8]}"
        (wiki / rel).mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "post-the-newest-one.html", wiki / rel / "page.html")
        leaves.append({"item": item, "dir": rel, "title": "語" * 100, "published": published, "audience": "everyone", "on_disk": False})
    rel_cap = str(cap.relative_to(wiki))
    _write_leaves(cap, wiki, "fourth-newsletter.invalid", job.slug, leaves)
    done = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py", "--capture-dir", rel_cap, cwd=wiki)
    assert done.returncode == 0, done.stdout + done.stderr
    wrote = run(ops, env, "run", "ops/skills/channel-substack/scripts/capture_posts.py",
                "--capture-dir", rel_cap, "--report", "--ticket", ticket_id, cwd=wiki)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr

    record0 = json.loads((wiki / leaves[0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    record1 = json.loads((wiki / leaves[1]["dir"] / "capture.json").read_text(encoding="utf-8"))
    first, second = record0["title"], record1["title"]
    assert first.endswith("…") and len(first.encode()) <= 203 and second == f"{first} (2026-09-03)"
    captured_dirs = [leaf["dir"] for leaf in leaves]
    pages = [_paged(ops, env, wiki, wiki / d, job.dest) for d in captured_dirs]
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
