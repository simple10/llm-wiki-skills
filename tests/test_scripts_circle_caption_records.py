import importlib.util
from pathlib import Path

import pytest

# Venue capture scripts live in installed skill units at ops/skills/. They
# are WIKI content (never shipped in the plugin repo) — skip when no wiki
# provides one.
SCRIPT = (Path(__file__).resolve().parents[1] / "skills" / "channel-circle" / "scripts"
          / "capture_lesson.py")
if not SCRIPT.exists():
    pytest.skip("circle capture_lesson.py is wiki content (channel-circle "
                "skill) — not present here",
                allow_module_level=True)
spec = importlib.util.spec_from_file_location("capture_lesson", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_caption_records_filenames_and_meta():
    tracks = [
        {"kind": "captions", "label": "Default", "srclang": "en", "text": "WEBVTT\n\n..."},
        {"kind": "captions", "label": "", "srclang": "", "text": "WEBVTT\n\n..."},
        {"kind": "metadata", "label": "thumbnails", "srclang": "", "text": ""},  # ignored
    ]
    recs = mod.caption_records(tracks)
    assert [r["file"] for r in recs] == ["captions/en.vtt", "captions/track-2.vtt"]
    assert recs[0]["lang"] == "en"
    assert all("text" in r for r in recs)  # carries text to write
