"""channel-hubspot-video on the rebuilt worker contract, in its two steps.

HARVEST is bytes: `leaves.py plan` (given `--ticket`, through `tickets open`)
filters the enumeration and names one capture directory per page,
`leaves.py record` writes the flat `capture.json` naming the rendered
`page.html`, and `leaves.py report --ticket <id>` posts `tickets update` —
the host's own report. PROCESS is this unit's own: the worker converts that
`page.html` with the SITE's selectors — which live in the unit's
`references/sites.json` because nothing reads a manifest `extract` block any
more — and writes the pages through `llm-wiki-ops page create`.

The pure logic is imported and runs everywhere. The end-to-end cases write
into a real wiki through the real CLI (a stand-in front door for `tickets
open`/`tickets update`) and skip where none is at hand. Nothing here touches
the network: the rendered page is a fixture.
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
UNIT = "channel-hubspot-video"
SCRIPTS = UNIT_DIR / "scripts"
LEAVES = SCRIPTS / "leaves.py"
CAPTURE = SCRIPTS / "capture_hubspot_video.py"
FIX = Path(__file__).resolve().parent / "fixtures"

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


# ------------------------------------------------------------ the ticket stub


def _stub_ops(tmp_path: Path, ticket_dict: dict | None) -> str:
    """A stand-in front door: `pipeline tickets open` answers `ticket_dict`
    (refused, naming nothing, where it is None); `pipeline tickets update` is
    recorded to `update-calls.jsonl` and answers a bare 0.

    Written under a dot-directory, never directly in `tmp_path` — several
    callers use `tmp_path` itself as the fake wiki root, and a stray file
    there would read as something a run wrote at the wiki root."""
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


def _cli(*args, tmp_path, ticket_dict=None, cwd=None, extra_env=None):
    """`leaves.py`, run as the worker runs it, with a stand-in front door
    always live (`open_ticket`/`post_update` reach it): `ticket_dict` is what
    `tickets open` answers (a refusal where it is None), and `--ticket <id>`
    is appended when the call names a subcommand that takes one, carries a
    `ticket_dict`, and does not already have one."""
    env = {**os.environ, **(extra_env or {}), "LLM_WIKI_OPS": _stub_ops(tmp_path, ticket_dict)}
    argv = list(args)
    if ticket_dict is not None and argv and argv[0] in ("plan", "report") and "--ticket" not in argv:
        argv += ["--ticket", ticket_dict["ticket"]]
    return _run(*argv, cwd=cwd, env=env)


def ticket(cap: Path, root: Path, **over) -> dict:
    """The ticket `open_ticket` would answer, shaped like `tickets open`'s A-1
    object — `capture_dir` is `cap`'s own wiki-relative name."""
    body = {
        "ticket": "0123456789ab", "slug": "site-learn", "item": SECTION, "target": SECTION,
        "capture_dir": str(cap.relative_to(root)), "dest": None, "hosts": ["www.example-hubspot.invalid"],
        "harvest": {"scope": "section", "exclude_urls": [], "assets": "download"},
        "options": {}, "credential": None, "min_date": "2026-01-01", "known": [], "refresh": False, "resource": None,
    }
    body.update(over)
    cap.mkdir(parents=True, exist_ok=True)
    return body


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


