#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.44"]
# ///
"""Render one HubSpot CMS page whose content is a HubSpot Video.

Platform: HubSpot CMS + HubSpot Video, which is Mux underneath. Not tied to
any one site — every HubSpot customer runs its own domain, which is why the
unit's `requires.network` names the PLATFORM's fixed hosts (the player, Mux)
and no site: a site is reached by keywords and fingerprints, and its own host
is the job's target.

Why this exists: a plain HTTP fetch yields NO player. The video iframe is lazy
— the markup carries `data-hsv-src`, never `src`, and HubSpot's script swaps it
in on load. The stream URL then appears only in the network log, and only as
SIGNED, expiring `*.edgemv.mux.com/.../rendition.m3u8` manifests. This script
renders the page, reads the Mux playback id out of the network log, and
rewrites it as the STABLE master playlist
`https://stream.mux.com/<playback_id>.m3u8`, which yt-dlp handles and which
does not expire.

This is the capture I/O of ONE page — one leaf of a section harvest. Which
pages, which directories, the flat `capture.json` and the `report.json` are
the sibling `leaves.py`'s; the PAGE is written at process, per SKILL.md.

Inputs / outputs (default mode, `render`):
  <capture-dir>/page.html   rendered DOM (asset-detection ground truth, and
                            the capture's own body)
  <capture-dir>/net.json    network log, list of {url,type,method}
  <capture-dir>/meta.json   {title, mux_playback_id, player_url, final_url, ...}
  stdout                    the same meta.json as one JSON object

**In a harvest the page is named by `--leaf <n>`, never by its url**: the
url is the venue's text (a sitemap `<loc>`), and a venue's text typed onto a
shell line is a command. `--capture-dir` is then the TICKET's capture
directory, the url and the leaf's own directory are read off
`<capture-dir>/plan.json` (`leaves.py plan` wrote it, and dropped every url
outside a conservative character set), and the three files land in that leaf.
`<url>` is for a hand run; with neither, the `item` of
`<capture-dir>/ticket.json` is the page.

`meta.json` also carries `status` (the HTTP status the page answered with —
404/410 is `gone` on a refresh ticket, never a capture) and `fetched_at` (when
THIS render read the page, which `leaves.py record` writes into `capture.json`).

Second mode, `patch-assets`, applies the platform's manifest rules to a
manifest produced by the plugin's `assets.py detect`:
  - drop the signed edgemv rendition manifests (they expire; they are also
    per-rendition, so yt-dlp cannot pick a format from them)
  - drop the verifi.podscribe.com beacon (analytics pixel, not content)
  - append the stable Mux master playlist as the page's video asset,
    carrying the rendered iframe's live src as `embed_url` — the same value
    the process step puts on the page as its iframe, because a bare
    play.hubspotvideo.com URL refuses to play outside its page

Usage (`<capture_dir>` is the ticket's `capture_dir`, verbatim: it is
WIKI-RELATIVE, and `run` starts this script at the wiki root):
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/capture_hubspot_video.py \\
      render --capture-dir <capture_dir> --leaf <n>
  llm-wiki-ops run ops/skills/channel-hubspot-video/scripts/capture_hubspot_video.py \\
      patch-assets <leaf dir>/assets.json --meta <leaf dir>/meta.json
(`leaves.py assets --leaf <n>` runs `patch-assets` itself, between the
plugin's detect and download.)

It launches a BROWSER: the Playwright Chromium build has to be on the machine
already. A slice cannot install one — the floor write-denies
`~/.cache/ms-playwright` and `~/Library/Caches/ms-playwright`
(`schedule/runner/floor.py::DENY_WRITE_OUTSIDE`) — see the unit's INSTALL.md.

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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# The playback id shows up in several shapes; the storyboard request is the
# most reliable because the player always fetches it, even before play.
# Anchored on the scheme and the HOST: a request to anywhere else that merely
# carries `image.mux.com/<id>/` in its query is the venue's text, not Mux.
MUX_ID_PATTERNS = [
    re.compile(r"^https://image\.mux\.com/([A-Za-z0-9]{20,})/"),
    re.compile(r"^https://stream\.mux\.com/([A-Za-z0-9]{20,})[./?]"),
    re.compile(r"^https://inferred\.litix\.io/[^#]*?[?&]playback_id=([A-Za-z0-9]{20,})(?:[&#]|$)"),
]
PLAY_SELECTORS = [
    "button[aria-label*='Play']",
    ".w-big-play-button",
    "[data-handle='bigPlayButton']",
    "video",
]
# The player's own origin as a PREFIX, not `hubspotvideo` anywhere in the
# value: `https://evil.example/?hubspotvideo` is not a player.
PLAYER_ORIGIN = "https://play.hubspotvideo.com/"
_PLAYER_PREFIXES = (PLAYER_ORIGIN, PLAYER_ORIGIN.removeprefix("https:"))  # the attribute may be protocol-relative
IFRAME_SELECTOR = ", ".join(f"iframe[{attr}^='{prefix}']" for attr in ("src", "data-hsv-src") for prefix in _PLAYER_PREFIXES)
# (Tightened from a substring match on 2026-09-19; unverified against a live site since.)

DROP_URL_MARKERS = ("edgemv.mux.com", "verifi.podscribe.com/tag")


def find_mux_id(requests):
    for r in requests:
        for pat in MUX_ID_PATTERNS:
            m = pat.search(r["url"])
            if m:
                return m.group(1)
    return None


def ticket_item(capture_dir):
    """The ticket's own page, where the spawner left a `ticket.json` here."""
    try:
        ticket = json.loads((Path(capture_dir) / "ticket.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    item = ticket.get("item") if isinstance(ticket, dict) else None
    return item if isinstance(item, str) and item.startswith(("http://", "https://")) else None


def planned_leaf(capture_dir, n):
    """`(url, leaf directory)` for `--leaf <n>`, off the ticket's `plan.json`.

    The directory is the plan's wiki-relative `dir` under the wiki root, which
    is three levels above a capture directory (`_raw/<slug>/<leaf>`)."""
    cap = Path(capture_dir).resolve()
    try:
        leaves = json.loads((cap / "plan.json").read_text(encoding="utf-8"))["leaves"]
        leaf = leaves[n] if 0 <= n < len(leaves) else None
    except (OSError, ValueError, KeyError, TypeError):
        leaf = None
    if not isinstance(leaf, dict) or leaf.get("media_of") or not isinstance(leaf.get("item"), str) or not isinstance(leaf.get("dir"), str):
        return None
    parts = Path(leaf["dir"]).parts
    if len(parts) != 3 or parts[0] != "_raw" or cap.parts[-3:-1] != parts[:2]:
        return None
    return leaf["item"], cap.parents[2] / leaf["dir"]


def is_player(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc == urlsplit(PLAYER_ORIGIN).netloc and parts.path.startswith("/v/")


def render(args):
    # Imported here, not at the top: `patch-assets` is pure JSON and must run
    # where no browser is installed.
    from playwright.sync_api import sync_playwright

    cap = Path(args.capture_dir)
    if args.leaf is not None:
        found = planned_leaf(cap, args.leaf)
        if found is None:
            print(f"--leaf {args.leaf}: {cap}/plan.json names no page at that index — run `leaves.py plan` first, and pass the ticket's capture_dir", file=sys.stderr)
            return 2
        args.url, cap = found
    args.url = args.url or ticket_item(cap)
    if not args.url or not args.url.startswith(("http://", "https://")):
        print(f"no http(s) url given and no ticket.json in {cap} names one", file=sys.stderr)
        return 2
    cap.mkdir(parents=True, exist_ok=True)
    reqs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("request", lambda r: reqs.append({"url": r.url, "type": r.resource_type, "method": r.method}))
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = page.goto(args.url, wait_until="networkidle", timeout=args.timeout)
        status = response.status if response is not None else None

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
        # its page). The process step puts it on the page as its iframe,
        # and leaves it out where the job says `process.embeds: false`,
        # which is why a plain link rides beside it.
        embed_src = page.evaluate(
            "(origin) => { const f = Array.from(document.querySelectorAll('iframe')).find(f => {"
            " try { return new URL(f.src).origin + '/' === origin; } catch (e) { return false; } });"
            " return f ? f.src : null; }",
            PLAYER_ORIGIN,
        )
        final_url = page.url
        ctx.close()
        browser.close()

    (cap / "page.html").write_text(html, encoding="utf-8")
    (cap / "net.json").write_text(json.dumps(reqs, indent=1))

    mux_id = find_mux_id(reqs)
    player = next((r["url"] for r in reqs if is_player(r["url"])), None)
    meta = {
        "url": args.url,
        "final_url": final_url,
        "status": status,
        "fetched_at": fetched_at,
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
    r.add_argument("url", nargs="?", default=None, help="a HAND run only — never a url read off a venue; default: the `item` of <capture-dir>/ticket.json")
    r.add_argument("--capture-dir", required=True, help="wiki-relative. With --leaf: the TICKET's capture_dir; else the leaf to write into")
    r.add_argument("--leaf", type=int, default=None, help="the page's index in <capture-dir>/plan.json's leaves[] — what `leaves.py next` printed")
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
