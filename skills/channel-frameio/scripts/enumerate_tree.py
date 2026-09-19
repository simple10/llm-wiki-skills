# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44"]
# ///
"""Recursively enumerate a Frame.io share's folder tree into a flat asset manifest.

platform: frameio
scope: platform-general (any `<host>/share/<share-id>[/<asset-id>]` link —
`next.frame.io` is the one seen so far). No hardcoded share ids, and no
hardcoded host: folder and leaf URLs are built on the ORIGIN of the URL that
was given. A slice's egress is the manifest's `*.frame.io` plus the ticket's
own target host, so the target's host is the one host certain to be granted;
building on a fixed `next.frame.io` sent a share served from anywhere else to
a host its slice could not reach, and gave its leaves a host `harvest.scope`
then judged against the wrong one.

Frame.io's guest share viewer is a React SPA, but folder/asset navigation is
plain client-side routing keyed by data-asset-id: every listing row is a
`[data-testid="asset-panel-grid-asset-card"]` div carrying `data-asset-id`,
and a `.folder-svg` child marks it as a folder vs a leaf file. Folder URLs are
`.../share/<share-id>/<asset-id>`; a leaf file's viewer is
`.../share/<share-id>/view/<asset-id>`. Both are directly navigable (no clicks
needed), so this walks the tree with plain page.goto() + DOM queries.

Outputs a JSON manifest to stdout (or --out):
{
  "share_id": "...", "domain": "...", "root_url": "...", "root_title": "...",
  "folders_visited": N, "leaf_count": N,
  "leaves": [
    {"asset_id": "...", "name": "<file name from the card text>",
     "path": ["Top Folder", "Subfolder"],   # folders walked INTO, below the
                                            # given URL; [] for a leaf listed
                                            # at it. Never the share's own
                                            # name — that is `root_title`.
     "view_url": "https://next.frame.io/share/<share>/view/<id>"}
  ]
}

Leaves come out in folder-walk order, which is stable for an unchanged
share. `harvest_share.py` captures them in that order and resumes a share too
large for one slice from the ticket's `known[]`, so the order is part of the
contract: do not sort or shuffle it.

Usage:
  uv run enumerate_tree.py <share-url> [--out <capture_dir>/tree.json]

History:
  2026-07-14  created — first Frame.io share harvest.
  2026-07-29  packaged into the channel-frameio skill unit.
  2026-09-19  docstring only — ported with the unit to the ticket contract:
              the manifest is written into the ticket's capture dir and read
              by `harvest_share.py`, which captures the leaves itself.
  2026-09-19  URLs are built on the given URL's own origin, not a hardcoded
              `next.frame.io`; an http(s) URL is required.
"""

import argparse
import json
import re
from urllib.parse import urlparse


def share_id_of(url: str) -> str:
    m = re.search(r"/share/([0-9a-f-]{36})", url)
    if not m:
        raise SystemExit(f"error: not a recognizable Frame.io share URL: {url}")
    return m.group(1)


def origin_of(url: str) -> str:
    """`https://<host>` of the URL that was given — what every folder and leaf
    URL of the walk is built on."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SystemExit(f"error: not an http(s) URL: {url}")
    return f"{parts.scheme}://{parts.netloc}"


def domain_of(url: str) -> str:
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


#: A card's `data-asset-id` is venue text that becomes part of a URL (a uuid
#: on every share seen). Anything else is left out of the walk, never spliced in.
ASSET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_BADGE_RE = re.compile(
    r"^(pg\.\s*\d+|\d{1,2}:\d{2}(:\d{2})?|Contains HTML|Interactive|"
    r"Preparing…|No Items|\d+\s*Items?|Status)$",
    re.I,
)
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def card_name(text: str, is_folder: bool) -> str:
    """Folders: first non-empty line. Leaf files: cards prefix a duration or
    page-count badge before the real filename, so prefer the line that looks
    like a filename (ends in an extension); fall back to the first line that
    isn't a known badge pattern."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return text.strip()
    if is_folder:
        return lines[0]
    for line in lines:
        if _EXT_RE.search(line):
            return line
    for line in lines:
        if not _BADGE_RE.match(line):
            return line
    return lines[0]


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="Root share URL (or any folder within one)")
    ap.add_argument("--out", help="Write manifest JSON here (default: stdout)")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    ap.add_argument("--max-folders", type=int, default=200, help="Safety cap on recursion breadth (default 200)")
    args = ap.parse_args()

    share_id = share_id_of(args.url)
    origin = origin_of(args.url)
    leaves = []
    visited = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page()

        def list_cards(url):
            page.goto(url, wait_until="commit", timeout=args.timeout_ms)
            page.wait_for_timeout(3500)
            return page.evaluate("""() => {
              const nodes = Array.from(document.querySelectorAll(
                '[data-testid="asset-panel-grid-asset-card"]'));
              return nodes.map(n => ({
                id: n.getAttribute('data-asset-id'),
                text: n.innerText || '',
                isFolder: !!n.querySelector('.folder-svg'),
              }));
            }""")

        root_title = None

        def walk(url, path):
            nonlocal root_title
            if url in visited or len(visited) >= args.max_folders:
                return
            visited.add(url)
            cards = list_cards(url)
            if root_title is None:
                root_title = page.title()
            for c in cards:
                if not isinstance(c.get("id"), str) or not ASSET_ID_RE.match(c["id"]):
                    continue
                name = card_name(c["text"], c["isFolder"])
                if c["isFolder"]:
                    child_url = f"{origin}/share/{share_id}/{c['id']}"
                    walk(child_url, path + [name])
                else:
                    leaves.append(
                        {
                            "asset_id": c["id"],
                            "name": name,
                            "path": path,
                            "view_url": f"{origin}/share/{share_id}/view/{c['id']}",
                        }
                    )

        walk(args.url, [])
        browser.close()

    manifest = {
        "share_id": share_id,
        "domain": domain_of(args.url),
        "root_url": args.url,
        "root_title": root_title,
        "folders_visited": len(visited),
        "leaf_count": len(leaves),
        "leaves": leaves,
    }
    out_json = json.dumps(manifest, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out_json)
        print(json.dumps({"ok": True, "leaf_count": len(leaves), "folders_visited": len(visited), "out": args.out}))
    else:
        print(out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