def test_the_update_names_its_status_from_what_landed():
    planned = [{"item": "u1", "dir": "_raw/s/a--1"}, {"item": "u2", "dir": "_raw/s/b--2"}]
    got = [{"item": "u1", "dir": "_raw/s/a--1", "title": "A"}]
    both = [*got, {"item": "u2", "dir": "_raw/s/b--2", "title": None}]
    update = leaves.build_update(planned=planned, captured=both, skipped=[], missing=[])
    assert (update["status"], update["reason"], update["captured"]) == ("ok", None, both)
    assert set(update) == {"status", "reason", "captured"}
    partial = leaves.build_update(planned=planned, captured=got, skipped=[], missing=[])
    assert partial["status"] == "partial" and "1 of 2" in partial["reason"]
    # P-5: a missing url is a LASTING shortfall — named, never bumping `ok` to `partial` on its own.
    denied = [("stream.mux.com", STREAM, "denied")]
    full = leaves.build_update(planned=planned, captured=both, skipped=[], missing=denied)
    assert full["status"] == "ok" and "1 url(s)" in full["reason"]
    known = leaves.build_update(planned=[], captured=[], skipped=[{"url": "u1", "why": "known"}], missing=[])
    assert known["status"] == "ok" and "known: 1" in known["reason"]  # P-4: nothing new is `ok`, never a worker's `skipped`
    assert leaves.build_update(planned=planned, captured=[], skipped=[], missing=[])["status"] == "failed"
    assert leaves.build_update(planned=[], captured=[], skipped=[], missing=[])["status"] == "failed"
    forced = leaves.build_update(planned=planned, captured=got, skipped=[], missing=[], reason="auth_expired:x", failed=True)
    assert forced["status"] == "failed" and forced["captured"] == [] and forced["reason"] == "auth_expired:x"


def test_a_missing_host_row_never_covers_for_a_page_it_does_not_name():
    """R2-1: `--missing-host` is host-level, never tied to a specific page —
    it must not be counted as an attempt on the ONE page that never landed.
    Only a `--missing-leaf` row, matched by the page's own dir, accounts for
    a page by name."""
    planned = [{"item": "u1", "dir": "_raw/s/a--1"}, {"item": "u2", "dir": "_raw/s/b--2"}]
    got = [{"item": "u1", "dir": "_raw/s/a--1", "title": "A"}]
    host_row = [("stream.mux.com", STREAM, "denied")]
    still_partial = leaves.build_update(planned=planned, captured=got, skipped=[], missing=host_row)
    assert still_partial["status"] == "partial" and "1 of 2" in still_partial["reason"]
    # Naming page 2 itself (by dir, as `--missing-leaf` does) makes it a
    # LASTING shortfall instead — `ok`, never `partial`.
    named = leaves.build_update(planned=planned, captured=got, skipped=[], missing=host_row,
                                missing_leaf_dirs=frozenset({"_raw/s/b--2"}))
    assert named["status"] == "ok"


def test_a_process_update_names_its_pages_and_captures_nothing():
    """A process ticket rewrites nothing a capture-freshness rule could read
    against; the update is about `written_from=` instead."""
    status, reason = leaves.process_update(written=["sources/courses/s/Lesson.md", "sources/courses/s/Lesson (video).md"])
    assert (status, reason) == ("ok", None)
    skipped_status, skipped_reason = leaves.process_update(written=[], skipped=True, reason="excluded")
    assert (skipped_status, skipped_reason) == ("ok", "excluded")  # P-4: excluded is `ok`, never a worker's `skipped`
    nothing_status, nothing_reason = leaves.process_update(written=[])
    assert nothing_status == "failed" and nothing_reason


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


# ------------------------------------------------------------ files + a ticket stub, no wiki


def _fill(leaf: Path) -> None:
    leaf.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / "page.html", leaf / "page.html")
    shutil.copy(FIX / "meta.json", leaf / "meta.json")


def test_plan_record_report_from_a_ticket_with_files_only(tmp_path):
    root = tmp_path
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root, known=[{"resource": SECTION, "harvested_at": "2026-08-30T09:12:04Z"}])
    done = _cli("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--sites", str(FIX / "sites.json"), tmp_path=tmp_path, ticket_dict=t)
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

    done = _cli("report", str(cap), tmp_path=tmp_path, ticket_dict=t)
    assert done.returncode == 0, done.stderr
    kv = _kv(_updates(tmp_path)[-1])
    assert kv["status"] == "partial"  # one of two pages was captured
    # S5: what an operator does next, said where the host reports it — a `once` job is never pulled again.
    assert "pipeline tickets retry 0123456789ab" in kv["reason"] and "pipeline jobs edit site-learn every=" in kv["reason"]


