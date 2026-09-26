"""`channel-substack` on the rebuilt pipeline's worker contract, both steps.

HARVEST is bytes: `enumerate_archive.py` applies the job's own rules and plans
the leaf capture directories, `capture_posts.py` writes each post's
`page.html`, `leaf.json` and a flat `capture.json`, and `capture_posts.py
--report` lists every leaf in `captured[]` and posts `tickets update`.

PROCESS is the unit's own: one ticket per captured leaf, the SKILL's three
commands — `to_markdown.py`, then `page create --stdin`, then `page edit
extracted=true` — driven here against the REAL CLI, and `capture_posts.py
--report --written-from` reporting the page.

No network anywhere: the archive API is a fixture (`fixtures/
archive.json`) served through a stubbed `fetch_page`, and post pages are
fixture HTML laid in the leaf directories the way a worker's fetch would.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


UNIT_DIR = Path(__file__).resolve().parents[1]  # the unit, wherever its tree sits
SCRIPTS = UNIT_DIR / "scripts"
FIX = Path(__file__).resolve().parent / "fixtures"
UNIT = "channel-substack"
HOST = "https://example-newsletter.invalid"
ARCHIVE = f"{HOST}/archive"


def _module(name):
    spec = importlib.util.spec_from_file_location(f"substack_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ticket(slug="news", **over):
    ticket = {
        "v": 1, "ticket": "0123456789ab", "unit": UNIT, "slug": slug, "item": ARCHIVE, "target": ARCHIVE,
        "capture_dir": f"_raw/{slug}/archive--4fbca949", "dest": None, "hosts": ["example-newsletter.invalid"],
        "harvest": {"scope": "domain", "access": "free", "max_age": None, "refresh": "never", "exclude_urls": [], "assets": "reference"},
        "options": {}, "credential": None, "min_date": None, "known": [],
    }
    ticket.update(over)
    return ticket


def _plan(monkeypatch, capsys, ticket_dir, ticket=None, *argv):
    """The enumerator over the fixture archive, IN PROCESS (the archive API is
    stubbed, which a subprocess cannot be) and therefore with an ABSOLUTE
    `--capture-dir`; everything else comes off `tickets open`, stubbed
    in-process. That is NOT how a worker runs it — `llm-wiki-ops run` starts
    a script at the wiki root with the ticket's wiki-relative `capture_dir`
    — and the cases under "the documented way" below drive all three
    scripts exactly so."""
    mod = _module("enumerate_archive")
    extra_argv = ["--capture-dir", str(ticket_dir)]
    if ticket is not None:
        ticket_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod, "open_ticket", lambda tid, stage=None: ticket)
        extra_argv += ["--ticket", ticket["ticket"]]
    archive = json.loads((FIX / "archive.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(mod, "fetch_page", lambda domain, offset, limit: archive[offset:offset + limit])
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", *extra_argv, *argv])
    mod.main()
    return json.loads(capsys.readouterr().out)


def _slugs(plan):
    return [leaf["item"].rsplit("/", 1)[-1] for leaf in plan["leaves"]]


# ---- leaf naming --------------------------------------------------------


def test_a_leaf_dir_is_the_hosts_own_shape():
    """`<path folded to a slug>--<first 8 hex of sha1(url)>` under the job's
    `_raw/<slug>/` — `pipeline/jobs.py::capture_dir_for`, which has no verb.
    The hash is a literal so a change of algorithm cannot move both sides."""
    mod = _module("enumerate_archive")
    url = f"{HOST}/p/the-newest-one"
    assert mod.leaf_dir("news", url) == "_raw/news/p-the-newest-one--1e31d334"
    assert hashlib.sha1(url.encode()).hexdigest()[:8] == "1e31d334"
    # Exactly three components: the only shape `apply` mints a process ticket for.
    assert len(mod.leaf_dir("news", f"{HOST}/p/a/b/c?x=1").split("/")) == 3
    # The slug is capped at 60, and an empty path falls back to the host.
    assert len(mod.leaf_name(f"{HOST}/p/" + "x" * 200).rsplit("--", 1)[0]) == 60
    assert mod.leaf_name(HOST).startswith("example-newsletter-invalid--")


# ---- the job's rules, applied by the unit -------------------------------


def test_the_ticket_alone_drives_the_plan(monkeypatch, capsys, tmp_path):
    """Free access, domain scope: the paid tiers and the off-host cross-post
    are dropped and COUNTED, newest first, and the plan lands beside the ticket."""
    cap = tmp_path / "_raw/news/archive--4fbca949"
    plan = _plan(monkeypatch, capsys, cap, _ticket())

    assert _slugs(plan) == ["the-newest-one", "a-podcast-episode", "sponsored-roundup", "already-held", "ancient-history"]
    assert plan["summary"]["skipped_paywalled"] == 2  # only_paid + founding
    assert plan["summary"]["skipped_by_scope"] == 1  # elsewhere.invalid
    assert plan["summary"]["by_audience"] == {"everyone": 6, "only_paid": 1, "founding": 1}
    first = plan["leaves"][0]
    assert first == {"item": f"{HOST}/p/the-newest-one", "dir": "_raw/news/p-the-newest-one--1e31d334",
                     "title": "The Newest One", "published": "2026-09-10", "audience": "everyone", "on_disk": False}
    assert json.loads((cap / "leaves.json").read_text(encoding="utf-8")) == plan
    assert "discovered" not in plan


def test_min_date_known_and_exclude_urls_are_the_units_to_apply(monkeypatch, capsys, tmp_path):
    ticket = _ticket(
        min_date="2026-01-01",
        known=[{"resource": f"{HOST}/p/already-held", "harvested_at": "2026-08-11T00:00:00Z"}],
        harvest={"scope": "domain", "access": "free", "exclude_urls": [f"{HOST}/p/sponsored-*"]},
    )
    plan = _plan(monkeypatch, capsys, tmp_path / "_raw/news/archive--4fbca949", ticket)

    assert _slugs(plan) == ["the-newest-one", "a-podcast-episode"]
    assert plan["summary"]["skipped_known"] == 1
    assert plan["summary"]["skipped_excluded"] == 1
    assert plan["summary"]["stopped_at_min_date"] is True


def test_licensed_access_plans_every_tier(monkeypatch, capsys, tmp_path):
    ticket = _ticket(harvest={"scope": "domain", "access": "licensed", "exclude_urls": []})
    plan = _plan(monkeypatch, capsys, tmp_path / "_raw/news/archive--4fbca949", ticket)
    assert {"members-only", "founders-letter"} <= set(_slugs(plan))
    assert plan["summary"]["skipped_paywalled"] == 0


@pytest.mark.parametrize("scope", ["page", "section"])
def test_a_narrow_scope_on_an_archive_plans_nothing_and_says_so(monkeypatch, capsys, tmp_path, scope):
    """The old host filter rejected every post of a page-scoped watch while
    the run still exited 0. The unit applies scope itself now: posts live at
    `/p/<slug>`, never under `/archive`, so nothing survives — loudly, and the
    report FAILS naming the scope instead of landing an empty success."""
    mod = _module("enumerate_archive")
    assert mod.in_scope(f"{HOST}/p/x", ARCHIVE, "domain")
    assert mod.in_scope("https://www.example-newsletter.invalid/p/x", ARCHIVE, "domain")
    assert not mod.in_scope(f"{HOST}/p/x", ARCHIVE, scope)
    assert mod.in_scope(f"{HOST}/archive/2026", ARCHIVE, "section")
    assert mod.in_scope(ARCHIVE + "/", ARCHIVE, "page")

    cap = tmp_path / "_raw/news/archive--4fbca949"
    plan = _plan(monkeypatch, capsys, cap, _ticket(harvest={"scope": scope, "access": "free", "exclude_urls": []}))
    assert plan["leaves"] == [] and plan["summary"]["skipped_by_scope"] == 8

    report = _module("capture_posts").build_update(plan, [], cap.parent)
    assert report["status"] == "failed" and "harvest.scope" in report["reason"]


def test_exclude_urls_matches_exact_path_prefix_and_star_glob():
    """WAS `..._exact_prefix_and_glob`, which pinned a bare STRING prefix:
    `/p/sponsored` dropped `/p/sponsored-roundup`, and so `/p/a` dropped
    `/p/ab`. A prefix ends on a path-segment boundary now, and `?` is a URL
    character, not a wildcard."""
    mod = _module("enumerate_archive")
    url = f"{HOST}/p/sponsored-roundup"
    assert mod.excluded(url, [url + "/"]) and mod.excluded(url + "/", [url])
    assert mod.excluded(url, [f"{HOST}/p"]) and mod.excluded(url, [f"{HOST}/p/"]) and mod.excluded(url, [HOST])
    assert mod.excluded(url + "/comments", [url]) and mod.excluded(url + "?utm=1", [url])
    assert not mod.excluded(url, [f"{HOST}/p/sponsored"])  # `/p/a` must never drop `/p/ab`
    assert not mod.excluded(f"{HOST}/p/ab", [f"{HOST}/p/a"])
    assert mod.excluded(url, ["*/p/sponsored-*"])
    assert not mod.excluded(f"{HOST}/p/ab", [f"{HOST}/p/a?"])  # not a glob: `?` opens a query
    assert mod.excluded(f"{HOST}/p/a?x=1", [f"{HOST}/p/a?x=*"])
    assert not mod.excluded(url, [f"{HOST}/p/other", "", None, "*/q/*"])


def test_a_post_target_and_a_refresh_ticket_plan_one_leaf_in_the_tickets_own_dir(monkeypatch, capsys, tmp_path):
    post = f"{HOST}/p/the-newest-one"
    mod = _module("enumerate_archive")
    monkeypatch.setattr(mod, "fetch_page", lambda *a: pytest.fail("a single post needs no archive walk"))

    def plan_for(ticket, name):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(mod, "open_ticket", lambda tid, stage=None: ticket)
        monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(directory), "--ticket", ticket["ticket"]])
        mod.main()
        return json.loads(capsys.readouterr().out)

    own = "_raw/news/p-the-newest-one--1e31d334"
    single = plan_for(_ticket(item=post, target=post, capture_dir=own, harvest={"scope": "page", "access": "free"}), "a")
    assert [(leaf["item"], leaf["dir"]) for leaf in single["leaves"]] == [(post, own)]

    held = [{"resource": post, "harvested_at": "2026-09-11T00:00:00Z"}]
    assert plan_for(_ticket(item=post, target=post, capture_dir=own, known=held), "b")["leaves"] == []
    # A refresh re-fetches a page the job HOLDS — `known[]` must not skip it.
    refresh = plan_for(_ticket(capture_dir=own, known=held, refresh=True, resource=post), "c")
    assert [(leaf["item"], leaf["dir"]) for leaf in refresh["leaves"]] == [(post, own)]


# ---- what a post page yields --------------------------------------------


def test_meta_is_read_whatever_order_its_attributes_come_in():
    mod = _module("capture_posts")
    html = (FIX / "post-the-newest-one.html").read_text(encoding="utf-8")
    assert mod.meta(html, "og:title") == "You Won't Believe The Newest One & More"  # unescaped, `data-rh` first
    assert mod.meta(html, "author") == "Ada Example"
    assert mod.meta("<html><body>nothing declared</body></html>", "og:title") is None


def test_a_paywall_preview_is_told_from_a_complete_paid_post():
    mod = _module("capture_posts")
    assert mod.is_paywall_preview((FIX / "post-members-only.html").read_text(encoding="utf-8"))
    assert not mod.is_paywall_preview((FIX / "post-the-newest-one.html").read_text(encoding="utf-8"))
    # A licensed session reads the whole of a post that still declares itself paid.
    assert not mod.is_paywall_preview('<script type="application/ld+json">{"isAccessibleForFree":false}</script><p>all of it</p>')


# ---- the report ---------------------------------------------------------


def _land(root, leaf, title="T"):
    directory = root / leaf["dir"].rsplit("/", 1)[-1]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page.html").write_text("<html><body>T</body></html>\n", encoding="utf-8")
    (directory / "capture.json").write_text(json.dumps({"item": leaf["item"], "title": title, "body": "page.html"}), encoding="utf-8")


def test_captured_is_what_is_on_disk_and_the_outcome_follows(tmp_path):
    build = _module("capture_posts").build_update
    leaves = [{"item": f"{HOST}/p/a", "dir": "_raw/news/p-a--11111111"}, {"item": f"{HOST}/p/b", "dir": "_raw/news/p-b--22222222"}]
    plan = {"ticket": "abc", "newsletter": "example-newsletter.invalid", "leaves": leaves, "summary": {"truncated": False}}

    nothing = build(plan, [], tmp_path)
    assert nothing["status"] == "failed" and nothing["captured"] == []

    _land(tmp_path, leaves[0])
    rows = [{"item": leaves[1]["item"], "state": "paywalled", "why": "auth"}]
    partial = build(plan, rows, tmp_path)
    # P-5: a paywall is a LASTING fact about that one post, not a shortfall a
    # re-run fixes — `ok`, with the post in `missing[]` and `reason` naming it.
    assert partial["status"] == "ok" and "1 of 2" in partial["reason"] and "paywalled" in partial["reason"]
    assert partial["captured"] == [{"item": leaves[0]["item"], "dir": leaves[0]["dir"], "title": "T"}]
    assert partial["missing"] == [{"host": "example-newsletter.invalid", "url": leaves[1]["item"], "why": "auth"}]

    # A capture record whose body is not there is not a capture.
    (tmp_path / "p-b--22222222").mkdir()
    (tmp_path / "p-b--22222222/capture.json").write_text(json.dumps({"body": "page.html"}), encoding="utf-8")
    assert len(build(plan, [], tmp_path)["captured"]) == 1

    _land(tmp_path, leaves[1])
    assert build(plan, [], tmp_path)["status"] == "ok"
    # The whole plan landed, but the plan was not the whole archive.
    capped = build({**plan, "summary": {"truncated": True}}, [], tmp_path)
    assert capped["status"] == "partial" and "known[]" in capped["reason"]


def test_an_empty_plan_is_skipped_and_a_dead_session_is_auth_expired(tmp_path):
    build = _module("capture_posts").build_update
    empty = build({"newsletter": "n.invalid", "leaves": [], "summary": {"skipped_known": 9}}, [], tmp_path)
    assert empty["status"] == "ok" and empty["reason"].startswith("known:")

    leaves = [{"item": f"{HOST}/p/a", "dir": "_raw/news/p-a--11111111"}]
    dead = build({"newsletter": "n.invalid", "leaves": leaves, "summary": {}}, [{"item": leaves[0]["item"], "state": "paywalled", "why": "auth"}], tmp_path)
    assert dead["status"] == "failed" and dead["reason"] == "auth_expired:n.invalid"


# ---- page names: one page per post, whatever two posts are called --------


def test_the_page_key_is_the_hosts_filename_rule_plus_what_a_filesystem_folds():
    """`page/note.py::filename_for` is `title.strip() + ".md"` and nothing else:
    outer whitespace is all the HOST folds; case is what a case-insensitive
    filesystem folds under it."""
    mod = _module("capture_posts")
    assert mod.page_key("Open Thread") == mod.page_key(" Open Thread\n") == mod.page_key("OPEN THREAD")
    assert mod.page_key("Open Thread") != mod.page_key("Open Thread!")  # nothing else is dropped
    assert mod.TITLE_ILLEGAL == '/\\:*?"<>|'  # `page/note.py::ILLEGAL` — a title carrying one is refused
    taken = {}
    assert mod.unique_title("Links ", ["2026-09-01", "aaaaaaaa"], taken) == "Links "  # the first: untouched
    assert mod.unique_title("links", ["2026-09-08", "bbbbbbbb"], taken) == "links (2026-09-08)"
    assert mod.unique_title("Links", ["2026-09-01", "cccccccc"], taken) == "Links (cccccccc)"  # the holder's own day tells nothing apart
    assert mod.unique_title("Links", ["2026-09-08", "dddddddd"], taken) == "Links (dddddddd)"  # that name is taken
    assert mod.unique_title("Links", ["2026-09-08"], taken) == "Links (2026-09-08) (2)"
    assert mod.qualifier("2026-09-08T10:00:00Z / x") == "2026-09-08T10-00-00Z - x"


def test_same_titled_posts_are_told_apart_by_the_day_they_were_published(tmp_path):
    mod = _module("capture_posts")
    leaves = [
        {"item": f"{HOST}/p/gone", "dir": "_raw/news/p-gone--00000000", "title": "Open Thread", "published": "2026-09-15"},
        {"item": f"{HOST}/p/a", "dir": "_raw/news/p-a--11111111", "title": "Open Thread", "published": "2026-09-08"},
        {"item": f"{HOST}/p/b", "dir": "_raw/news/p-b--22222222", "title": "Open Thread", "published": None},
        {"item": f"{HOST}/p/c", "dir": "_raw/news/p-c--33333333", "title": "Something Else", "published": "2026-09-01"},
    ]
    plan = {"ticket": "abc", "newsletter": "example-newsletter.invalid", "leaves": leaves, "summary": {}}
    for leaf in leaves[1:]:  # the newest never landed: it still holds the archive's title
        _land(tmp_path, leaf, title=leaf["title"])
    want = ["Open Thread (2026-09-08)", f"Open Thread ({hashlib.sha1(leaves[2]['item'].encode()).hexdigest()[:8]})", "Something Else"]
    for _ in range(2):  # a second report renames nothing a second time
        report = mod.build_update(plan, [], tmp_path)
        assert [c["title"] for c in report["captured"]] == want
        assert [json.loads((tmp_path / leaf["dir"].rsplit("/", 1)[-1] / "capture.json").read_text(encoding="utf-8"))["title"]
                for leaf in leaves[1:]] == want
    # `--only` re-renders one leaf with its plain title; the next report settles it to the SAME name.
    _land(tmp_path, leaves[1], title="Open Thread")
    assert [c["title"] for c in mod.build_update(plan, [], tmp_path)["captured"]] == want
    # With nothing before it, the first LANDED post keeps its title untouched.
    report = mod.build_update({**plan, "leaves": leaves[1:]}, [], tmp_path)
    assert [c["title"] for c in report["captured"]][0] == "Open Thread (2026-09-08)"  # already settled: left as it is
    _land(tmp_path, leaves[1], title="Open Thread")
    assert [c["title"] for c in mod.build_update({**plan, "leaves": leaves[1:]}, [], tmp_path)["captured"]][:2] == ["Open Thread", want[1]]


# --- the scripts from the wiki root, on relative paths ------------------------
#
# `llm-wiki-ops run` starts a script with the wiki root as its cwd
# (`commands/run/run.py::_exec`), NOT in the capture directory the worker
# stands in. Every case above this line passes an absolute `--capture-dir`.


POST = f"{HOST}/p/the-newest-one"
OWN = "_raw/news/p-the-newest-one--1e31d334"
STDLIB = ("enumerate_archive.py", "capture_posts.py")  # no dependencies: plain python runs them


def _py(name, *argv, cwd, env=None):
    run_env = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / name), *argv], capture_output=True, text=True, check=False, cwd=cwd, env=run_env)


def _stub_ops(root, ticket=None):
    """A stand-in front door: `pipeline tickets open` answers `ticket`;
    `pipeline tickets update` is recorded to `update-calls.jsonl` and
    answers a bare 0 — the one `LLM_WIKI_OPS` `open_ticket`/`post_update`
    both reach through when these scripts run as real subprocesses."""
    stub = root / "ops_stub.py"
    stub.write_text(
        "import json, pathlib, sys\n"
        "argv = [a for a in sys.argv[1:] if a != '--json']\n"
        f"TICKET = json.loads({json.dumps(json.dumps(ticket))})\n"
        "if argv[:3] == ['pipeline', 'tickets', 'open']:\n"
        "    print(json.dumps({'ticket': TICKET}))\n"
        "    sys.exit(0)\n"
        "if argv[:3] == ['pipeline', 'tickets', 'update']:\n"
        "    pathlib.Path('update-calls.jsonl').open('a').write(json.dumps(argv) + '\\n')\n"
        "    sys.exit(0)\n"
        "sys.exit('ops_stub: unhandled ' + repr(argv))\n"
    )
    return shlex.join([sys.executable, str(stub)])


def _updates(root):
    path = root / "update-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _kv(argv):
    return dict(a.split("=", 1) for a in argv if "=" in a and not a.startswith("--"))


def _root_with_post_ticket(tmp_path, **over):
    """A stand-in wiki root and the ticket whose target IS a post — the one
    plan the enumerator makes with no archive call, so a subprocess can run
    it with no network. Returns `(root, ticket, env)`."""
    root = tmp_path / "wiki"
    (root / OWN).mkdir(parents=True)
    ticket = _ticket(item=POST, target=POST, capture_dir=OWN, **over)
    env = {"LLM_WIKI_OPS": _stub_ops(root, ticket=ticket)}
    return root, ticket, env


def _litter(root):
    return sorted(path.name for path in root.iterdir() if path.name not in ("_raw", "ops_stub.py", "update-calls.jsonl"))


def test_no_script_runs_without_a_capture_dir_and_none_writes_at_the_wiki_root(tmp_path):
    """The blocker, as the reviewer hit it through the real `run`: no
    arguments, cwd the wiki root. Each script must REFUSE."""
    root, _ticket_, _env = _root_with_post_ticket(tmp_path)
    for name in STDLIB:
        done = _py(name, cwd=root)
        assert done.returncode == 2 and "--capture-dir" in done.stderr, (name, done.stderr)
    assert _litter(root) == []

    # Pointed at a directory that is no ticket's (the wiki root itself), with
    # no --ticket and no domain: refused, nothing written.
    done = _py("enumerate_archive.py", "--capture-dir", ".", cwd=root)
    assert done.returncode == 2 and "--ticket" in done.stderr and _litter(root) == []
    # A capture dir that is not there (an absolute-minded path from the wrong cwd) says what it wants.
    done = _py("enumerate_archive.py", "--capture-dir", "_raw/news/nope", cwd=root)
    assert done.returncode == 2 and "wiki-relative" in done.stderr


def test_the_enumerator_and_the_report_run_from_the_wiki_root_on_relative_paths(tmp_path):
    root, ticket, env = _root_with_post_ticket(tmp_path)

    planned = _py("enumerate_archive.py", "--capture-dir", OWN, "--ticket", ticket["ticket"], cwd=root, env=env)
    assert planned.returncode == 0, planned.stderr
    plan = json.loads((root / OWN / "leaves.json").read_text(encoding="utf-8"))
    assert [(leaf["item"], leaf["dir"]) for leaf in plan["leaves"]] == [(POST, OWN)]

    # A hand run's relative `--out` lands INSIDE the capture dir, never at the wiki root.
    hand = root / "_raw/news/hand"
    hand.mkdir()
    out = _py("enumerate_archive.py", POST, "--slug", "news", "--capture-dir", "_raw/news/hand", "--out", "leaves.json", cwd=root)
    assert out.returncode == 0 and (hand / "leaves.json").is_file(), out.stderr

    # Nothing captured: `tickets update` posts `status=failed` — with the ticket's id.
    # The script's OWN exit is 0: the `tickets update` CALL succeeded, whatever it reported.
    wrote = _py("capture_posts.py", "--capture-dir", OWN, "--report", "--ticket", ticket["ticket"], cwd=root, env=env)
    assert wrote.returncode == 0, wrote.stderr
    call = _updates(root)[-1]
    assert call[3] == ticket["ticket"] and _kv(call)["status"] == "failed"

    # A malformed `--missing` is refused before anything is posted.
    before = len(_updates(root))
    bad = _py("capture_posts.py", "--capture-dir", OWN, "--report", "--ticket", ticket["ticket"], "--missing", "nonsense", cwd=root, env=env)
    assert bad.returncode == 2
    assert len(_updates(root)) == before


def test_capture_posts_runs_from_the_wiki_root_on_relative_paths(tmp_path):
    """The capture arm needs no ops CLI: a stand-in root, the fixture page in
    the leaf dir, and the script with cwd the root and `--capture-dir`
    relative. `--report` at the end does."""
    root, ticket, env = _root_with_post_ticket(tmp_path)
    assert _py("enumerate_archive.py", "--capture-dir", OWN, "--ticket", ticket["ticket"], cwd=root, env=env).returncode == 0
    shutil.copy(FIX / "post-the-newest-one.html", root / OWN / "page.html")

    done = _py("capture_posts.py", "--capture-dir", OWN, cwd=root)
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 1}, done.stderr
    for name in ("capture.json", "leaf.json", "results.json"):
        assert (root / OWN / name).is_file(), name
    assert not (root / OWN / "page.md").exists()  # harvest renders nothing
    # a relative --plan is inside the capture dir too
    again = _py("capture_posts.py", "--capture-dir", OWN, "--plan", "leaves.json", "--only", POST, cwd=root)
    assert again.returncode == 0, again.stderr
    reported = _py("capture_posts.py", "--capture-dir", OWN, "--report", "--ticket", ticket["ticket"], cwd=root, env=env)
    assert reported.returncode == 0, reported.stderr
    assert _kv(_updates(root)[-1])["status"] == "ok"


def test_a_process_ticket_reports_the_page_it_wrote(tmp_path):
    """`--written-from` makes it the PROCESS ticket's report: the pages, and
    no capture. A process ticket captures nothing, so the refusal guarding a
    success claimed over an empty capture directory does not apply to it."""
    root, ticket, env = _root_with_post_ticket(tmp_path)
    page = "sources/newsletters/news/The Newest One.md"
    (root / OWN / "written.json").write_text(json.dumps([page]), encoding="utf-8")
    wrote = _py("capture_posts.py", "--capture-dir", OWN, "--report", "--ticket", ticket["ticket"],
               "--written-from", "written.json", cwd=root, env=env)
    assert wrote.returncode == 0, wrote.stderr
    call = _updates(root)[-1]
    kv = _kv(call)
    assert call[3] == ticket["ticket"] and kv["stage"] == "process" and kv["status"] == "ok"
    assert kv["written_from"] == "written.json"

    # A capture that earns no page says so instead, with no `--written-from` behind it.
    skipped = _py("capture_posts.py", "--capture-dir", OWN, "--report", "--ticket", ticket["ticket"], "--process",
                  "--reason", "excluded: the job's rules drop it", cwd=root, env=env)
    assert skipped.returncode == 0, skipped.stderr
    kv = _kv(_updates(root)[-1])
    assert kv["status"] == "ok" and kv["reason"].startswith("excluded:") and "written_from" not in kv

# ---- Rule 1: the title is a legal filename ---------------------------------


def test_safe_title_is_what_the_hosts_filename_rule_will_hold():
    safe = _module("capture_posts").safe_title
    assert safe("Lesson 3: Pricing") == "Lesson 3 - Pricing"
    assert safe('What is "A/B" testing?') == "What is ’A-B’ testing"
    assert safe(".hidden <draft> | notes\\x*") == "hidden (draft) - notes-x"
    assert safe("  ...  ") == "Untitled" and safe(None) == "Untitled" and safe("", fallback="p-slug") == "p-slug"
    assert safe("one\ntwo\tthree\x00four\x1f") == "one two three four"
    assert safe("Ends with a dot. ") == "Ends with a dot"
    long = safe("x" * 300)
    assert len(long) == 121 and long.endswith("…")
    wide = safe("語" * 300)  # 3 bytes each: the cap is held in BYTES too
    assert len((wide + " (2026-09-08).md").encode()) <= 255 and wide.endswith("…")
    # Whatever comes out is a name `page/note.py::filename_for` accepts.
    for text in ("a:b", "?", "..", "\x07", "C:\\Users\\x", "<>", "*" * 9, " . leading"):
        out = safe(text)
        assert out and not out.startswith(".") and out == out.strip()
        assert not any(ch in '/\\:*?"<>|' or ord(ch) < 32 for ch in out)


HOSTILE_TITLE = '.Lesson 3: What is "A/B" testing?\n\n# Forged heading\n\n---\n'
HOSTILE_AUTHOR = "Ada Example\n\n## Forged by the author\n\n```"


def test_no_qualifier_this_unit_adds_can_push_a_capped_title_past_a_filename():
    """Worst case, by arithmetic and by the functions themselves: the longest
    safe title, a namesake on another day, then the counter."""
    capture = report = _module("capture_posts")
    title = capture.safe_title("語" * 300)
    taken = {}
    names = [report.unique_title(title, ["2026-09-08", "aaaaaaaa"], taken) for _ in range(12)]
    names += [report.unique_title(title, ["2026-09-08"], taken) for _ in range(120)]
    assert len(set(names)) == len(names)
    assert max(len(f"{name}.md".encode()) for name in names) <= 255


def test_a_date_is_a_fact_never_free_text():
    valid_day = _module("capture_posts").valid_day
    assert valid_day("2026-09-10") == "2026-09-10"
    assert valid_day("2026-09-10T00:00") is None and valid_day("2026-09-10\n# x") is None and valid_day(None) is None


# ---- S9: a page that is not a post is never the article ---------------------

CHALLENGE = "<html><head><title>Just a moment...</title></head><body><h1>example-newsletter.invalid</h1><p>Verifying you are human. This may take a few seconds.</p></body></html>"
LOGIN = "<html><head><title>Sign in - Substack</title></head><body><h1>Sign in</h1><form><input type=email></form></body></html>"
SHELL = '<html><head><title>x</title></head><body><div id="root"></div><script>var s = "available-content"</script></body></html>'


def test_a_page_without_the_content_root_is_told_apart():
    mod = _module("capture_posts")
    assert mod.unusable((FIX / "post-the-newest-one.html").read_text(encoding="utf-8")) is None
    assert mod.unusable(CHALLENGE)[0] == "error" and "just a moment" in mod.unusable(CHALLENGE)[1]
    assert mod.unusable(LOGIN)[0] == "auth"
    assert mod.unusable(SHELL)[0] == "error"
    # A real post may SAY any of it: the signals are read only where there is no root.
    assert mod.unusable('<div class="body available-content"><h1>Sign in</h1><p>just a moment, captcha</p></div>') is None


@pytest.mark.parametrize(("html", "why"), [(CHALLENGE, "error"), (LOGIN, "auth"), (SHELL, "error")])
def test_a_block_page_is_an_error_row_and_its_html_is_out_of_the_way(tmp_path, html, why):
    """Before: `to_markdown.pick_root` fell back to the whole body, "Just a
    moment…" landed as the article with outcome `ok`, and its `page.html` was
    never fetched again. `capture_leaf` refuses before anything is captured."""
    mod = _module("capture_posts")
    leaf = {"item": POST, "dir": OWN, "title": "The Newest One"}
    directory = tmp_path / "p-the-newest-one--1e31d334"
    directory.mkdir()
    (directory / "page.html").write_text(html, encoding="utf-8")
    (directory / "capture.json").write_text(json.dumps({"body": "page.html"}), encoding="utf-8")  # an older capture
    (directory / "leaf.json").write_text('{"item": "old"}\n', encoding="utf-8")

    row = mod.capture_leaf(directory, leaf, slug="news", newsletter="example-newsletter.invalid")
    assert (row["state"], row["why"]) == ("error", why) and row["detail"]
    assert not (directory / "page.html").exists() and (directory / "page.refused.html").is_file()  # the next run re-fetches
    assert not (directory / "capture.json").exists() and not (directory / "leaf.json").exists()

    plan = {"ticket": "t", "newsletter": "example-newsletter.invalid", "leaves": [leaf], "summary": {}}
    report = _module("capture_posts").build_update(plan, [row], tmp_path)
    assert report["status"] == "failed" and report["captured"] == []
    assert report["missing"] == [{"host": "example-newsletter.invalid", "url": POST, "why": why}]
    if why == "auth":
        assert report["reason"] == "auth_expired:example-newsletter.invalid"


def test_the_paywall_sentence_counts_only_outside_the_content_root():
    mod = _module("capture_posts")
    free = (FIX / "post-the-newest-one.html").read_text(encoding="utf-8")
    quoting = free.replace("<h2>A section about lighthouses</h2>", "<h2>A section</h2><p>You have seen it: “This post is for paid subscribers”.</p><div>nested</div>")
    assert "This post is for paid subscribers" in quoting and not mod.is_paywall_preview(quoting)
    assert mod.is_paywall_preview((FIX / "post-members-only.html").read_text(encoding="utf-8"))
    assert mod.is_paywall_preview("<html><body><h2>This post is for paid subscribers</h2></body></html>")  # no root at all
    # An unclosed <p> or <li> inside the post must not stretch the root over the paywall block after it.
    torn = '<body><div class="available-content"><p>preview<li>x</div><div><h2>This post is for paid subscribers</h2></div></body>'
    assert mod.is_paywall_preview(torn)


# ---- S7 + S15: what is fetched, and when fetching stops ---------------------


def _capture_main(monkeypatch, mod, capture_dir, fetch, *argv):
    """`capture_posts.main` in process with an INJECTED fetcher — no network."""
    slept = []
    code = mod.main(["--capture-dir", str(capture_dir), *argv], sleep=slept.append, fetch=fetch)
    return code, json.loads((capture_dir / "results.json").read_text(encoding="utf-8"))["rows"]


def _refresh_dir(tmp_path, monkeypatch, capsys):
    """A refresh ticket's own, STABLE directory, still holding the last pull."""
    own = tmp_path / OWN
    own.mkdir(parents=True)
    old = (FIX / "post-the-newest-one.html").read_text(encoding="utf-8")
    (own / "page.html").write_text(old.replace("lighthouses", "OLD-BYTES"), encoding="utf-8")
    (own / "leaf.json").write_text(json.dumps({"item": POST, "title": "The Newest One"}), encoding="utf-8")
    (own / "capture.json").write_text(json.dumps({"item": POST, "title": "The Newest One", "body": "page.html"}), encoding="utf-8")
    ticket = _ticket(capture_dir=OWN, refresh=True, resource=POST, item=POST,
                     known=[{"resource": POST, "harvested_at": "2026-09-11T00:00:00Z"}])
    enum = _module("enumerate_archive")
    monkeypatch.setattr(enum, "fetch_page", lambda *a: pytest.fail("a refresh plans exactly its resource: no archive walk"))
    monkeypatch.setattr(enum, "open_ticket", lambda tid, stage=None: ticket)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(own), "--ticket", ticket["ticket"]])
    enum.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["refresh"] is True and [(leaf["item"], leaf["dir"]) for leaf in plan["leaves"]] == [(POST, OWN)]
    return own, old


