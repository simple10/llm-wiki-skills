"""A venue's extraction rules: what to keep, what to drop, what to call it."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "channel-circle" / "scripts" / "to_markdown.py"

PAGE = """<html><head><title>Start Here</title></head><body>
<main id="main-content">
<h2>Lesson 3 — Pricing</h2>
<p>The real lesson content lives here and is what should survive intact.</p>
<div class="course-modules"><a href="/1">Module 1</a><a href="/2">Module 2</a></div>
<div class="cta-workshop">Meet the team in Vegas</div>
<div class="legal-disclaimer">Results are not typical.</div>
</main></body></html>
"""


def convert(tmp_path, *extra):
    html = tmp_path / "page.html"
    html.write_text(PAGE)
    r = subprocess.run(["uv", "run", str(SCRIPT), str(html), "--out", "-",
                        "--selector", "main#main-content", *extra],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout, r.stderr


def test_drop_selectors_remove_chrome_before_conversion(tmp_path):
    md, _ = convert(tmp_path, "--drop-selector", ".course-modules",
                    "--drop-selector", ".cta-workshop",
                    "--drop-selector", ".legal-disclaimer")
    assert "The real lesson content" in md
    for gone in ("Module 1", "Vegas", "not typical"):
        assert gone not in md


def test_a_selector_matching_nothing_says_so(tmp_path):
    _, err = convert(tmp_path, "--drop-selector", ".no-such-block")
    assert "matched nothing" in err


def test_title_selector_beats_the_generic_rule(tmp_path):
    md, _ = convert(tmp_path, "--title-selector", "main#main-content h2")
    assert md.startswith("# Lesson 3 — Pricing")
    assert "Start Here" not in md


def test_the_promoted_heading_is_not_left_duplicated(tmp_path):
    md, _ = convert(tmp_path, "--title-selector", "main#main-content h2")
    assert md.count("Lesson 3 — Pricing") == 1


def test_without_a_title_selector_nothing_changes(tmp_path):
    md, _ = convert(tmp_path)
    assert "The real lesson content" in md
    assert "Module 1" in md          # chrome survives without --drop-selector
