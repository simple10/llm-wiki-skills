"""channel-hubspot-video, the harness tier: the unit installed and enabled through the REAL
CLI, landing pages in the session wiki. Its helpers and constants are the
unit's own tests' — `skills/channel-hubspot-video/tests/test_hubspot.py`, which ships with
the unit — so a case here reads exactly as it did beside them.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess

from pathlib import Path

from harness import declared_job, rooted, run, ticket_in, unit_tests

# The unit's own helpers, constants and fixtures — the stdlib above is this file's.
globals().update(unit_tests("channel-hubspot-video", "test_hubspot"))


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


def test_what_the_partial_reason_tells_the_operator_to_do_is_real(ops, env, wiki):
    """`every` is not an identity key, so a `once` job can be given a period while a section fills, and back."""
    job = declared_job(ops, env, wiki, UNIT, "https://www.example-hubspot.invalid/continue", slug="port-channel-hubspot-continue")
    assert job.record["every"] == "once"
    for cadence in ("1h", "once"):
        done = run(ops, rooted(env, wiki), "--json", "pipeline", "edit", job.slug, f"every={cadence}")
        assert done.returncode == 0, done.stdout + done.stderr
        assert run(ops, rooted(env, wiki), "--json", "pipeline", "show", job.slug).data["job"]["every"] == cadence
    refused = run(ops, rooted(env, wiki), "--json", "pipeline", "queue", "retry", "0123456789ab")
    assert refused.returncode != 0 and "no finished item" in refused.stdout + refused.stderr  # the verb exists; this ticket never ran


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
        done = subprocess.run(["/bin/sh", "-c", line], env=rooted(env, wiki), capture_output=True, text=True, check=False)
        assert done.returncode == 0, line + "\n" + done.stdout + done.stderr
        assert (wiki / json.loads(done.stdout)["path"]).is_file()