def test_a_refresh_ticket_really_re_fetches(tmp_path, monkeypatch, capsys):
    """Before: the old `page.html` in the stable leaf dir meant nothing was
    fetched, the old capture read as `on_disk`, the report said ok, and
    `apply` stamped the page `unchanged` on bytes nobody re-read."""
    own, old = _refresh_dir(tmp_path, monkeypatch, capsys)
    mod = _module("capture_posts")
    calls = []

    def fetch(url, *, sleep):
        calls.append(url)
        return old.replace("lighthouses", "REFETCHED-lighthouses")

    code, rows = _capture_main(monkeypatch, mod, own, fetch, "--fetch")
    assert code == 0 and calls == [POST] and [row["state"] for row in rows] == ["captured"]
    landed = (own / "page.html").read_text(encoding="utf-8")
    assert "REFETCHED" in landed and "OLD-BYTES" not in landed
    plan = json.loads((own / "leaves.json").read_text(encoding="utf-8"))
    report = _module("capture_posts").build_update(plan, rows, own.parent)
    # `ok` WITH the capture: `unchanged` is apply's verdict, reached by hashing what was left.
    assert report["status"] == "ok" and [c["dir"] for c in report["captured"]] == [OWN]


def test_a_refresh_that_cannot_reach_the_source_is_never_ok(tmp_path, monkeypatch, capsys):
    import urllib.error
    own, _old = _refresh_dir(tmp_path, monkeypatch, capsys)
    mod = _module("capture_posts")

    def down(url, *, sleep):
        raise urllib.error.URLError("[Errno -2] Name or service not known")

    _code, rows = _capture_main(monkeypatch, mod, own, down, "--fetch")
    assert [(row["state"], row["why"]) for row in rows] == [("error", "error")]
    plan = json.loads((own / "leaves.json").read_text(encoding="utf-8"))
    report = _module("capture_posts").build_update(plan, rows, own.parent)
    assert report["status"] == "failed" and report["captured"] == []  # WAS: states {"on_disk": 1}, outcome ok

    def gone(url, *, sleep):
        raise urllib.error.HTTPError(url, 410, "Gone", None, None)

    _code, rows = _capture_main(monkeypatch, mod, own, gone, "--fetch")
    assert [row["state"] for row in rows] == ["gone"]
    assert _module("capture_posts").build_update(plan, rows, own.parent)["status"] == "gone"
    # …which is a refresh's word alone: the same 410 on an archive pull is a failure.
    assert _module("capture_posts").build_update({**plan, "refresh": False}, rows, own.parent)["status"] == "failed"


