#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.44"]
# ///
"""Capture one HubSpot CMS page whose content is a HubSpot Video.

Platform: HubSpot CMS + HubSpot Video, which is Mux underneath. Not tied to
any one site — every HubSpot customer runs its own domain, which is why the
unit matches on `custom_domains` and fingerprints rather than a host list.

Why this exists: a plain HTTP fetch yields NO player. The video iframe is lazy
— the markup carries `data-hsv-src`, never `src`, and HubSpot's script swaps it
in on load. The stream URL then appears only in the network log, and only as
SIGNED, expiring `*.edgemv.mux.com/.../rendition.m3u8` manifests. This script
renders the page, reads the Mux playback id out of the network log, and
rewrites it as the STABLE master playlist
`https://stream.mux.com/<playback_id>.m3u8`, which yt-dlp handles and which
does not expire.

Inputs / outputs (default mode, `render`):
  <capture-dir>/page.html   rendered DOM (asset-detection ground truth)
  <capture-dir>/net.json    network log, list of {url,type,method}
  <capture-dir>/meta.json   {title, mux_playback_id, player_url, final_url, ...}
  stdout                    the same meta.json as one JSON object

Second mode, `patch-assets`, applies the platform's manifest rules to a
manifest produced by the plugin's `assets.py detect`:
  - drop the signed edgemv rendition manifests (they expire; they are also
    per-rendition, so yt-dlp cannot pick a format from them)
  - drop the verifi.podscribe.com beacon (analytics pixel, not content)
  - append the stable Mux master playlist as the page's video asset,
    carrying the rendered iframe's live src as `embed_url` so a
    non-bundled note can embed the venue's player instead of linking a
    play.hubspotvideo.com URL that refuses to play outside its page

Usage:
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/capture_hubspot_video.py \\
      render <url> --capture-dir <dir>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/capture_hubspot_video.py \\
      patch-assets <capture-dir>/assets.json --meta <capture-dir>/meta.json

`--capture-dir` is host-derived (inside the watch's own slice) and handed
over by the caller — this unit never composes one itself.

(The leading `ops/` is the run verb's frozen argument grammar, resolved by
the front door to wherever this wiki's machinery tree lives.)

Exit 4 = rendered but no video resolved. On this platform that is a real page
shape (a pointer page whose payload is an external link), not necessarily a
failure — see the unit's SKILL.md.

No --access or --storage-state handling: HubSpot Video pages behind a login
have not been exercised. A site that needs auth wants the standard
`<domain>.storage` credential added here, not worked around.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# The playback id shows up in several shapes; the storyboard request is the
# most reliable because the player always fetches it, even before play.
MUX_ID_PATTERNS = [
    re.compile(r"image\.mux\.com/([A-Za-z0-9]{20,})/"),
    re.compile(r"stream\.mux\.com/([A-Za-z0-9]{20,})[./?]"),
    re.compile(r"inferred\.litix\.io/.*?playback_id=([A-Za-z0-9]{20,})"),
]
PLAY_SELECTORS = [
    "button[aria-label*='Play']",
    ".w-big-play-button",
    "[data-handle='bigPlayButton']",
    "video",
]
IFRAME_SELECTOR = "iframe[src*='hubspotvideo'], iframe[data-hsv-src*='hubspotvideo']"

DROP_URL_MARKERS = ("edgemv.mux.com", "verifi.podscribe.com/tag")


def find_mux_id(requests):
    for r in requests:
        for pat in MUX_ID_PATTERNS:
            m = pat.search(r["url"])
            if m:
                return m.group(1)
    return None


def render(args):
    cap = Path(args.capture_dir)
    cap.mkdir(parents=True, exist_ok=True)
    reqs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("request", lambda r: reqs.append({"url": r.url, "type": r.resource_type, "method": r.method}))
        page.goto(args.url, wait_until="networkidle", timeout=args.timeout)

        # Clicking play is what makes the player request the manifest. The
        # storyboard request usually precedes it, so a failed click is not
        # fatal — we only warn.
        clicked = None
        for sel in PLAY_SELECTORS:
            try:
                page.frame_locator(IFRAME_SELECTOR).locator(sel).first.click(timeout=4000)
                clicked = sel
                break
            except Exception:
                continue
        if clicked is None:
            print("warn: no play button clicked; relying on storyboard request", file=sys.stderr)
        page.wait_for_timeout(args.settle)

        html = page.content()
        title = (page.title() or "").strip()
        # Lesson title is the first H2 in the content root, NOT <title> and not
        # h1. HubSpot reuses one <title> across a whole course ("Start Here"
        # for all 11 Offers lessons), and the only two h1s in the corpus are
        # template chrome ("FREE ADVANCED TRAINING", "GET YOUR COPY NOW").
        # Verified against all 77 pages, 2026-07-31.
        h1 = page.evaluate(
            "() => { const h = document.querySelector('main#main-content h2')"
            " || document.querySelector('main#main-content h1');"
            " return h ? h.innerText.trim() : null; }"
        )
        tracks = page.evaluate(
            "() => Array.from(document.querySelectorAll('track'))"
            ".map(t => ({kind: t.kind, src: t.src, lang: t.srclang}))"
        )
        # The LIVE iframe src after HubSpot's script swapped data-hsv-src in
        # — verbatim, params and all (parentOrigin is what lets the player
        # run inside a frame; the bare player URL refuses to play outside
        # its page). scaffold renders it as the note's iframe when media
        # isn't bundled and [media] policy allows.
        embed_src = page.evaluate(
            "() => { const f = document.querySelector(\"iframe[src*='hubspotvideo']\"); return f ? f.src : null; }"
        )
        final_url = page.url
        ctx.close()
        browser.close()

    (cap / "page.html").write_text(html, encoding="utf-8")
    (cap / "net.json").write_text(json.dumps(reqs, indent=1))

    mux_id = find_mux_id(reqs)
    player = next((r["url"] for r in reqs if "play.hubspotvideo.com/v/" in r["url"]), None)
    meta = {
        "url": args.url,
        "final_url": final_url,
        "title": h1 or title,
        "page_title": title,
        "mux_playback_id": mux_id,
        "stream_url": f"https://stream.mux.com/{mux_id}.m3u8" if mux_id else None,
        "player_url": player.split("?")[0] if player else None,
        "embed_url": embed_src,
        "tracks": tracks,
        "play_selector": clicked,
        "requests": len(reqs),
    }
    (cap / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    return 0 if mux_id else 4  # 4 = rendered but no video resolved


def patch_assets(args):
    manifest = Path(args.manifest)
    assets = json.loads(manifest.read_text())
    meta = json.loads(Path(args.meta).read_text())

    kept = [a for a in assets if not any(m in (a.get("src_url") or "") for m in DROP_URL_MARKERS)]
    dropped = len(assets) - len(kept)

    if meta.get("stream_url"):
        kept.append(
            {
                "id": "asset-video-001",
                "type": "hls",
                "src_url": meta["stream_url"],
                "player_url": meta.get("player_url"),
                "embed_url": meta.get("embed_url"),
                "local_path": None,
                "sha256": None,
                "bytes": None,
                "status": "pending",
                "note": (
                    "HubSpot Video -> Mux; playback id read from the network log (image.mux.com/<id>/storyboard.vtt)"
                ),
            }
        )
    for i, a in enumerate(kept, 1):
        a.setdefault("id", f"asset-{i:03d}")
        a.setdefault("status", "pending")
        a.setdefault("local_path", None)
        a.setdefault("sha256", None)
        a.setdefault("bytes", None)
    manifest.write_text(json.dumps(kept, indent=1))
    print(json.dumps({"assets": len(kept), "dropped": dropped, "video": bool(meta.get("stream_url"))}))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render a lesson page and resolve its Mux stream")
    r.add_argument("url")
    r.add_argument("--capture-dir", required=True)
    r.add_argument("--timeout", type=int, default=90000, help="page.goto timeout (ms)")
    r.add_argument("--settle", type=int, default=9000, help="ms to wait after clicking play, for the manifest request")
    r.add_argument("--headed", action="store_true")
    r.set_defaults(fn=render)

    p = sub.add_parser("patch-assets", help="apply venue rules to an assets.py manifest")
    p.add_argument("manifest")
    p.add_argument("--meta", required=True)
    p.set_defaults(fn=patch_assets)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
