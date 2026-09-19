"""`channel-substack` on the rebuilt pipeline's worker contract.

One ticket captures a newsletter's archive: `enumerate_archive.py` applies the
job's own rules and plans the leaf capture directories, `capture_posts.py`
renders each post to `page.md` + `capture.json` at HARVEST (no unit's process
stage is ever invoked), and `write_report.py` lists every leaf in
`captured[]`. The last case runs what they leave through the REAL extractor.

No network anywhere: the archive API is a fixture (`fixtures/substack/
archive.json`) served through a stubbed `fetch_page`, and post pages are
fixture HTML laid in the leaf directories the way a worker's fetch would.
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
SCRIPTS = ROOT / "skills/channel-substack/scripts"
FIX = Path(__file__).resolve().parent / "fixtures/substack"
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
    `--capture-dir`; everything else comes off `ticket.json`. That is NOT how
    a worker runs it — `llm-wiki-ops run` starts a script at the wiki root
    with the ticket's wiki-relative `capture_dir` — and the cases under "the
    documented way" below drive all three scripts exactly so."""
    mod = _module("enumerate_archive")
    if ticket is not None:
        ticket_dir.mkdir(parents=True, exist_ok=True)
        (ticket_dir / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    archive = json.loads((FIX / "archive.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(mod, "fetch_page", lambda domain, offset, limit: archive[offset:offset + limit])
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(ticket_dir), *argv])
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

    report = _module("write_report").build(plan, [], {"ticket": "t"}, cap.parent)
    assert report["outcome"] == "failed" and "harvest.scope" in report["reason"]


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
        (directory / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(directory)])
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
    assert mod.published_of(html) == "2026-09-10"
    assert mod.author_of(html) == "Ada Example"
    assert mod.published_of("<html><body>no date declared</body></html>") is None  # never a guess
    assert mod.audio_of((FIX / "post-a-podcast-episode.html").read_text(encoding="utf-8")).startswith("https://api.substack.com/")


def test_a_paywall_preview_is_told_from_a_complete_paid_post():
    mod = _module("capture_posts")
    assert mod.is_paywall_preview((FIX / "post-members-only.html").read_text(encoding="utf-8"))
    assert not mod.is_paywall_preview((FIX / "post-the-newest-one.html").read_text(encoding="utf-8"))
    # A licensed session reads the whole of a post that still declares itself paid.
    assert not mod.is_paywall_preview('<script type="application/ld+json">{"isAccessibleForFree":false}</script><p>all of it</p>')


def test_the_body_never_opens_a_second_yaml_block():
    mod = _module("capture_posts")
    body = mod.compose_body("# Tab Title - by Someone\n\n---\n\nReal text.\n", "Real Title", {"published": "2026-09-10", "source": "u"})
    assert body.startswith("# Real Title\n\n- **Published:** 2026-09-10\n- **Source:** u\n\nReal text.")
    assert "Tab Title" not in body and not any(line.strip() == "---" for line in body.splitlines()[:6])


# ---- the report ---------------------------------------------------------


def _land(root, leaf, title="T"):
    directory = root / leaf["dir"].rsplit("/", 1)[-1]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page.md").write_text("# T\n", encoding="utf-8")
    (directory / "capture.json").write_text(json.dumps({"item": leaf["item"], "title": title, "body": "page.md"}), encoding="utf-8")


def test_captured_is_what_is_on_disk_and_the_outcome_follows(tmp_path):
    build = _module("write_report").build
    leaves = [{"item": f"{HOST}/p/a", "dir": "_raw/news/p-a--11111111"}, {"item": f"{HOST}/p/b", "dir": "_raw/news/p-b--22222222"}]
    plan = {"ticket": "abc", "newsletter": "example-newsletter.invalid", "leaves": leaves, "summary": {"truncated": False}}

    nothing = build(plan, [], {}, tmp_path)
    assert nothing["outcome"] == "failed" and nothing["captured"] == []

    _land(tmp_path, leaves[0])
    rows = [{"item": leaves[1]["item"], "state": "paywalled", "why": "auth"}]
    partial = build(plan, rows, {"ticket": "abc"}, tmp_path)
    assert partial["outcome"] == "partial" and "1 of 2" in partial["reason"] and "paywalled" in partial["reason"]
    assert partial["captured"] == [{"item": leaves[0]["item"], "dir": leaves[0]["dir"], "title": "T"}]
    assert partial["missing"] == [{"host": "example-newsletter.invalid", "url": leaves[1]["item"], "why": "auth"}]
    assert (partial["v"], partial["ticket"], partial["written"], partial["discovered"]) == (1, "abc", [], [])

    # A capture record whose body is not there is not a capture.
    (tmp_path / "p-b--22222222").mkdir()
    (tmp_path / "p-b--22222222/capture.json").write_text(json.dumps({"body": "page.md"}), encoding="utf-8")
    assert len(build(plan, [], {}, tmp_path)["captured"]) == 1

    _land(tmp_path, leaves[1])
    assert build(plan, [], {}, tmp_path)["outcome"] == "ok"
    # The whole plan landed, but the plan was not the whole archive.
    capped = build({**plan, "summary": {"truncated": True}}, [], {}, tmp_path)
    assert capped["outcome"] == "partial" and "known[]" in capped["reason"]


