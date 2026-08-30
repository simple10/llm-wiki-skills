# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44", "httpx>=0.27"]
# ///
"""Capture a single Frame.io share asset (video or document) into a raw dir.

platform: frameio
scope: platform-general (any next.frame.io/share/<id>/view/<asset-id> URL).
No hardcoded share ids or asset ids.

Frame.io's guest viewer streams video over HLS (a `sahls.frame.io/encode-hls/
.../<jwt-token>/main.m3u8` master playlist, requested once the player mounts)
and renders documents (pdf/pptx/mht) via a proxy conversion
(`assets.frame.io/pdf/<id>/pdf_proxy.pdf`, or similar `_proxy.<ext>` routes for
other office formats) — both are signed URLs visible only in the network log,
never the DOM. This script opens the view page, watches network traffic long
enough for the relevant request to fire, then:
  - video: hands the master m3u8 straight to yt-dlp (mux'd mp4 out) —
    yt-dlp must be on PATH
  - document: streams the proxy URL down with httpx (same signed-CDN pattern
    as the video asset's low-res proxy — no auth needed beyond the URL itself)

Does not write capture.json or handle stage-2 handoff — the caller wraps this
per-item with metadata (path, tags, watch_id) into the pipeline's job/capture
schema (capture_job.py does exactly that).

Usage:
  uv run capture_asset.py <view-url> --out <dir> --name <asset-name>
         [--timeout-ms 20000]

Outputs into <dir>/:
  - video.mp4   (video assets)
  - document.<ext>  (document assets; the original filename is recorded in
                     meta.json's "name" — the note builder restores it)
  - meta.json   title, final_url, kind, resolved asset URL, bytes

Exit 0 on success, 2 if no video/doc URL was ever observed (page didn't
render the expected player/viewer — usually means Frame.io changed the SPA
and this script needs updating), 3 on yt-dlp/download failure.

History:
  2026-07-14  created — first Frame.io share harvest.
  2026-07-29  packaged into the channel-frameio skill unit.
  2026-08-18  the document extension falls back to the proxy URL's own
              `_proxy.<ext>` when no --name is passed: the per-job
              capture path has only the job's URL, and every document
              landed as `document.bin` without it.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HLS_MASTER_RE = re.compile(r"sahls\.frame\.io/encode-hls/[^\"'\s]+/main\.m3u8")
DOC_PROXY_RE = re.compile(r"assets\.frame\.io/\w+/[^\"'\s]+_proxy\.\w+\?[^\"'\s]+")
DOC_PROXY_EXT_RE = re.compile(r"_proxy\.(\w+)\?")


def document_ext(name, doc_url):
    """The extension a downloaded document lands with.

    `--name` wins where it is passed: it is the asset's ORIGINAL filename,
    off the share's leaf manifest, and the proxy route names only whatever
    Frame.io converted the asset to. But a dispatched leaf job carries a URL
    and nothing else, so on the per-job capture path there is no name
    — and the signed conversion route spells the extension itself
    (`.../<kind>_proxy.<ext>?<signature>`). Without that fallback every
    document on that path landed as `document.bin`.
    """
    if name and "." in name:
        return name.rsplit(".", 1)[-1].lower()
    m = DOC_PROXY_EXT_RE.search(doc_url)
    return m.group(1).lower() if m else "bin"


def main() -> int:
    from playwright.sync_api import sync_playwright
    import httpx

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="Frame.io .../view/<asset-id> URL")
    ap.add_argument("--out", required=True, help="Output capture dir")
    ap.add_argument("--name", default=None, help="Human/original file name (for doc extension + logging)")
    ap.add_argument("--timeout-ms", type=int, default=20000)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    net_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page()
        page.on("request", lambda r: net_urls.append(r.url))
        try:
            page.goto(args.url, wait_until="commit", timeout=args.timeout_ms)
        except Exception as e:
            print(f"(goto {e.__class__.__name__}; proceeding)", file=sys.stderr)
        page.wait_for_timeout(6000)
        title = page.title()
        final_url = page.url
        browser.close()

    hls = next((u for u in net_urls if HLS_MASTER_RE.search(u)), None)
    doc = next((u for u in net_urls if DOC_PROXY_RE.search(u)), None)

    if hls:
        dest = out / "video.mp4"
        r = subprocess.run(
            ["yt-dlp", "--no-warnings", "-o", str(dest), hls],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or not dest.exists():
            print(r.stdout, file=sys.stderr)
            print(r.stderr, file=sys.stderr)
            return 3
        kind, resolved, size = "video", hls, dest.stat().st_size
    elif doc:
        dest = out / f"document.{document_ext(args.name, doc)}"
        try:
            with httpx.stream("GET", doc, timeout=60.0, follow_redirects=True) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        except Exception as e:
            print(f"document download failed: {e}", file=sys.stderr)
            return 3
        kind, resolved, size = "document", doc, dest.stat().st_size
    else:
        print("no HLS master or document proxy URL observed in network log", file=sys.stderr)
        return 2

    (out / "meta.json").write_text(
        json.dumps(
            {
                "url": args.url,
                "final_url": final_url,
                "title": title,
                "name": args.name,
                "kind": kind,
                "resolved_url": resolved,
                "bytes": size,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps({"ok": True, "kind": kind, "bytes": size, "title": title}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