def test_everything_known_is_ok_with_nothing_captured_not_a_failure(tmp_path):
    root = tmp_path
    pages, _ = leaves.parse_urls((FIX / "sitemap.xml").read_text(encoding="utf-8"))
    known = [{"resource": page["url"], "harvested_at": None} for page in pages]
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root, known=known)
    assert _cli("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), tmp_path=tmp_path, ticket_dict=t).returncode == 0
    done = _cli("report", str(cap), tmp_path=tmp_path, ticket_dict=t)
    assert done.returncode == 0, done.stderr
    kv = _kv(_updates(tmp_path)[-1])
    assert kv["status"] == "ok" and "known" in kv["reason"] and "captured" not in kv


def test_a_hand_run_with_no_ticket_takes_the_job_as_flags(tmp_path):
    cap = tmp_path / "_raw" / "site-learn" / "learn--00000000"
    cap.mkdir(parents=True)
    refused = _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"))
    assert refused.returncode == 2 and "--slug" in refused.stderr
    done = _run("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--slug", "site-learn", "--target", SECTION)
    assert done.returncode == 0, done.stderr
    # `report` still needs SOME front door to post — a hand run's own stub, with no `tickets open` behind the id.
    done = _cli("report", str(cap), "--ticket", "feedfacecafe", "--failed", "--reason", "auth_expired:www.example-hubspot.invalid", tmp_path=tmp_path, ticket_dict=None)
    assert done.returncode == 1, done.stderr
    call = _updates(tmp_path)[-1]
    kv = _kv(call)
    assert (call[3], kv["status"], kv["reason"]) == ("feedfacecafe", "failed", "auth_expired:www.example-hubspot.invalid")


def test_no_doc_or_manifest_names_the_extract_block_any_more():
    """`skills doctor` fails a manifest carrying a key the contract does not name, and nothing read it."""
    manifest = json.loads((UNIT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert "extract" not in manifest
    for name in ("SKILL.md", "references/customize.md", "references/enable.md"):
        text = (UNIT_DIR / name).read_text(encoding="utf-8")
        assert '"extract"' not in text and "fill the manifest" not in text, name  # no doc shows or asks for the key
    for name in ("SKILL.md", "references/enable.md"):
        assert "references/sites.json" in (UNIT_DIR / name).read_text(encoding="utf-8"), name


def test_the_stages_speak_the_new_contract():
    text = (UNIT_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r'^argument-hint: "ticket=<id>"$', text, re.M)
    for gone in ("<job.", "harvest_apply", "out_of_scope`", "scaffold", "intake.py", "job.py", "watch.py", "drain_pending"):
        assert gone not in text, gone
    for kept in ("capture.json", "data-hsv-src", "stream.mux.com", "verifi.podscribe.com",
                 "tickets open", "tickets update", "### harvest", "### process", "page create", "to_markdown.py"):
        assert kept in text, kept
    for stale in ("$ARGUMENTS", "stage=harvest|process", "ticket.json", "report.json"):
        assert stale not in text, stale
    # Harvest is bytes: no page is rendered there, and no body is one.
    harvest = text.split("### harvest", 1)[1].split("### process", 1)[0]
    assert "page.md" not in harvest and "page create" not in harvest
    # One sentence settles the step, and the reasoning stays in its one home.
    opening = text.split("## Stages", 1)[1].split("### harvest", 1)[0]
    assert " ".join(opening.split()) == (
        "```sh llm-wiki-ops --json pipeline tickets open <id> ``` "
        "The answer's own `stage` — `harvest` or `process` — is the step; the two "
        "sections below are those steps. Either step opens with the policy read — the "
        "stage's overlay, then this unit's own, folded onto the step: "
        "```sh llm-wiki-ops policy get <stage> channel-hubspot-video ```"
    )


def test_every_script_this_unit_ships_is_named_where_a_worker_would_run_it():
    """A script no SKILL.md line runs, and no sibling runs, is dead surface."""
    skill = (UNIT_DIR / "SKILL.md").read_text(encoding="utf-8")
    runs = set(re.findall(r"llm-wiki-ops run \S*/([\w-]+\.py)", skill))
    siblings = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS.glob("*.py"))
    for script in sorted(SCRIPTS.glob("*.py")):
        named = script.name in runs or re.search(rf"\b{re.escape(script.name)}\b", siblings.replace(script.read_text(encoding="utf-8"), ""))
        assert named, f"{script.name} is named on no `llm-wiki-ops run` line and no sibling runs it"


def test_the_quirks_log_holds_venue_facts_and_nothing_else():
    """The log is what this VENUE does, dated. Facts about the plugin, the
    harness or the port belong in the change record, not here."""
    log = (UNIT_DIR / "SKILL.md").read_text(encoding="utf-8").split("## Quirks log", 1)[1]
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


# --- the video: off the asset manifest, and what the transcriber refuses ------


def test_the_downloaded_video_is_read_off_the_asset_manifest(tmp_path):
    """No flag needed: the asset `patch-assets` appended, once `assets.py download`
    marks it `downloaded`, is placed in the leaf — and `--no-media` declines it."""
    root = tmp_path
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root)
    (cap / "urls.json").write_text(json.dumps([LESSON]), encoding="utf-8")  # one page: a `--limit` that left others would be `partial`
    assert _cli("plan", str(cap), "--urls", str(cap / "urls.json"), "--sites", str(FIX / "sites.json"), tmp_path=tmp_path, ticket_dict=t).returncode == 0
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
    done = _cli("report", str(cap), tmp_path=tmp_path, ticket_dict=t)
    assert done.returncode == 0, done.stderr
    kv = _kv(_updates(tmp_path)[-1])
    assert kv["status"] == "ok" and kv["captured"] == leaf.relative_to(root).as_posix()


@pytest.mark.parametrize("bad", ["notes.txt", "missing.mp4"])
def test_a_media_file_the_transcriber_could_not_read_is_refused(tmp_path, bad):
    root = tmp_path
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root)
    assert _cli("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), tmp_path=tmp_path, ticket_dict=t).returncode == 0
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


# ------------------------------------------------------------ Rule 2: venue text forges nothing


def test_venue_text_cannot_forge_a_heading_a_rule_or_an_attribute(tmp_path):
    root = tmp_path
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root)
    assert _cli("plan", str(cap), "--urls", str(FIX / "sitemap.xml"), "--limit", "1", tmp_path=tmp_path, ticket_dict=t).returncode == 0
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
    """A job rooted at a leaf (`every: once`, the manifest default) closing
    silently having captured nothing was the old bug: this is reported `failed`."""
    rows = [{"url": f"https://www.example-hubspot.invalid/p{n}", "why": "scope"} for n in range(9)]
    job = {"scope": "section", "target": LESSON, "slug": "site-learn", "ticket": "t1"}
    update = leaves.build_update(planned=[], captured=[], skipped=[*rows, {"url": "u", "why": "excluded"}], missing=[], job=job)
    assert update["status"] == "failed" and "harvest.scope" in update["reason"] and LESSON in update["reason"] and "scope: 9" in update["reason"]
    for closes in ("known", "older_than_min_date"):
        held = leaves.build_update(planned=[], captured=[], skipped=[*rows, {"url": "u", "why": closes}], missing=[], job=job)
        assert held["status"] == "ok", closes  # P-4: nothing new is `ok`, never a worker's `skipped`