def test_an_empty_plan_is_skipped_and_a_dead_session_is_auth_expired(tmp_path):
    build = _module("write_report").build
    empty = build({"newsletter": "n.invalid", "leaves": [], "summary": {"skipped_known": 9}}, [], {"ticket": "t"}, tmp_path)
    assert empty["outcome"] == "skipped" and empty["reason"].startswith("known:")

    leaves = [{"item": f"{HOST}/p/a", "dir": "_raw/news/p-a--11111111"}]
    dead = build({"newsletter": "n.invalid", "leaves": leaves, "summary": {}}, [{"item": leaves[0]["item"], "state": "paywalled", "why": "auth"}], {}, tmp_path)
    assert dead["outcome"] == "failed" and dead["reason"] == "auth_expired:n.invalid"


# ---- page names: one page per post, whatever two posts are called --------


def test_the_page_key_is_the_hosts_filename_rule_plus_what_a_filesystem_folds():
    """`page/note.py::filename_for` is `title.strip() + ".md"` and nothing else:
    outer whitespace is all the HOST folds; case is what a case-insensitive
    filesystem folds under it."""
    mod = _module("write_report")
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
    mod = _module("write_report")
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
        report = mod.build(plan, [], {}, tmp_path)
        assert [c["title"] for c in report["captured"]] == want
        assert [json.loads((tmp_path / leaf["dir"].rsplit("/", 1)[-1] / "capture.json").read_text(encoding="utf-8"))["title"]
                for leaf in leaves[1:]] == want
    # `--only` re-renders one leaf with its plain title; the next report settles it to the SAME name.
    _land(tmp_path, leaves[1], title="Open Thread")
    assert [c["title"] for c in mod.build(plan, [], {}, tmp_path)["captured"]] == want
    # With nothing before it, the first LANDED post keeps its title untouched.
    report = mod.build({**plan, "leaves": leaves[1:]}, [], {}, tmp_path)
    assert [c["title"] for c in report["captured"]][0] == "Open Thread (2026-09-08)"  # already settled: left as it is
    _land(tmp_path, leaves[1], title="Open Thread")
    assert [c["title"] for c in mod.build({**plan, "leaves": leaves[1:]}, [], {}, tmp_path)["captured"]][:2] == ["Open Thread", want[1]]


# ---- end to end, through the real extractor ------------------------------


def _script(name, *argv, cwd=None):
    """A unit script from THIS working tree (the session wiki installs from
    git HEAD), under `uv run` so its PEP 723 dependencies resolve. `cwd` is
    the wiki root when a case drives the script the documented way."""
    return subprocess.run(["uv", "run", "-q", str(SCRIPTS / name), *argv], capture_output=True, text=True, check=False, cwd=cwd)