def _three_leaves(tmp_path):
    cap = tmp_path / "_raw/news/archive--4fbca949"
    cap.mkdir(parents=True)
    leaves = [{"item": f"{HOST}/p/{n}", "dir": f"_raw/news/p-{n}--0000000{i}", "title": n, "published": None, "audience": "everyone"}
              for i, n in enumerate(("one", "two", "three"))]
    # the third is already fetched: rendering it touches no host
    third = tmp_path / leaves[2]["dir"]
    third.mkdir(parents=True)
    shutil.copy(FIX / "post-the-newest-one.html", third / "page.html")
    (cap / "leaves.json").write_text(json.dumps({"v": 1, "ticket": "t", "slug": "news", "newsletter": "example-newsletter.invalid",
                                                 "leaves": leaves, "summary": {}}), encoding="utf-8")
    return cap, leaves


@pytest.mark.parametrize(("exc", "why", "said"), [
    (lambda url: __import__("urllib.error").error.HTTPError(url, 401, "Unauthorized", None, None), "auth", "auth_expired:example-newsletter.invalid"),
    (lambda url: __import__("urllib.error").error.URLError("Tunnel connection failed: 403 Forbidden"), "denied", "denied:example-newsletter.invalid"),
    (lambda url: __import__("urllib.error").error.HTTPError(url, 429, "Too Many Requests", None, None), "error", "soft block"),
])
def test_fetching_stops_on_a_host_that_said_no(tmp_path, monkeypatch, exc, why, said):
    """agent-loop: on auth expiry fail every remaining item on that domain with
    `auth_expired:<domain>` and STOP fetching it. Before, the loop went on to
    the next leaf — up to 200 of them, each with its own backoff."""
    cap, leaves = _three_leaves(tmp_path)
    mod = _module("capture_posts")
    calls = []

    def fetch(url, *, sleep):
        calls.append(url)
        raise exc(url)

    _code, rows = _capture_main(monkeypatch, mod, cap, fetch, "--fetch")
    assert calls == [leaves[0]["item"]]  # ONE request, not one per leaf
    assert [(row["state"], row["why"]) for row in rows] == [("error", why), ("error", why), ("captured", None)]
    assert said in rows[0]["detail"] and said in rows[1]["detail"] and "not fetched" in rows[1]["detail"]

    report = _module("capture_posts").build_update(json.loads((cap / "leaves.json").read_text(encoding="utf-8")), rows, cap.parent)
    # P-5: a host that said no is a LASTING fact this run — `ok`, with the two
    # posts in `missing[]` and the reason naming what stopped fetching.
    assert report["status"] == "ok" and [m["why"] for m in report["missing"]] == [why, why]
    if why == "auth":
        assert "auth_expired:example-newsletter.invalid" in report["reason"]


