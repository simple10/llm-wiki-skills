"""`published` as the channel units write it.

These two helpers are the only remaining places in the system that can put a
`published:` line in a note without going through `scaffold.py`, and a
mutation audit found both entirely uncovered: making `youtube_note.yt_date`
pass a non-8-digit value straight through, or making the note emit
`published:` unconditionally, left all 448 ops tests green. An empty
`published:` is precisely the shape the lint rule, the engine's date gate and
scaffold's own emission logic all exist to prevent — so the one place still
able to produce it needs a test.

A unit cannot import the plugin's `scripts/`, so each carries its own
day-precision helper. That is correct (see `published_date`'s module
docstring) and it is exactly why they need testing separately rather than by
inspection of the shared one.
"""
import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[1] / "skills"


@contextlib.contextmanager
def _stubbed(*names: str):
    """Temporarily satisfy third-party imports a unit declares in its PEP 723
    block but that the shared test venv does not carry (this suite reuses the
    search CLI's venv purely for pytest).

    Removes exactly what it inserted. `sys.modules.setdefault` alone LEAKS:
    the stub outlives the test and wins for every later import in the same
    xdist worker — including after someone installs the real package, which
    is the version of this bug that would be hard to find. Only network/IO
    libraries are ever stubbed, never anything the helper under test calls.
    """
    inserted = [n for n in names if n not in sys.modules]
    for name in inserted:
        sys.modules[name] = types.ModuleType(name)
    try:
        yield
    finally:
        for name in inserted:
            sys.modules.pop(name, None)


def _load(unit: str, script: str):
    """Import a unit script as a module."""
    path = CATALOG / unit / "scripts" / script
    spec = importlib.util.spec_from_file_location(f"_{unit}_{script}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- channel-youtube: yt-dlp `upload_date` (YYYYMMDD) -> `published` ------------

@pytest.fixture(scope="module")
def youtube_note():
    return _load("channel-youtube", "youtube_note.py")


def test_yt_date_converts_a_real_upload_date(youtube_note):
    assert youtube_note.yt_date("20260618") == "2026-06-18"


@pytest.mark.parametrize("value", [
    None, "", "2026", "202606", "2026-06-18", "not-a-date", "2026061",
    "202606180", "abcdefgh",
])
def test_yt_date_yields_nothing_for_anything_but_eight_digits(youtube_note, value):
    """Passing an odd-shaped value THROUGH is the dangerous direction: it
    lands in the note looking authoritative and reads as absent everywhere
    downstream."""
    assert youtube_note.yt_date(value) == ""


def test_yt_date_rejects_eight_non_digits(youtube_note):
    assert youtube_note.yt_date("2026-6-18") == ""


# --- channel-spotify: `release_date` -> `published` -----------------------------

@pytest.fixture(scope="module")
def spotify():
    with _stubbed("requests"):
        yield _load("channel-spotify", "spotify.py")


def test_spotify_published_day_accepts_a_full_date(spotify):
    assert spotify._published_day("2019-03-04") == "2019-03-04"
    assert spotify._published_day("  2019-03-04  ") == "2019-03-04"


@pytest.mark.parametrize("value", [None, "", "2019", "2019-03", "whenever"])
def test_spotify_published_day_yields_nothing_below_day_precision(spotify, value):
    """Spotify honours `release_date_precision`, so an album legitimately
    returns a bare year — which names no publication DAY."""
    assert spotify._published_day(value) == ""


@pytest.mark.parametrize("value", ["0000-00-00", "2019-02-30", "2023-02-29",
                                   "2019-13-45"])
def test_spotify_published_day_rejects_an_impossible_calendar_date(spotify, value):
    """Day-SHAPED is not a day. This helper is a WRITER — the value it emits
    is copied into `capture.json` — so it owes the same calendar validation
    every other gate in the system applies."""
    assert spotify._published_day(value) == ""


@pytest.mark.parametrize("value", [20190304, ["2019-03-04"], {"d": 1}, 2019])
def test_spotify_published_day_tolerates_a_non_string(spotify, value):
    """It reads parsed JSON, where a venue can legitimately supply a number —
    which must yield nothing rather than raise."""
    assert spotify._published_day(value) == ""