def test_one_ticket_lands_every_free_post_as_a_staged_page(ops, env, wiki, monkeypatch, capsys):
    if shutil.which("uv") is None:
        pytest.skip("no uv: capture_posts.py carries PEP 723 dependencies")
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
    done = _script("capture_posts.py", "--capture-dir", rel, cwd=wiki)  # no --fetch: no network
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["states"] == {"captured": 2, "paywalled": 1}

    wrote = _script("write_report.py", "--capture-dir", rel, cwd=wiki)
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
    assert record["body"] == "page.md" and record["content_type"] == "text/markdown" and record["slug"] == job.slug
    assert record["frontmatter"] == {"type": "article", "published": "2026-09-10", "author": "Ada Example",
                                     "newsletter": "example-newsletter.invalid", "audience": "everyone", "paywalled": False}

    # `apply` mints one process ticket per captured dir; each is one `pipeline extract`.
    pages = [page for c in report["captured"] for page in extracted(ops, env, wiki, wiki / c["dir"])]
    assert len(pages) == 2 and len(set(pages)) == 2
    assert all(page.is_relative_to(wiki / job.dest) for page in pages)

    newest, podcast = (page.read_text(encoding="utf-8") for page in pages)
    # the archive's calm title, not the clickbait og:title
    assert "title: The Newest One" in newest and "You Won't Believe" not in newest
    assert f"resource: {HOST}/p/the-newest-one" in newest and "status: draft" in newest
    assert "- **Published:** 2026-09-10" in newest and "- **Author:** Ada Example" in newest
    assert "lighthouses" in newest and "## A section about lighthouses" in newest
    for chrome in ("SITE-NAV-CHROME", "SUBSCRIBE-BUTTON-CHROME", "COMMENTS-ARE-NOT-THE-ARTICLE", "FOOTER-CHROME"):
        assert chrome not in newest  # `.available-content` is the content root
    # exactly ONE frontmatter block: the extractor's own
    assert newest.startswith("---\n") and [line for line in newest.splitlines() if line == "---"] == ["---", "---"]
    # the audio sits outside `.available-content`; the facts name it anyway
    assert "- **Audio:** https://api.substack.com/api/v1/audio/upload/abc-123/src" in podcast and "tide tables" in podcast

    # A slice that died before reporting: the next plan re-lists what is on
    # disk without spending the cap on it, and re-fetches nothing.
    again = _plan(monkeypatch, capsys, cap, None, "--max-leaves", "1")
    assert [(leaf["item"].rsplit("/", 1)[-1], leaf["on_disk"]) for leaf in again["leaves"]] == [
        ("the-newest-one", True), ("members-only", False), ("a-podcast-episode", True)]


