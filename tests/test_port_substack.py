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
    """The enumerator over the fixture archive, run the way a worker runs it:
    no arguments but the directory it stands in, everything off `ticket.json`."""
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


def test_exclude_urls_matches_exact_prefix_and_glob():
    mod = _module("enumerate_archive")
    url = f"{HOST}/p/sponsored-roundup"
    assert mod.excluded(url, [url + "/"])
    assert mod.excluded(url, [f"{HOST}/p/sponsored"])
    assert mod.excluded(url, ["*/p/sponsored-*"])
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


def _script(name, *argv):
    """A unit script from THIS working tree (the session wiki installs from
    git HEAD), under `uv run` so its PEP 723 dependencies resolve."""
    return subprocess.run(["uv", "run", "-q", str(SCRIPTS / name), *argv], capture_output=True, text=True, check=False)


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

    done = _script("capture_posts.py", "--capture-dir", str(cap))  # no --fetch: no network
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["states"] == {"captured": 2, "paywalled": 1}

    wrote = _script("write_report.py", "--capture-dir", str(cap))
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

    done = _script("capture_posts.py", "--capture-dir", str(cap))
    assert done.returncode == 0 and json.loads(done.stdout)["states"] == {"captured": 2}, done.stderr
    reports = []
    for _ in range(2):  # a respawned worker reports again: same names
        wrote = _script("write_report.py", "--capture-dir", str(cap))
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
