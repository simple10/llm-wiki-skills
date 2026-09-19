import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "channel-circle" / "scripts" / "to_markdown.py"
FIX = Path(__file__).resolve().parent / "fixtures" / "media_page.html"


def run(tmp_path):
    out = tmp_path / "page.md"
    subprocess.run(
        ["uv", "run", str(SCRIPT), str(FIX), "--out", str(out),
         "--base-url", "https://accelerator.example.com/lesson"],
        check=True, capture_output=True, text=True)
    return out.read_text()


def test_media_placeholders_in_order(tmp_path):
    md = run(tmp_path)
    assert "<!-- media:video:1 -->" in md
    assert "<!-- media:audio:1 -->" in md
    assert "<!-- media:embed:1 -->" in md
    # positional: video placeholder sits between intro and the between-text
    assert md.index("Intro paragraph") < md.index("<!-- media:video:1 -->")
    assert md.index("<!-- media:video:1 -->") < md.index("Text between")
    # non-player iframe is dropped, not tokenized
    assert "ads.example.com" not in md
    assert "<!-- media:embed:2 -->" not in md


FIX_CUSTOM = Path(__file__).resolve().parent / "fixtures" / "custom_player.html"


def test_custom_player_element_gets_video_placeholder(tmp_path):
    out = tmp_path / "page.md"
    subprocess.run(
        ["uv", "run", str(SCRIPT), str(FIX_CUSTOM), "--out", str(out),
         "--base-url", "https://accelerator.example.com/lesson"],
        check=True, capture_output=True, text=True)
    md = out.read_text()
    # the <hls-video> web component is recognized as video and tokenized in place
    assert "<!-- media:video:1 -->" in md
    assert md.index("intro paragraph") < md.index("<!-- media:video:1 -->")
    assert md.index("<!-- media:video:1 -->") < md.index("closing paragraph")
    # the raw custom tag name must not leak into the markdown
    assert "hls-video" not in md