def test_two_posts_with_one_title_land_as_two_pages(ops, env, wiki):
    """The extractor files a page under its title and overwrites what is there:
    before the report settled titles, a newsletter's second "Open Thread" WAS
    the first one's page, and both process tickets said ok."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: capture_posts.py carries PEP 723 dependencies")
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
    done = _script("capture_posts.py", "--capture-dir", rel, cwd=wiki)
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 2}, done.stderr
    reports = []
    for _ in range(2):  # a respawned worker reports again: same names
        wrote = _script("write_report.py", "--capture-dir", rel, cwd=wiki)
        assert wrote.returncode == 0, wrote.stderr
        reports.append(json.loads((cap / "report.json").read_text(encoding="utf-8")))
    report = reports[-1]
    assert report["outcome"] == "ok"

    pages = [extracted(ops, env, wiki, wiki / c["dir"])[0] for c in report["captured"]]
    assert len({page.resolve() for page in pages}) == 2 and all(page.is_relative_to(wiki / job.dest) for page in pages)
    assert [page.name for page in pages] == ["Open Thread.md", "Open Thread (2026-08-13).md"]
    assert [[c["title"] for c in r["captured"]] for r in reports] == [["Open Thread", "Open Thread (2026-08-13)"]] * 2
    newest, older = (page.read_text(encoding="utf-8") for page in pages)
    assert f"resource: {host}/p/open-thread-2" in newest and "lighthouses" in newest  # still the FIRST post's page
    assert f"resource: {host}/p/open-thread\n" in older and "tide tables" in older


# ---- the documented way: cwd is the WIKI ROOT, the capture dir wiki-relative ----
#
# `llm-wiki-ops run` starts a script with the wiki root as its cwd
# (`commands/run/run.py::_exec`), NOT in the capture directory the worker
# stands in. Every case above this line passes an absolute `--capture-dir`,
# which is how a `.` default got through review: `write_report.py` wrote a
# fabricated `failed` report with a null ticket AT THE WIKI ROOT.

POST = f"{HOST}/p/the-newest-one"
OWN = "_raw/news/p-the-newest-one--1e31d334"
STDLIB = ("enumerate_archive.py", "write_report.py")  # these two carry no dependencies: plain python runs them


def _py(name, *argv, cwd):
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / name), *argv], capture_output=True, text=True, check=False, cwd=cwd)


def _root_with_post_ticket(tmp_path, **over):
    """A stand-in wiki root holding one ticket whose target IS a post — the
    one plan the enumerator makes with no archive call, so a subprocess can
    run it with no network."""
    root = tmp_path / "wiki"
    (root / OWN).mkdir(parents=True)
    ticket = _ticket(item=POST, target=POST, capture_dir=OWN, **over)
    (root / OWN / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    return root


def _litter(root):
    return sorted(path.name for path in root.iterdir() if path.name != "_raw")


def test_no_script_runs_without_a_capture_dir_and_none_writes_at_the_wiki_root(tmp_path):
    """The blocker, as the reviewer hit it through the real `run`: no
    arguments, cwd the wiki root. Each script must REFUSE — and above all
    `write_report.py` must not leave a `report.json` where it stands."""
    root = _root_with_post_ticket(tmp_path)
    for name in STDLIB:
        done = _py(name, cwd=root)
        assert done.returncode == 2 and "--capture-dir" in done.stderr, (name, done.stderr)
    assert "required=True" in (SCRIPTS / "capture_posts.py").read_text(encoding="utf-8").split('"--capture-dir"', 1)[1][:40]
    assert _litter(root) == []

    # Pointed at a directory that is no ticket's (the wiki root itself): refused, nothing written.
    done = _py("write_report.py", "--capture-dir", ".", cwd=root)
    assert done.returncode == 2 and "ticket.json" in done.stderr and _litter(root) == []
    # A capture dir that is not there (an absolute-minded path from the wrong cwd) says what it wants.
    done = _py("enumerate_archive.py", "--capture-dir", "_raw/news/nope", cwd=root)
    assert done.returncode == 2 and "wiki-relative" in done.stderr


def test_the_enumerator_and_the_report_run_from_the_wiki_root_on_relative_paths(tmp_path):
    root = _root_with_post_ticket(tmp_path)
    (root / OWN / "report.json").write_text('{"outcome": "ok", "ticket": "an-older-spawn"}', encoding="utf-8")

    planned = _py("enumerate_archive.py", "--capture-dir", OWN, cwd=root)
    assert planned.returncode == 0, planned.stderr
    plan = json.loads((root / OWN / "leaves.json").read_text(encoding="utf-8"))
    assert [(leaf["item"], leaf["dir"]) for leaf in plan["leaves"]] == [(POST, OWN)]
    # Rule 4: the first thing the flow does is remove a report that is not this run's.
    assert not (root / OWN / "report.json").exists()

    # A hand run's relative `--out` lands INSIDE the capture dir, never at the wiki root.
    hand = root / "_raw/news/hand"
    hand.mkdir()
    out = _py("enumerate_archive.py", POST, "--slug", "news", "--capture-dir", "_raw/news/hand", "--out", "leaves.json", cwd=root)
    assert out.returncode == 0 and (hand / "leaves.json").is_file(), out.stderr

    # Nothing captured: the report says `failed` — IN the capture dir, with the ticket's id.
    wrote = _py("write_report.py", "--capture-dir", OWN, cwd=root)
    report = json.loads((root / OWN / "report.json").read_text(encoding="utf-8"))
    assert wrote.returncode == 1 and (report["ticket"], report["outcome"]) == ("0123456789ab", "failed")
    assert _litter(root) == []

    # A success claimed over nothing on disk is refused (agent-loop: it fails the ticket anyway).
    # …and a refusal never leaves the PREVIOUS run's report standing for `apply` to read.
    (root / OWN / "report.json").write_text('{"v": 1, "ticket": "0123456789ab", "outcome": "ok"}', encoding="utf-8")
    claimed = _py("write_report.py", "--capture-dir", OWN, "--outcome", "unchanged", cwd=root)
    assert claimed.returncode == 2 and not (root / OWN / "report.json").exists()
    (root / OWN / "report.json").write_text('{"v": 1, "ticket": "0123456789ab", "outcome": "ok"}', encoding="utf-8")
    assert _py("write_report.py", "--capture-dir", OWN, "--missing", "nonsense", cwd=root).returncode == 2
    assert not (root / OWN / "report.json").exists()


def test_capture_posts_runs_from_the_wiki_root_on_relative_paths(tmp_path):
    """No ops CLI needed: a stand-in root, the fixture page in the leaf dir, and
    the script under `uv run` with cwd the root and `--capture-dir` relative."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: capture_posts.py carries PEP 723 dependencies")
    root = _root_with_post_ticket(tmp_path)
    assert _py("enumerate_archive.py", "--capture-dir", OWN, cwd=root).returncode == 0
    shutil.copy(FIX / "post-the-newest-one.html", root / OWN / "page.html")

    done = _script("capture_posts.py", "--capture-dir", OWN, cwd=root)
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 1}, done.stderr
    assert (root / OWN / "capture.json").is_file() and (root / OWN / "results.json").is_file()
    # a relative --plan is inside the capture dir too
    again = _script("capture_posts.py", "--capture-dir", OWN, "--plan", "leaves.json", "--only", POST, cwd=root)
    assert again.returncode == 0, again.stderr
    assert _py("write_report.py", "--capture-dir", OWN, cwd=root).returncode == 0
    assert json.loads((root / OWN / "report.json").read_text(encoding="utf-8"))["outcome"] == "ok"
    assert _litter(root) == []


