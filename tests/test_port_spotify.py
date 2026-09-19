"""channel-spotify on the rebuilt worker contract.

One ticket is one Spotify entity; `spotify.py capture` reads `ticket.json`
beside it and leaves `page.md` + `capture.json` (with the entity's facts under
`frontmatter`), `spotify.py report` leaves `report.json` last, and the REAL
generic extractor turns that capture into a staged page. Nothing here reaches
Spotify, iTunes or a feed: entities are fixtures, and the open-feed lookup is
either replaced in-process or switched off with `--no-audio`.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import declared_job, extracted, ticket_in

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "channel-spotify" / "scripts" / "spotify.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "spotify"
PLAYLIST_URL = "https://open.spotify.com/playlist/4rprjH5cIR72vskqa6RhpC"
EPISODE_URL = "https://open.spotify.com/episode/ep0000000000000000001"
ENCLOSURE = "https://cdn.feed.example/part-1.mp3"
FEED = "https://feed.example/rss"
RESERVED = {"status", "document_id", "document_revision", "harvested", "extracted", "title", "resource"}


def entity(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def spotify(monkeypatch):
    """The script imported in-process with `requests` stubbed (the test venv
    does not carry it), and the two feed lookups answered from memory."""
    inserted = "requests" not in sys.modules
    if inserted:
        sys.modules["requests"] = types.ModuleType("requests")
    try:
        spec = importlib.util.spec_from_file_location("_port_spotify", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if inserted:
            sys.modules.pop("requests", None)
    monkeypatch.setattr(mod, "itunes_feed_candidates", lambda show, limit=5: [{"show": show, "feed": FEED, "artist": "x"}])
    monkeypatch.setattr(
        mod,
        "_parse_rss",
        lambda feed: [{"title": "Part 1: Offers | Fixture Audiobook", "pub_date": "", "duration_s": 3605, "enclosure": ENCLOSURE, "bytes": 1}],
    )
    return mod


def cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """The script as a worker runs it — a real process, `requests` satisfied
    by an empty stand-in because no network call is ever made."""
    stub = tmp_path / "stub-site"
    stub.mkdir(exist_ok=True)
    (stub / "requests.py").write_text("", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env={"PYTHONPATH": str(stub), "PATH": "/usr/bin:/bin"}
    )


def ticket(cap: Path, **over) -> dict:
    body = {
        "v": 1, "ticket": "0123456789ab", "unit": "channel-spotify", "slug": "money-models", "item": PLAYLIST_URL,
        "target": PLAYLIST_URL, "capture_dir": "_raw/money-models/playlist--deadbeef", "dest": None, "hosts": [],
        "harvest": {"scope": "page", "access": "free", "assets": "download"}, "options": {}, "credential": None,
        "min_date": None, "known": [],
    }
    body.update(over)
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "ticket.json").write_text(json.dumps(body), encoding="utf-8")
    return body


def read(cap: Path, name: str):
    return json.loads((cap / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------ the pure writers


def test_plan_routes_each_item_and_builds_the_manifest(spotify):
    meta, assets = spotify.plan_capture(entity("playlist"))
    assert [i["audio"]["route"] for i in meta["items"]] == ["rss", "none", "drm"]
    assert meta["counts"] == {"items": 3, "audio_resolved": 1, "drm_or_unmatched": 2}
    assert [(a["type"], a["src_url"], a["status"]) for a in assets] == [
        ("image", "https://i.scdn.co/image/fixturecover", "pending"),
        ("audio", ENCLOSURE, "pending"),
    ]
    assert {r["status"] for r in meta["drm_refs"]} == {"drm_protected"} and meta["unreachable"] == []


def test_plan_does_not_mutate_the_entity_it_was_given(spotify):
    ent = entity("playlist")
    spotify.plan_capture(ent, min_date="2026-06-15")
    assert ent == entity("playlist")


def test_min_date_drops_items_dated_below_the_floor(spotify):
    meta, _ = spotify.plan_capture(entity("playlist"), min_date="2026-06-15", no_audio=True)
    # 2026-06-01 goes; "1979" sorts below the floor and goes too; 2026-07-01 stays.
    assert [i["n"] for i in meta["items"]] == [1]


def test_no_audio_never_asks_for_a_feed(spotify, monkeypatch):
    monkeypatch.setattr(spotify, "itunes_feed_candidates", lambda *a, **k: pytest.fail("looked up a feed"))
    meta, assets = spotify.plan_capture(entity("playlist"), no_audio=True)
    assert [a["type"] for a in assets] == ["image"] and meta["counts"]["audio_resolved"] == 0


def test_an_unreachable_feed_is_recorded_not_swallowed(spotify, monkeypatch):
    def refused(feed):
        raise OSError("Tunnel connection failed: 403 Forbidden — feed.example is not in the allowlist")

    monkeypatch.setattr(spotify, "_parse_rss", refused)
    meta, _ = spotify.plan_capture(entity("playlist"))
    assert meta["unreachable"] == [{"host": "feed.example", "url": FEED, "why": "denied"}]
    assert meta["counts"]["audio_resolved"] == 0


@pytest.mark.parametrize(
    "said, why",
    [
        ("Tunnel connection failed: 403", "denied"),
        ("host is not in the allowlist", "denied"),
        ("HTTPSConnectionPool: Read timed out.", "timeout"),
        ("403 Client Error: Forbidden", "auth"),
        ("server returned HTML instead of media (content-type 'text/html') — auth/paywall?", "auth"),
        ("Name or service not known", "error"),
        (None, "error"),
    ],
)
def test_why_is_one_of_the_reports_four_words(spotify, said, why):
    assert spotify.why_for(said) == why


def test_frontmatter_is_flat_exact_and_owns_nothing_reserved(spotify):
    meta, _ = spotify.plan_capture(entity("episode"))
    facts = spotify.capture_frontmatter(meta)
    assert facts == {
        "type": "episode", "venue": "spotify", "spotify_id": "ep0000000000000000001", "author": "Fixture Media LLC",
        "show": "The Fixture Show", "published": "2026-07-01", "items": 1, "audio_resolved": 1, "drm_or_unmatched": 0,
        "keyless": False, "market": "US",
    }
    assert not RESERVED & set(facts)
    assert all(isinstance(v, (str, int, bool)) for v in facts.values())


def test_a_date_below_day_precision_is_no_published_at_all(spotify):
    ent = {**entity("episode"), "release_date": "1979"}
    meta, _ = spotify.plan_capture(ent, no_audio=True)
    assert "published" not in spotify.capture_frontmatter(meta)
    assert "- published:" not in spotify.render_page_md(meta)


def test_the_page_body_carries_the_facts_and_never_opens_a_yaml_block(spotify):
    meta, _ = spotify.plan_capture(entity("playlist"))
    page = spotify.render_page_md(meta)
    assert page.startswith("## Fixture Money Models\n")
    assert not page.lstrip().startswith("---")
    for line in ("- type: playlist", "- author: Fixture Curator", "- items: 3", "- audio_resolved: 1", "- keyless: false"):
        assert line in page, line
    assert "Part 1: Offers \\| Fixture Audiobook" in page  # the pipe cannot break the table
    assert f"[open RSS feed]({ENCLOSURE})" in page and "DRM — listen at source" in page and "no open feed match" in page


def test_capture_record_is_text_where_the_extractor_demands_text(spotify, tmp_path):
    meta, assets = spotify.plan_capture(entity("playlist"))
    record = spotify.write_capture_dir(tmp_path / "cap", meta, assets, slug="money-models", item=PLAYLIST_URL + "?si=abc")
    assert record == read(tmp_path / "cap", "capture.json")
    assert record["body"] == "page.md" and record["content_type"] == "text/markdown"
    assert record["item"] == PLAYLIST_URL + "?si=abc"  # the ticket's item verbatim: it is what `known[]` matches on
    assert record["title"] == "Fixture Money Models" and record["slug"] == "money-models"
    assert all(isinstance(record[k], (str, type(None))) for k in ("slug", "item", "title", "body", "content_type", "fetched_at"))
    assert record["fetched_at"].endswith("Z") and isinstance(record["frontmatter"], dict)
    assert sorted(p.name for p in (tmp_path / "cap").iterdir()) == ["assets.json", "capture.json", "items.json", "meta.json", "page.md"]


def test_a_nameless_entity_still_gets_a_text_title(spotify):
    meta, _ = spotify.plan_capture({**entity("episode"), "name": None}, no_audio=True)
    assert spotify.capture_record(meta, slug="s", item=None)["title"] == "Spotify episode ep0000000000000000001"
    assert spotify.capture_record(meta, slug="s", item=None)["item"] == EPISODE_URL


# ------------------------------------------------------------------- the report


def test_report_names_the_tickets_capture_dir(spotify, tmp_path):
    cap = tmp_path / "cap"
    t = ticket(cap)
    meta, assets = spotify.plan_capture(entity("playlist"))
    spotify.write_capture_dir(cap, meta, assets, slug=t["slug"], item=t["item"])
    report = spotify.build_report(cap, t)
    assert report == {
        "v": 1, "ticket": "0123456789ab", "outcome": "ok", "reason": None,
        "captured": [{"item": PLAYLIST_URL, "dir": "_raw/money-models/playlist--deadbeef", "title": "Fixture Money Models"}],
        "written": [], "missing": [], "discovered": [],
    }


def test_report_is_partial_when_an_asset_failed_and_says_which(spotify, tmp_path):
    cap = tmp_path / "cap"
    t = ticket(cap)
    meta, assets = spotify.plan_capture(entity("playlist"))
    spotify.write_capture_dir(cap, meta, assets, slug=t["slug"], item=t["item"])
    assets[1].update(status="failed", error="Tunnel connection failed: 403 Forbidden")
    (cap / "assets.json").write_text(json.dumps(assets), encoding="utf-8")
    report = spotify.build_report(cap, t)
    assert report["outcome"] == "partial" and report["captured"]
    assert report["missing"] == [{"host": "cdn.feed.example", "url": ENCLOSURE, "why": "denied"}]


def test_report_is_partial_for_a_keyless_capture(spotify, tmp_path):
    cap = tmp_path / "cap"
    t = ticket(cap)
    meta, assets = spotify.plan_capture({**entity("playlist"), "keyless": True}, no_audio=True)
    spotify.write_capture_dir(cap, meta, assets, slug=t["slug"], item=t["item"])
    report = spotify.build_report(cap, t)
    assert report["outcome"] == "partial" and "truncated" in report["reason"]
    assert "> [!warning] Keyless capture" in (cap / "page.md").read_text(encoding="utf-8")


def test_report_with_no_capture_is_failed(spotify, tmp_path):
    cap = tmp_path / "cap"
    report = spotify.build_report(cap, ticket(cap))
    assert report["outcome"] == "failed" and report["captured"] == [] and report["reason"]


# ------------------------------------------------------- the script as a process


def test_capture_reads_everything_off_the_ticket(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap, min_date="2026-06-15", harvest={"scope": "page", "access": "free", "assets": "download-audio"})
    r = cli(tmp_path, "capture", "--capture-dir", str(cap), "--entity-json", str(FIXTURES / "playlist.json"), "--no-audio")
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert summary["assets"] == "download-audio" and summary["assets_args"] == ["--skip-types", "image"]
    assert summary["items"] == 1  # the ticket's min_date was applied
    record = read(cap, "capture.json")
    assert (record["slug"], record["item"]) == ("money-models", PLAYLIST_URL)
    r = cli(tmp_path, "report", "--capture-dir", str(cap))
    assert r.returncode == 0, r.stderr
    assert read(cap, "report.json")["outcome"] == "ok"


def test_flags_override_the_ticket(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap, min_date="2026-06-15")
    r = cli(
        tmp_path, "capture", PLAYLIST_URL, "--capture-dir", str(cap), "--entity-json", str(FIXTURES / "playlist.json"),
        "--no-audio", "--min-date", "1900-01-01", "--slug", "by-hand", "--assets", "reference",
    )
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert summary["items"] == 3 and summary["assets_args"] == ["--mode", "reference"]
    assert read(cap, "capture.json")["slug"] == "by-hand"


def test_a_known_entity_is_skipped_without_a_fetch(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap, known=[{"resource": PLAYLIST_URL, "harvested_at": "2026-09-01T00:00:00Z"}])
    r = cli(tmp_path, "capture", "--capture-dir", str(cap))  # no entity, no network: it must not need either
    assert r.returncode == 0, r.stderr
    report = read(cap, "report.json")
    assert report["outcome"] == "skipped" and report["reason"] == f"known: {PLAYLIST_URL}" and report["captured"] == []
    assert not (cap / "capture.json").exists()


def test_a_refresh_ticket_recaptures_a_known_entity(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap, known=[{"resource": PLAYLIST_URL, "harvested_at": "x"}], refresh=True, resource=PLAYLIST_URL)
    r = cli(tmp_path, "capture", "--capture-dir", str(cap), "--entity-json", str(FIXTURES / "playlist.json"), "--no-audio")
    assert r.returncode == 0, r.stderr
    assert (cap / "capture.json").exists() and not (cap / "report.json").exists()


def test_an_entity_file_for_another_entity_is_refused(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap)
    r = cli(tmp_path, "capture", "--capture-dir", str(cap), "--entity-json", str(FIXTURES / "episode.json"), "--no-audio")
    assert r.returncode == 2 and not (cap / "capture.json").exists()


def test_report_with_no_ticket_anywhere_is_refused(tmp_path):
    (tmp_path / "cap").mkdir()
    r = cli(tmp_path, "report", "--capture-dir", str(tmp_path / "cap"))
    assert r.returncode == 2 and "ticket" in r.stderr


def test_a_failed_report_exits_nonzero_and_still_lands(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap)
    r = cli(tmp_path, "report", "--capture-dir", str(cap), "--missing", "https://api.spotify.com/v1/playlists/x=denied")
    assert r.returncode == 1
    report = read(cap, "report.json")
    assert report["outcome"] == "failed"
    assert report["missing"] == [{"host": "api.spotify.com", "url": "https://api.spotify.com/v1/playlists/x", "why": "denied"}]


# ------------------------------------------------- END TO END, the real extractor


def test_a_spotify_capture_becomes_a_staged_page(ops, env, wiki, spotify, tmp_path, capsys):
    job = declared_job(ops, env, wiki, "channel-spotify", PLAYLIST_URL)
    cap = ticket_in(wiki, job, "playlist-4rprjh5cir72vskqa6rhpc--00000000", unit="channel-spotify", item=PLAYLIST_URL)
    assert job.record["harvest"]["assets"] == "download"  # the unit's own watch default reached the job

    # The worker's first step, in-process so the feed lookup is the fixture's.
    spotify.cmd_capture(
        types.SimpleNamespace(
            url=None, capture_dir=str(cap), slug=None, market="US", min_date=None, assets=None, keyless=False,
            no_audio=False, entity_json=str(FIXTURES / "playlist.json"),
        )
    )
    # …and its last, as the process a worker really runs. Read before the
    # extractor runs: `pipeline extract` leaves its own report in this directory.
    r = cli(tmp_path, "report", "--capture-dir", str(cap))
    assert r.returncode == 0, r.stderr
    report = read(cap, "report.json")
    assert report["outcome"] == "ok"
    assert report["captured"] == [{"item": PLAYLIST_URL, "dir": f"_raw/{job.slug}/{cap.name}", "title": "Fixture Money Models"}]

    (page,) = extracted(ops, env, wiki, cap)
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest)
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    # The extractor's own frontmatter, and only its own.
    assert "title: Fixture Money Models" in head and "status: draft" in head and PLAYLIST_URL in head
    # The unit's body survived verbatim: heading, facts, item table with every audio route.
    assert "## Fixture Money Models" in body and "By **Fixture Curator** — Spotify playlist:" in body
    for line in ("- type: playlist", "- venue: spotify", "- items: 3", "- audio_resolved: 1", "- drm_or_unmatched: 2", "- keyless: false"):
        assert line in body, line
    assert "| 1 | Part 1: Offers \\| Fixture Audiobook | 1:00:00 | 2026-07-01 |" in body
    assert f"[open RSS feed]({ENCLOSURE})" in body and "DRM — listen at source" in body
    # One frontmatter block. The description's own `---` is a rule mid-body, not a second block.
    assert text.startswith("---\n") and not body.lstrip().startswith("---")
    assert "\ntype: playlist" not in head  # today the extractor ignores `frontmatter`; the facts ride the body


def test_a_respawn_does_not_report_the_last_attempts_capture(tmp_path):
    cap = tmp_path / "cap"
    ticket(cap)
    ok = cli(tmp_path, "capture", "--capture-dir", str(cap), "--entity-json", str(FIXTURES / "playlist.json"), "--no-audio")
    assert ok.returncode == 0 and (cap / "capture.json").exists()
    died = cli(tmp_path, "capture", "--capture-dir", str(cap), "--entity-json", str(tmp_path / "absent.json"))
    assert died.returncode == 2 and not (cap / "capture.json").exists()
    assert cli(tmp_path, "report", "--capture-dir", str(cap)).returncode == 1
    assert read(cap, "report.json")["outcome"] == "failed"
