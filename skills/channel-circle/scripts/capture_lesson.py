# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44"]
# ///
"""Capture a single Circle.so lesson/post page into a raw capture dir.

platform: circle
scope: platform-general (any Circle-hosted community: *.circle.so or a
custom domain fronted by Circle). No hardcoded domain/slug — takes the URL
as an arg.

Circle is a React SPA behind Cloudflare, with lesson bodies and video
players rendered client-side. So we drive a real Chrome via Playwright
using the persistent per-domain profile that the plugin's login helper
(`llm-wiki-ops run scripts/login.py`) created — channel="chrome" plus
the profile that earned cf_clearance — wait for the lesson content to
render, then dump:
  - page.html         rendered DOM (asset-discovery ground truth)
  - net.json          network request log (HLS/mp4/wistia/vimeo stream URLs
                      that never appear in the DOM live here)
  - meta.json         title, final_url, canonical, discovered sidebar links

It does NOT write page.md, capture.json, or download assets — the caller
runs this unit's `to_markdown.py` and the harvest skill's `assets.py` on the
outputs (keeps this script pure I/O).

Usage:
  uv run capture_lesson.py <root> <lesson-url> --out <dir> \
         [--headed] [--timeout-ms 45000]

`<root>` is the wiki root — auth profiles are reached through the
credential store's `profile-dir` lookup, keyed by domain. Outputs land in
<dir>/. Exit 0 on capture, 2 if there is no auth profile yet or the session
had expired (landed on a sign_in page) — either way, re-run the login
helper. 3 on a Cloudflare challenge that didn't clear. 5 if the credential
store itself could not be reached (denied/unreadable) — a REAL failure,
distinct from "no profile yet"; re-running the login helper will not fix it.

History:
  2026-07-11  created — first Circle course capture.
  2026-07-29  packaged into the channel-circle skill unit; wiki root is a
              positional and auth profiles resolve under a machine-local
              auth directory.
  2026-08-04  auth profile lookup moves through the credential store's
              `profile-dir` verb instead of a hardcoded path.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

STREAM_RE = re.compile(
    r"\.(m3u8|mpd|mp4|m4a|webm)(\?|$)|wistia|vimeocdn|vimeo\.com|"
    r"cloudfront|embedwistia|player\.",
    re.I,
)


# The front door, by the bare name every SKILL.md already runs this script
# under — never a path into the wiki, which stops carrying a shim.
OPS = "llm-wiki-ops"

# What a nested front-door call must NOT inherit from the one that ran this
# script. The re-entry guard is still set in here — this script is the front
# door's grandchild — and a call carrying it is refused (127) as a loop, which
# this is not. And the front door binds to `CLAUDE_PROJECT_DIR` AHEAD of the
# cwd, so without dropping it `cwd=<root>` would not be what picks the wiki.
NOT_INHERITED = ("LLM_WIKI_OPS_DISPATCHED", "CLAUDE_PROJECT_DIR")


def _ops(root, *args):
    """One front-door command, bound to the wiki by running from its root,
    answered as JSON — the CLI's plain answer is prose for a person. An
    `llm-wiki-ops` that is not on PATH raises `FileNotFoundError` — an
    `OSError`, which the caller reports as an unreachable store."""
    env = {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}
    return subprocess.run([OPS, "--json", *args], cwd=str(root), env=env, capture_output=True, text=True)


def profile_dir(root, domain):
    """`(path, None)` for a profile a login has minted; else `(None, (kind,
    why))`, kind `absent` or `unreachable`.

    `credential profile-dir` exits 0 whether or not the directory exists —
    it reports, and creates nothing — so `exists` is the answer, never the
    exit code. Launching a persistent context on a path that is not there
    would MAKE it, and run the whole capture logged out without a word."""
    proc = _ops(root, "credential", "profile-dir", domain)
    try:
        answer = json.loads(proc.stdout)
    except ValueError:
        answer = None
    if proc.returncode != 0 or not isinstance(answer, dict) or not answer.get("path"):
        detail = answer.get("error") if isinstance(answer, dict) else None
        detail = detail or (proc.stderr or proc.stdout or "").strip()
        return None, ("unreachable", f"credential store unreachable ({proc.returncode}): {detail[:200]}")
    if answer.get("exists") is not True:
        return None, ("absent", f"no auth profile for {domain}")
    return Path(answer["path"]), None


def domain_of(url: str) -> str:
    # `.hostname` lowercases and drops the port, matching most of
    # `credentials.normalize_name` — but unlike that function this does NOT
    # IDNA-encode a non-ASCII host, so an internationalized community domain
    # would derive a unicode key here while login.py's `normalize_name`
    # wrote the ASCII `xn--…` form, and the two would never meet. No IDN
    # Circle community has been observed; flagging the divergence rather
    # than silently reproducing it.
    return urlsplit(url).hostname or ""


def caption_records(tracks):
    """Map resolved <track> dicts to (meta record incl. text) list. Keeps only
    kind in {captions, subtitles} with non-empty text; names files by srclang,
    falling back to track-<n>."""
    recs, n = [], 0
    for t in tracks:
        if t.get("kind") not in ("captions", "subtitles"):
            continue
        if not (t.get("text") or "").strip():
            continue
        n += 1
        lang = re.sub(r"[^a-z0-9-]", "", (t.get("srclang") or "").lower())
        name = f"{lang}.vtt" if lang else f"track-{n}.vtt"
        recs.append(
            {"lang": lang or f"track-{n}", "label": t.get("label") or "", "file": f"captions/{name}", "text": t["text"]}
        )
    return recs


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="wiki root path")
    ap.add_argument("url")
    ap.add_argument("--out", required=True, help="Output capture dir")
    ap.add_argument("--headed", action="store_true", help="Show the browser (safer vs Cloudflare; default headless)")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    args = ap.parse_args()

    domain = domain_of(args.url)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        profile, refused = profile_dir(args.root, domain)
    except OSError as e:
        print(f"error: credential store unreachable ({e.__class__.__name__}: {e})", file=sys.stderr)
        return 5
    if refused:
        kind, why = refused
        if kind == "absent":
            why += f". Run the plugin's login helper: llm-wiki-ops run scripts/login.py {domain}"
        print(f"error: {why}", file=sys.stderr)
        return 2 if kind == "absent" else 5

    net = []

    with sync_playwright() as p:
        launch_kw = dict(
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            context = p.chromium.launch_persistent_context(str(profile), channel="chrome", **launch_kw)
        except Exception:
            context = p.chromium.launch_persistent_context(str(profile), **launch_kw)

        page = context.pages[0] if context.pages else context.new_page()
        page.on(
            "request",
            lambda r: (
                net.append({"url": r.url, "method": r.method, "type": r.resource_type})
                if STREAM_RE.search(r.url)
                else None
            ),
        )

        try:
            # "commit" fires on navigation start — Circle rarely settles
            # domcontentloaded/networkidle (long-poll + beacons), so don't block
            # on it; the content-selector wait below is the real readiness gate.
            page.goto(args.url, wait_until="commit", timeout=args.timeout_ms)
        except Exception as e:
            print(f"(goto {e.__class__.__name__}; proceeding to content wait)", file=sys.stderr)
        # Let the SPA hydrate + video players attach. networkidle is flaky on
        # Circle (long-poll + analytics beacons never idle), so wait on the
        # main content region with a bounded fallback.
        for sel in ("main", '[class*="lesson"]', '[class*="post"]', "article"):
            try:
                page.wait_for_selector(sel, timeout=8000)
                break
            except Exception:
                continue
        page.wait_for_timeout(4000)

        final_url = page.url
        title = page.title()

        if re.search(r"/sign_in|/users/sign_in|/login", final_url):
            print(
                f"auth_expired: landed on {final_url} — re-run llm-wiki-ops run scripts/login.py for this domain",
                file=sys.stderr,
            )
            context.close()
            return 2

        # Circle's SPA keeps re-navigating (long-poll, lazy player mount), so
        # page.content() intermittently raises "page is navigating". Retry with
        # a short settle, then fall back to reading the live DOM via evaluate,
        # which has no navigation guard.
        html = None
        for _ in range(4):
            try:
                html = page.content()
                break
            except Exception:
                page.wait_for_timeout(1500)
        if html is None:
            html = page.evaluate("() => '<!DOCTYPE html>' + document.documentElement.outerHTML")
        if re.search(r"just a moment|cf-challenge|turnstile|checking your browser", html, re.I) and len(html) < 20000:
            print(
                "cloudflare_challenge: page did not clear — re-run "
                "llm-wiki-ops run scripts/login.py for this domain "
                "(the persistent profile carries cf_clearance)",
                file=sys.stderr,
            )
            context.close()
            return 3

        # Discovered sidebar / curriculum links (provenance; scope=page won't queue them)
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim().slice(0,80)}))"
        )
        lesson_links = [l for l in links if re.search(r"/lessons/|/sections/", l["href"])]

        canonical = ""
        try:
            canonical = page.eval_on_selector('link[rel="canonical"]', "e => e.href")
        except Exception:
            pass

        # Resolve <track> captions (blob: or URL) to text while the page is live.
        # Best-effort: a caption-fetch failure must never abort the capture.
        try:
            tracks = page.evaluate("""async () => {
              const els = Array.from(document.querySelectorAll('track'));
              const out = [];
              for (const el of els) {
                let text = "";
                try { text = await (await fetch(el.src)).text(); } catch (e) {}
                out.push({kind: el.kind, label: el.label, srclang: el.srclang, text});
              }
              return out;
            }""")
        except Exception as e:
            print(f"(caption resolution failed: {e.__class__.__name__}; continuing)", file=sys.stderr)
            tracks = []
        cap_recs = caption_records(tracks)
        if cap_recs:
            (out / "captions").mkdir(exist_ok=True)
            for r in cap_recs:
                (out / r["file"]).write_text(r["text"], encoding="utf-8")

        (out / "page.html").write_text(html, encoding="utf-8")
        (out / "net.json").write_text(json.dumps(net, indent=2), encoding="utf-8")
        (out / "meta.json").write_text(
            json.dumps(
                {
                    "url": args.url,
                    "final_url": final_url,
                    "title": title,
                    "canonical": canonical,
                    "domain": domain,
                    "html_bytes": len(html),
                    "stream_requests": net,
                    "discovered_lesson_links": lesson_links,
                    "captions": [{k: r[k] for k in ("lang", "label", "file")} for r in cap_recs],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        context.close()

    print(
        json.dumps(
            {
                "ok": True,
                "title": title,
                "final_url": final_url,
                "html_bytes": len(html),
                "stream_requests": len(net),
                "lesson_links": len(lesson_links),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