# ---- Rule 1: the title is a legal filename ---------------------------------


def test_safe_title_is_what_the_hosts_filename_rule_will_hold():
    safe = _module("capture_posts").safe_title
    assert safe("Lesson 3: Pricing") == "Lesson 3 - Pricing"
    assert safe('What is "A/B" testing?') == "What is 'A-B' testing"
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


def _hostile_page():
    html = (FIX / "post-the-newest-one.html").read_text(encoding="utf-8")
    return html.replace('name="author" content="Ada Example"', 'name="author" content="Ada Example&#10;&#10;## Forged by the author&#10;&#10;```"')


def test_a_title_the_host_would_refuse_still_lands_and_forges_nothing(ops, env, wiki):
    """Rule 1 + Rule 2, through the REAL extractor. Before the fix the raw
    title went into `capture.json`, harvest said ok, and `pipeline extract`
    refused the process ticket: "a title cannot carry ':'"."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: capture_posts.py carries PEP 723 dependencies")
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

    done = _script("capture_posts.py", "--capture-dir", rel_cap, cwd=wiki)
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 2}, done.stderr + done.stdout
    assert _script("write_report.py", "--capture-dir", rel_cap, cwd=wiki).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    assert [c["title"] for c in report["captured"]] == ["Lesson 3 - What is 'A-B' testing # Forged heading ---",
                                                        "Lesson 3 - What is 'A-B' testing"]

    record = json.loads((wiki / leaves[0]["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert record["title"] == report["captured"][0]["title"]
    assert record["frontmatter"]["source_title"] == '.Lesson 3: What is "A/B" testing? # Forged heading ---'  # the true one, on one line
    assert record["frontmatter"]["author"] == "Ada Example ## Forged by the author ```"
    second = json.loads((wiki / leaves[1]["dir"] / "capture.json").read_text(encoding="utf-8"))
    assert second["frontmatter"]["published"] == "2026-09-10"  # the plan's was no date: the page's own declared one

    pages = [extracted(ops, env, wiki, wiki / c["dir"])[0] for c in report["captured"]]  # the pages LAND
    assert [page.name for page in pages] == [f"{c['title']}.md" for c in report["captured"]]
    text = pages[0].read_text(encoding="utf-8")
    assert '# .Lesson 3: What is "A/B" testing? # Forged heading ---' in text  # the venue's title is the H1
    lines = text.splitlines()
    assert "# Forged heading" not in lines and "## Forged by the author" not in lines and "# Forged date" not in lines
    assert [line for line in lines if line.strip() == "---"] == ["---", "---"]  # the extractor's own block only
    assert not any(line.startswith("```") for line in lines)  # no fence opened over the rest of the page
    assert "- **Author:** Ada Example ## Forged by the author" in text and "lighthouses" in text


def test_a_hundred_cjk_characters_land_and_so_does_their_namesake(ops, env, wiki):
    """The host's `filename_for` checks no LENGTH: 100 CJK characters are 300
    bytes and the real extractor died `OSError: File name too long`. The cap is
    held in UTF-8 bytes — and the report's de-dup qualifier, added AFTER it,
    still fits: this unit's qualifiers are a date, a hash8 and a counter."""
    if shutil.which("uv") is None:
        pytest.skip("no uv: capture_posts.py carries PEP 723 dependencies")
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
    assert _script("capture_posts.py", "--capture-dir", rel_cap, cwd=wiki).returncode == 0
    assert _script("write_report.py", "--capture-dir", rel_cap, cwd=wiki).returncode == 0
    report = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    first, second = (c["title"] for c in report["captured"])
    assert first.endswith("…") and len(first.encode()) <= 203 and second == f"{first} (2026-09-03)"
    pages = [extracted(ops, env, wiki, wiki / c["dir"])[0] for c in report["captured"]]
    assert [page.name for page in pages] == [f"{first}.md", f"{second}.md"] and all(page.is_file() for page in pages)
    assert "# " + "語" * 100 in pages[0].read_text(encoding="utf-8")  # the whole title is still the H1


