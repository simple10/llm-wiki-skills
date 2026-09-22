"""The shared port helpers work against the real CLI: a declared job, a ticket
beside a capture, and the real extractor turning a unit-rendered `page.md` —
with a `frontmatter` object the extractor ignores today — into a staged page."""

import json

from harness import declared_job, extracted, ticket_in


def test_a_unit_rendered_page_md_becomes_a_staged_page(ops, env, wiki):
    job = declared_job(ops, env, wiki, "channel-youtube", "https://www.youtube.com/watch?v=smoke")
    cap = ticket_in(wiki, job, "smoke--00000000", unit="channel-youtube", item="https://www.youtube.com/watch?v=smoke")
    (cap / "page.md").write_text("## Transcript\n\nhello\n", encoding="utf-8")
    (cap / "capture.json").write_text(json.dumps({
        "slug": job.slug, "item": "https://www.youtube.com/watch?v=smoke", "title": "Smoke", "body": "page.md",
        "content_type": "text/markdown", "fetched_at": "2026-09-19T00:00:00Z", "frontmatter": {"type": "video"},
    }), encoding="utf-8")
    (page,) = extracted(ops, env, wiki, cap)
    text = page.read_text(encoding="utf-8")
    assert page.is_relative_to(wiki / job.dest) and "## Transcript" in text and "status: draft" in text
