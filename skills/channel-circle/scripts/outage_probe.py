# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44"]
# ///
"""Probe whether a Circle.so community is serving course content again.

platform: circle
scope: platform-general (any Circle-hosted community). Diagnostic/monitoring
helper — NOT part of the harvest worker loop.

Background (observed during a ~5h backend outage on one community): the
course page authenticates fine (no /sign_in redirect, cf_clearance valid)
but the standard-layout content wrapper renders EMPTY —
`<div class="standard-layout-v2__content-wrapper ..."></div>` with no
children — and several API XHRs return HTTP 500. If it were merely an
access/licensing issue the wrapper would instead show an upgrade CTA, not
be empty. So the fix signal is: the wrapper gains real child content AND no
5xx responses are seen.

Loads the page with the persistent auth profile (channel="chrome" + the
per-domain profile that earned cf_clearance; created by the plugin's
`llm-wiki-ops run scripts/login.py`), records every >=500 response,
and measures the wrapper's rendered size.

Usage:
  uv run outage_probe.py <root> <course-url> [--headed] [--timeout-ms 45000]

`<root>` is the wiki root. Always exits 0 (it's a probe, not a gate).
Prints a JSON verdict on stdout:
  {fixed, wrapper_children, wrapper_chars, http_5xx, sample_5xx,
   final_url, title, auth_ok}
fixed == (auth_ok AND http_5xx == 0 AND wrapper has real content).

History:
  2026-07-13  created — monitor a community-wide course outage.
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


def _ops_dirname():
    """The wiki's machinery-tree name, exported by the front door that ran
    this script (`LLM_WIKI_OPS_DIRNAME`). None outside `llm-wiki-ops run` —
    the caller treats the wiki as unreachable rather than guessing where its
    tree lives."""
    return os.environ.get("LLM_WIKI_OPS_DIRNAME") or None


def domain_of(url: str) -> str:
    # `.hostname` lowercases and drops the port, matching most of
    # `credentials.normalize_name` — but unlike that function this does NOT
    # IDNA-encode a non-ASCII host, so an internationalized community domain
    # would derive a unicode key here while login.py's `normalize_name`
    # wrote the ASCII `xn--…` form, and the two would never meet. No IDN
    # Circle community has been observed; flagging the divergence rather
    # than silently reproducing it.
    return urlsplit(url).hostname or ""


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="wiki root path")
    ap.add_argument("url")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    ap.add_argument("--settle-ms", type=int, default=8000, help="Wait after load for XHRs to fire / content to render")
    args = ap.parse_args()

    domain = domain_of(args.url)

    dirname = _ops_dirname()
    if dirname is None:
        print(
            json.dumps(
                {
                    "fixed": False,
                    "error": "LLM_WIKI_OPS_DIRNAME is not set — run "
                    "this through the wiki's front door "
                    "(`llm-wiki-ops run …`), which exports "
                    "it",
                }
            )
        )
        return 0
    try:
        proc = subprocess.run(
            [str(Path(args.root) / dirname / "bin" / "llm-wiki-ops"), "credential", "profile-dir", domain],
            capture_output=True,
            text=True,
        )
    except OSError as e:
        print(json.dumps({"fixed": False, "error": f"credential store unreachable ({e.__class__.__name__}: {e})"}))
        return 0
    if proc.returncode != 0:
        err = (
            f"no auth profile for {domain} — run llm-wiki-ops run scripts/login.py first"
            if proc.returncode == 1
            else f"credential store unreachable ({proc.returncode}): {(proc.stderr or '').strip()[:200]}"
        )
        print(json.dumps({"fixed": False, "error": err}))
        return 0
    profile_dir = Path(proc.stdout.strip())

    errors_5xx = []

    with sync_playwright() as p:
        launch_kw = dict(
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            context = p.chromium.launch_persistent_context(str(profile_dir), channel="chrome", **launch_kw)
        except Exception:
            context = p.chromium.launch_persistent_context(str(profile_dir), **launch_kw)

        page = context.pages[0] if context.pages else context.new_page()

        def on_response(r):
            try:
                if r.status >= 500:
                    errors_5xx.append({"status": r.status, "url": r.url[:160]})
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(args.url, wait_until="commit", timeout=args.timeout_ms)
        except Exception as e:
            print(f"(goto {e.__class__.__name__}; proceeding)", file=sys.stderr)
        for sel in ("main", '[class*="lesson"]', "article"):
            try:
                page.wait_for_selector(sel, timeout=8000)
                break
            except Exception:
                continue
        page.wait_for_timeout(args.settle_ms)

        final_url = page.url
        try:
            title = page.title()
        except Exception:
            title = ""

        auth_ok = not re.search(r"/sign_in|/users/sign_in|/login", final_url)

        # Measure the content wrapper — the outage's fix signal.
        try:
            wrapper = page.evaluate("""() => {
              const w = document.querySelector('.standard-layout-v2__content-wrapper')
                     || document.querySelector('main');
              if (!w) return {found: false, children: -1, chars: -1};
              return {found: true,
                      children: w.childElementCount,
                      chars: (w.innerText || '').trim().length};
            }""")
        except Exception as e:
            wrapper = {"found": False, "children": -1, "chars": -1, "err": e.__class__.__name__}

        context.close()

    # Dedupe 5xx by URL for a compact sample.
    seen, sample = set(), []
    for e in errors_5xx:
        if e["url"] not in seen:
            seen.add(e["url"])
            sample.append(e)
    wrapper_children = wrapper.get("children", -1)
    wrapper_chars = wrapper.get("chars", -1)

    fixed = bool(
        auth_ok and len(errors_5xx) == 0 and wrapper.get("found") and (wrapper_children > 0 or wrapper_chars > 200)
    )

    print(
        json.dumps(
            {
                "fixed": fixed,
                "wrapper_children": wrapper_children,
                "wrapper_chars": wrapper_chars,
                "http_5xx": len(errors_5xx),
                "sample_5xx": sample[:8],
                "final_url": final_url,
                "title": title,
                "auth_ok": auth_ok,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