def test_no_qualifier_this_unit_adds_can_push_a_capped_title_past_a_filename():
    """Worst case, by arithmetic and by the functions themselves: the longest
    safe title, a namesake on another day, then the counter."""
    capture, report = _module("capture_posts"), _module("write_report")
    title = capture.safe_title("語" * 300)
    taken = {}
    names = [report.unique_title(title, ["2026-09-08", "aaaaaaaa"], taken) for _ in range(12)]
    names += [report.unique_title(title, ["2026-09-08"], taken) for _ in range(120)]
    assert len(set(names)) == len(names)
    assert max(len(f"{name}.md".encode()) for name in names) <= 255


def test_the_body_top_is_one_line_per_fact_whatever_the_venue_says():
    mod = _module("capture_posts")
    body = mod.compose_body("Text.\n", "A title\n\n# Forged\n---", {"author": "A\n## B\n```", "published": "2026-09-10", "source": "u"})
    assert body.splitlines()[:5] == ["# A title # Forged ---", "", "- **Published:** 2026-09-10", "- **Author:** A ## B ```", "- **Source:** u"]
    assert mod.valid_day("2026-09-10") == "2026-09-10"
    assert mod.valid_day("2026-09-10T00:00") is None and mod.valid_day("2026-09-10\n# x") is None and mod.valid_day(None) is None
    assert mod.audio_of('<audio src="javascript:alert(1)">') is None
    assert mod.audio_of('<audio src="https://api.substack.com/a b">') is None


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
    never fetched again. `capture_leaf` refuses BEFORE converting — which is
    also why this runs with no converter dependencies installed."""
    mod = _module("capture_posts")
    leaf = {"item": POST, "dir": OWN, "title": "The Newest One"}
    directory = tmp_path / "p-the-newest-one--1e31d334"
    directory.mkdir()
    (directory / "page.html").write_text(html, encoding="utf-8")
    (directory / "capture.json").write_text(json.dumps({"body": "page.md"}), encoding="utf-8")  # an older capture
    (directory / "page.md").write_text("old\n", encoding="utf-8")

    row = mod.capture_leaf(directory, leaf, slug="news", newsletter="example-newsletter.invalid")
    assert (row["state"], row["why"]) == ("error", why) and row["detail"]
    assert not (directory / "page.html").exists() and (directory / "page.refused.html").is_file()  # the next run re-fetches
    assert not (directory / "capture.json").exists() and not (directory / "page.md").exists()

    plan = {"ticket": "t", "newsletter": "example-newsletter.invalid", "leaves": [leaf], "summary": {}}
    report = _module("write_report").build(plan, [row], {"ticket": "t"}, tmp_path)
    assert report["outcome"] == "failed" and report["captured"] == []
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
    """`capture_posts.main` in process with an INJECTED fetcher — no network —
    and the converter stubbed (its dependencies are not in the test env; the
    real one runs in the `uv` cases)."""
    def render(html_path, out_path, base_url, drop_selectors):
        out_path.write_text("# tab title\n\n" + re.sub(r"<[^>]+>", " ", html_path.read_text(encoding="utf-8")) + "\n", encoding="utf-8")
    monkeypatch.setattr(mod, "render", render)
    slept = []
    code = mod.main(["--capture-dir", str(capture_dir), *argv], sleep=slept.append, fetch=fetch)
    return code, json.loads((capture_dir / "results.json").read_text(encoding="utf-8"))["rows"]


def _refresh_dir(tmp_path, monkeypatch, capsys):
    """A refresh ticket's own, STABLE directory, still holding the last pull."""
    own = tmp_path / OWN
    own.mkdir(parents=True)
    old = (FIX / "post-the-newest-one.html").read_text(encoding="utf-8")
    (own / "page.html").write_text(old, encoding="utf-8")
    (own / "page.md").write_text("# The Newest One\n\nOLD BYTES\n", encoding="utf-8")
    (own / "capture.json").write_text(json.dumps({"item": POST, "title": "The Newest One", "body": "page.md"}), encoding="utf-8")
    (own / "report.json").write_text(json.dumps({"outcome": "ok"}), encoding="utf-8")
    ticket = _ticket(capture_dir=OWN, refresh=True, resource=POST, item=POST,
                     known=[{"resource": POST, "harvested_at": "2026-09-11T00:00:00Z"}])
    enum = _module("enumerate_archive")
    monkeypatch.setattr(enum, "fetch_page", lambda *a: pytest.fail("a refresh plans exactly its resource: no archive walk"))
    (own / "ticket.json").write_text(json.dumps(ticket), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(own)])
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
    assert "REFETCHED" in (own / "page.html").read_text(encoding="utf-8")
    body = (own / "page.md").read_text(encoding="utf-8")
    assert "REFETCHED" in body and "OLD BYTES" not in body
    plan = json.loads((own / "leaves.json").read_text(encoding="utf-8"))
    report = _module("write_report").build(plan, rows, {"ticket": "t"}, own.parent)
    # `ok` WITH the capture: `unchanged` is apply's verdict, reached by hashing what was left.
    assert report["outcome"] == "ok" and [c["dir"] for c in report["captured"]] == [OWN]


