# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44"]
# ///
"""Recursively enumerate a Frame.io share's folder tree into a flat asset manifest.

platform: frameio
scope: platform-general (any next.frame.io/share/<share-id>[/<asset-id>]
link). No hardcoded share ids.

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
     "path": ["Top Folder", "Subfolder"],
     "view_url": "https://next.frame.io/share/<share>/view/<id>"}
  ]
}

Usage:
  uv run enumerate_tree.py <share-url> [--out tree.json]

History:
  2026-07-14  created — first Frame.io share harvest.
  2026-07-29  packaged into the channel-frameio skill unit.
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


def domain_of(url: str) -> str:
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


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
                name = card_name(c["text"], c["isFolder"])
                if c["isFolder"]:
                    child_url = f"https://next.frame.io/share/{share_id}/{c['id']}"
                    walk(child_url, path + [name])
                else:
                    leaves.append(
                        {
                            "asset_id": c["id"],
                            "name": name,
                            "path": path,
                            "view_url": f"https://next.frame.io/share/{share_id}/view/{c['id']}",
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