def test_one_posts_own_failure_does_not_stop_the_host(tmp_path, monkeypatch):
    import urllib.error
    cap, leaves = _three_leaves(tmp_path)
    mod = _module("capture_posts")
    calls = []

    def fetch(url, *, sleep):
        calls.append(url)
        raise urllib.error.HTTPError(url, 500, "Server Error", None, None)

    _capture_main(monkeypatch, mod, cap, fetch, "--fetch")
    assert calls == [leaves[0]["item"], leaves[1]["item"]]


def test_a_denial_is_raised_at_once_and_a_login_redirect_is_auth(monkeypatch):
    import urllib.error
    mod = _module("capture_posts")
    seen, slept = [], []

    def denied(request, timeout):
        seen.append(request.full_url)
        raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

    monkeypatch.setattr(mod.urllib.request, "urlopen", denied)
    with pytest.raises(urllib.error.URLError):
        mod.fetch_html(POST, sleep=slept.append)
    assert len(seen) == 1 and slept == []  # WAS three tries, 5 s then 10 s apart

    class Redirected:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return "https://substack.com/sign-in?redirect=%2Fp%2Fx"
        def read(self): return b"<html></html>"

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda request, timeout: Redirected())
    with pytest.raises(mod.LoginWall) as caught:
        mod.fetch_html(POST, sleep=slept.append)
    assert mod.why_for(caught.value) == "auth"