def test_a_refresh_that_cannot_reach_the_source_is_never_ok(tmp_path, monkeypatch, capsys):
    import urllib.error
    own, _old = _refresh_dir(tmp_path, monkeypatch, capsys)
    mod = _module("capture_posts")

    def down(url, *, sleep):
        raise urllib.error.URLError("[Errno -2] Name or service not known")

    _code, rows = _capture_main(monkeypatch, mod, own, down, "--fetch")
    assert [(row["state"], row["why"]) for row in rows] == [("error", "error")]
    plan = json.loads((own / "leaves.json").read_text(encoding="utf-8"))
    report = _module("write_report").build(plan, rows, {"ticket": "t"}, own.parent)
    assert report["outcome"] == "failed" and report["captured"] == []  # WAS: states {"on_disk": 1}, outcome ok

    def gone(url, *, sleep):
        raise urllib.error.HTTPError(url, 410, "Gone", None, None)

    _code, rows = _capture_main(monkeypatch, mod, own, gone, "--fetch")
    assert [row["state"] for row in rows] == ["gone"]
    assert _module("write_report").build(plan, rows, {"ticket": "t"}, own.parent)["outcome"] == "gone"
    # …which is a refresh's word alone: the same 410 on an archive pull is a failure.
    assert _module("write_report").build({**plan, "refresh": False}, rows, {"ticket": "t"}, own.parent)["outcome"] == "failed"


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

    report = _module("write_report").build(json.loads((cap / "leaves.json").read_text(encoding="utf-8")), rows, {"ticket": "t"}, cap.parent)
    assert report["outcome"] == "partial" and [m["why"] for m in report["missing"]] == [why, why]
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
    (cap / "ticket.json").write_text(json.dumps(_ticket()), encoding="utf-8")

    def fetch_page(domain, offset, limit):
        return json.loads("<html>Just a moment...</html>") if answer == "not-json" else answer  # raises JSONDecodeError

    monkeypatch.setattr(mod, "fetch_page", fetch_page)
    monkeypatch.setattr(sys, "argv", ["enumerate_archive.py", "--capture-dir", str(cap)])
    mod.main()  # WAS: json.JSONDecodeError, uncaught
    plan = json.loads(capsys.readouterr().out)
    assert plan["leaves"] == [] and "did not answer a JSON list" in plan["summary"]["fetch_failed"]
    report = _module("write_report").build(plan, [], {"ticket": "t"}, cap.parent)
    assert report["outcome"] == "failed" and "would not load" in report["reason"]


def test_an_empty_plan_gives_its_true_reason(tmp_path):
    build = _module("write_report").build
    def reason(**summary):
        report = build({"newsletter": "n.invalid", "access": "free", "leaves": [], "summary": summary}, [], {"ticket": "t"}, tmp_path)
        assert report["outcome"] == "skipped"
        return report["reason"]
    paid = reason(skipped_paywalled=7, skipped_known=0)
    assert paid.startswith("paywalled:") and "known" not in paid and "7" in paid  # WAS "known: nothing new"
    assert reason(skipped_known=3, skipped_paywalled=2).startswith("known:")
    assert reason(skipped_excluded=2).startswith("excluded:")
    assert reason().startswith("nothing in range")