def test_a_job_rooted_at_a_leaf_fails_out_loud(tmp_path):
    root = tmp_path
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root, item=LESSON + "/deeper", target=LESSON + "/deeper")
    shutil.copy(FIX / "sitemap.xml", cap / "sitemap.xml")
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0, done.stderr
    done = _cli("report", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)
    kv = _kv(_updates(tmp_path)[-1])
    assert done.returncode == 1 and kv["status"] == "failed" and "harvest.scope" in kv["reason"] and LESSON + "/deeper" in kv["reason"]


# ------------------------------------------------------------ S5: the clock, and what a second run does


def _documented_section(tmp_path, **ticket_over):
    root = tmp_path
    rel = f"_raw/site-learn/learn--{_hash8(SECTION)}"
    cap = root / rel
    t = ticket(cap, root, **ticket_over)
    shutil.copy(FIX / "sitemap.xml", cap / "sitemap.xml")
    shutil.copy(FIX / "sites.json", cap / "sites.json")
    return root, cap, rel, t


def test_plan_record_and_report_the_documented_way_from_the_wiki_root(tmp_path):
    """Rule 3: `run` starts a script at the WIKI ROOT, so every path is the ticket's wiki-relative `capture_dir`."""
    root, cap, rel, t = _documented_section(tmp_path)
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", tmp_path=tmp_path, ticket_dict=t, cwd=root)
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
    done = _cli("report", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0, done.stderr
    assert _kv(_updates(tmp_path)[-1])["status"] == "partial"
    assert sorted(p.name for p in root.iterdir() if p.name != ".ops-stub") == ["_raw"]  # nothing was written at the wiki root
    # A mistyped or absolute-elsewhere directory is refused with the reason, not a traceback.
    wrong = _cli("report", "_raw/site-learn/nope", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert wrong.returncode == 2 and "wiki-relative" in wrong.stderr and "Traceback" not in wrong.stderr


def test_past_the_deadline_the_worker_is_told_to_stop_and_the_report_says_how_to_go_on(tmp_path):
    """The broker's slice-cap kill fails the ticket WITHOUT reading a report, so the run ends itself first.
    The deadline is keyed to this run's own first write — `plan`'s own clock at the time it ran."""
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    spawned = time.mktime(time.strptime(plan["spawned_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    assert plan["deadline_epoch"] == pytest.approx(spawned + 20 * 60, abs=2)
    assert plan["hard_stop_epoch"] < spawned + 30 * 60
    _fill(root / plan["leaves"][0]["dir"])

    # A run planned 25 minutes ago: no file to backdate any more, so the
    # deadline fields themselves are moved back, as a real spawn 25 minutes
    # ago would have left them.
    plan["deadline_epoch"] -= 25 * 60
    plan["hard_stop_epoch"] -= 25 * 60
    (cap / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    done = _documented(root, "record", rel, "--leaf", "0")
    assert done.returncode == 5 and json.loads(done.stdout)["stop"] is True  # written, AND told to stop
    assert (root / plan["leaves"][0]["dir"] / "capture.json").is_file()
    nxt = _documented(root, "next", rel)
    assert nxt.returncode == 5 and json.loads(nxt.stdout) == {"stop": True, "why": "deadline", "left": 2, "seconds_left": 0}
    done = _cli("report", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)
    kv = _kv(_updates(tmp_path)[-1])
    assert done.returncode == 0 and kv["status"] == "partial" and "1 of 3 pages captured, 2 left" in kv["reason"]
    assert "every: once" in kv["reason"] and "pipeline tickets retry 0123456789ab" in kv["reason"]


def test_a_second_run_does_not_redo_what_a_killed_run_finished(tmp_path):
    """A killed slice mints no process tickets, so `known[]` never grows: without `landed`, the retry
    re-planned every leaf in the same order and hit the same cap."""
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    first = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [leaf["item"] for leaf in first["leaves"]] == [LESSON] and first["limit"] == 1
    _fill(root / first["leaves"][0]["dir"])
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    assert _run("record", rel, "--leaf", "0", "--media-file", str(video), cwd=root).returncode == 0
    done = _cli("report", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0
    limited = _kv(_updates(tmp_path)[-1])
    assert limited["status"] == "partial" and "1 of 3" in limited["reason"]  # every PLANNED page landed, and the limit left two: not `ok`

    # …the slice is killed; the update is never landed; the same ticket is dispatched again.
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0
    second = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert [(leaf["item"], bool(leaf.get("landed"))) for leaf in second["leaves"]] == [
        (LESSON, True), ("https://www.example-hubspot.invalid/learn/offers/lesson-one", False)]  # landed is outside the limit
    nxt = json.loads(_documented(root, "next", rel).stdout)
    assert nxt["item"].endswith("/lesson-one") and nxt["n"] == 1
    done = _cli("report", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0
    call = _updates(tmp_path)[-1]
    captured = [a.split("=", 1)[1] for a in call if a.startswith("captured=")]
    assert captured == [str(first["leaves"][0]["dir"])]  # what the killed run finished is reported by this one


def test_the_limit_defaults_by_what_a_leaf_costs(tmp_path):
    harvest = {"scope": "section", "exclude_urls": [], "assets": "reference"}
    root, cap, rel, t = _documented_section(tmp_path, harvest=harvest)
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert json.loads(done.stdout)["limit"] is None
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--limit", "0", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert json.loads(done.stdout)["limit"] is None


# ------------------------------------------------------------ S7: a refresh ticket


def test_a_refresh_ticket_plans_exactly_its_resource_and_captures_it_again(tmp_path):
    """It used to drop the refreshed page as `known`, walk the rest of the section, and report `failed`."""
    root = tmp_path
    rel = f"_raw/site-learn/{leaves.leaf_name(LESSON)}"  # `jobs.capture_dir_for(slug, resource)`
    cap = root / rel
    t = ticket(
        cap, root, item=LESSON, target=LESSON, dest="sources/courses/site-learn",
        known=[{"resource": LESSON, "harvested_at": "2026-08-30T09:12:04Z"}, {"resource": SECTION, "harvested_at": None}],
        refresh=True, resource=LESSON,
    )
    for stale in ("capture.json", "page.html"):  # the first pull's, in the SAME directory
        (cap / stale).write_text('{"body": "page.html"}', encoding="utf-8")
    done = _cli("plan", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)  # no --urls: there is nothing to enumerate
    assert done.returncode == 0, done.stderr
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    assert plan["leaves"] == [{"item": LESSON, "dir": rel, "lastmod": None}] and plan["skipped"] == []
    assert not any((cap / stale).exists() for stale in ("capture.json", "page.html"))  # forced: a later read hashes THIS run's body
    _fill(cap)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    done = _run("record", rel, "--leaf", "0", "--media-file", str(video), cwd=root)
    assert done.returncode == 0, done.stderr
    # No second transcript stub over a video the wiki already transcribed.
    assert json.loads(done.stdout)["captured"]["media"] is None and not list(cap.glob("media.*"))
    done = _cli("report", rel, tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0
    kv = _kv(_updates(tmp_path)[-1])
    assert kv["status"] == "ok" and kv["captured"] == rel
    # 404/410: the render's status refuses the capture, and `--gone` is the answer.
    meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), "status": 410}
    (cap / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    gone = _documented(root, "record", rel, "--leaf", "0")
    assert gone.returncode == 2 and "--gone" in gone.stderr
    done = _cli("report", rel, "--gone", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0
    kv = _kv(_updates(tmp_path)[-1])
    assert kv["status"] == "gone" and "captured" not in kv


def test_gone_is_a_refresh_tickets_alone_and_a_media_stub_is_not_refreshed_alone(tmp_path):
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    refused = _cli("report", rel, "--gone", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert refused.returncode == 2 and "refresh" in refused.stderr
    assert not _updates(tmp_path)  # a refusal posts nothing

    stream_t = ticket(cap, root, refresh=True, resource=STREAM, item=STREAM, target=STREAM)
    refused = _cli("plan", rel, tmp_path=tmp_path, ticket_dict=stream_t, cwd=root)
    assert refused.returncode == 2 and "media" in refused.stderr
    done = _cli("report", rel, "--failed", "--reason", "refresh_unsupported:media", tmp_path=tmp_path, ticket_dict=stream_t, cwd=root)
    assert done.returncode == 1
    assert _kv(_updates(tmp_path)[-1])["status"] == "failed"


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
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "LLM_WIKI_OPS": str(stub)}
    return env, seen


def test_the_asset_steps_carry_the_pages_url_as_argv_and_never_through_a_shell(tmp_path):
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    leaf = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]
    _fill(root / leaf["dir"])
    (root / leaf["dir"] / "net.json").write_text("[]", encoding="utf-8")
    env, seen = _stub_front_door(tmp_path)
    done = _documented(root, "assets", rel, "--leaf", "0", env=env)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["video"] == "downloaded" and "the plugin script said" not in done.stdout
    detect, download = (json.loads(line) for line in seen.read_text(encoding="utf-8").splitlines())
    script = "scripts/assets.py"
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
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    plan = json.loads((cap / "plan.json").read_text(encoding="utf-8"))
    _fill(root / plan["leaves"][0]["dir"])
    assert _documented(root, "record", rel, "--leaf", "0").returncode == 0
    done = _cli("report", rel, "--missing-leaf", "denied", "0", "--missing-leaf", "timeout", "1",
                "--missing-host", "denied", "Chunk.Mux.com", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0, done.stderr
    call = _updates(tmp_path)[-1]
    missing = [a.split("=", 1)[1] for a in call if a.startswith("missing=")]
    assert missing == [
        f"stream.mux.com,{STREAM},denied",
        f"www.example-hubspot.invalid,{plan['leaves'][1]['item']},timeout",
        "chunk.mux.com,https://chunk.mux.com/,denied",
    ]
    refused = _cli("report", rel, "--missing-host", "denied", "x;$(touch${IFS}PWNED)", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert refused.returncode == 2


def test_render_reads_its_url_off_the_plan(tmp_path):
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--limit", "1", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    (leaf,) = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"]
    assert capturer.planned_leaf(cap, 0) == (leaf["item"], root.resolve() / leaf["dir"])
    assert capturer.planned_leaf(cap, 1) is None and capturer.planned_leaf(cap, -1) is None and capturer.planned_leaf(tmp_path, 0) is None
    assert "--leaf" in subprocess.run([sys.executable, str(CAPTURE), "render", "-h"], capture_output=True, text=True, check=False).stdout


def test_venue_text_on_a_command_line_is_quoted_verbatim_and_never_retyped():
    """The harvest half never lets a url near a shell at all (`--leaf <n>`);
    the process half has the worker paste venue text, under one stated rule."""
    skill = (UNIT_DIR / "SKILL.md").read_text(encoding="utf-8")
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
    root, cap, rel, t = _documented_section(tmp_path)
    (cap / "sitemap.xml").write_text(served, encoding="utf-8")
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 2 and "Traceback" not in done.stderr and "--urls" in done.stderr and "report --failed" in done.stderr
    assert not (cap / "plan.json").exists()
    missing = _cli("plan", rel, "--urls", f"{rel}/never-saved.xml", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert missing.returncode == 2 and "Traceback" not in missing.stderr and "never-saved.xml" in missing.stderr
    done = _cli("report", rel, "--failed", "--reason", "sitemap_unreadable", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 1
    kv = _kv(_updates(tmp_path)[-1])
    call = _updates(tmp_path)[-1]
    assert (call[3], kv["status"], kv["reason"]) == ("0123456789ab", "failed", "sitemap_unreadable")


def test_fetched_at_is_the_renders_time_not_the_time_record_ran(tmp_path):
    root, cap, rel, t = _documented_section(tmp_path)
    assert _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", "--sites", f"{rel}/sites.json", "--limit", "1", tmp_path=tmp_path, ticket_dict=t, cwd=root).returncode == 0
    leaf = root / json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"][0]["dir"]
    _fill(leaf)
    meta = {**json.loads((FIX / "meta.json").read_text(encoding="utf-8")), "fetched_at": "2026-09-01T08:00:00Z"}
    (leaf / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    done = _documented(root, "record", rel, "--leaf", "0", "--no-media")
    assert json.loads(done.stdout)["captured"]["fetched_at_from"] == "meta.json"
    assert json.loads((leaf / "capture.json").read_text(encoding="utf-8"))["fetched_at"] == "2026-09-01T08:00:00Z"


def test_a_sitemap_index_offers_only_children_that_are_safe_and_on_the_targets_host(tmp_path):
    root, cap, rel, t = _documented_section(tmp_path)
    (cap / "sitemap.xml").write_text(
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://www.example-hubspot.invalid/sitemap-1.xml</loc></sitemap>"
        "<sitemap><loc>https://www.example-hubspot.invalid/s.xml;$(touch${IFS}PWNED)</loc></sitemap>"
        "<sitemap><loc>https://elsewhere.example/sitemap.xml</loc></sitemap></sitemapindex>", encoding="utf-8")
    done = _cli("plan", rel, "--urls", f"{rel}/sitemap.xml", tmp_path=tmp_path, ticket_dict=t, cwd=root)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["sitemaps"] == ["https://www.example-hubspot.invalid/sitemap-1.xml"]