# ---- nits -------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["not-json", {"error": "nope"}, ["<html>"]])
def test_an_archive_that_answers_a_challenge_page_is_unloadable_not_a_traceback(monkeypatch, capsys, tmp_path, answer):
    mod = _module("enumerate_archive")
    cap = tmp_path / "_raw/news/archive--4fbca949"
    cap.mkdir(parents=True)
    ticket = _ticket()
    monkeypatch.setattr(mod, "open_ticket", lambda tid, stage=None: ticket)

    def fetch_page(domain, offset, limit):
        return json.loads("<html>Just a moment...</html>") if answer == "not-json" else answer  # raises JSONDecodeError

    monkeypatch.setattr(mod, "fetch_page", fetch_page)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(cap), "--ticket", ticket["ticket"]])
    mod.main()  # WAS: json.JSONDecodeError, uncaught
    plan = json.loads(capsys.readouterr().out)
    assert plan["leaves"] == [] and "did not answer a JSON list" in plan["summary"]["fetch_failed"]
    report = _module("capture_posts").build_update(plan, [], cap.parent)
    assert report["status"] == "failed" and "would not load" in report["reason"]


def test_an_empty_plan_gives_its_true_reason(tmp_path):
    build = _module("capture_posts").build_update
    def reason(**summary):
        report = build({"newsletter": "n.invalid", "access": "free", "leaves": [], "summary": summary}, [], tmp_path)
        assert report["status"] == "ok"
        return report["reason"]
    paid = reason(skipped_paywalled=7, skipped_known=0)
    assert paid.startswith("paywalled:") and "known" not in paid and "7" in paid  # WAS "known: nothing new"
    assert reason(skipped_known=3, skipped_paywalled=2).startswith("known:")
    assert reason(skipped_excluded=2).startswith("excluded:")
    assert reason().startswith("nothing in range")


