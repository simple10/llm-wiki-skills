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
  llm-wiki-ops run ops/skills/channel-circle/scripts/outage_probe.py \
         <root> --ticket-dir <capture_dir> [--headed] [--settle-ms 8000]
         <root> <course-url> …                       # HAND RUNS ONLY

`<root>` is the wiki root (`.` under `llm-wiki-ops run`). With `--ticket-dir`
(wiki-relative: resolved against `<root>`) the url probed is the `target` of
that directory's `ticket.json` — a worker never types a venue url onto a
command line. Always exits 0 (it's a probe, not a gate).
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
  2026-09-19  `--ticket-dir`: the url comes off the ticket, never a command line.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


# The front door, by the bare name every SKILL.md already runs this script
# under — never a path.
OPS = "llm-wiki-ops"


def front_door() -> list:
    """The front door, as an argv prefix.

    A hosted run exports `LLM_WIKI_OPS`, naming the CLI it was itself reached
    by — a command LINE, not a path — and that is the one spelling a jail is
    sure to carry. Otherwise the bare name on PATH. Empty when there is
    neither."""
    named = os.environ.get("LLM_WIKI_OPS")
    if named:
        return shlex.split(named)
    found = shutil.which(OPS)
    return [found] if found else []


# What a nested front-door call must NOT inherit from the one that ran this
# script. `CLAUDE_PROJECT_DIR` is the harness's project directory, never a wiki
# root: the `cwd=<root>` this script was handed is what binds the nested call
# to THIS wiki.
NOT_INHERITED = ("CLAUDE_PROJECT_DIR",)


def _ops(root, *args):
    """One front-door command, bound to the wiki by running from its root,
    answered as JSON — the CLI's plain answer is prose for a person. An
    front door that is not reachable raises `FileNotFoundError` — an
    `OSError`, which the caller reports as an unreachable store."""
    env = {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}
    return subprocess.run([*(front_door() or [OPS]), "--json", *args], cwd=str(root), env=env, capture_output=True, text=True)


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


def ticket_target(root, ticket_dir) -> str | None:
    """The http(s) `target` of `<root>/<ticket_dir>/ticket.json`, or None."""
    if not ticket_dir:
        return None
    base = Path(ticket_dir) if Path(ticket_dir).is_absolute() else Path(root) / ticket_dir
    try:
        ticket = json.loads((base / "ticket.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    target = ticket.get("target") if isinstance(ticket, dict) else None
    return target if isinstance(target, str) and urlsplit(target).scheme in ("http", "https") else None


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="wiki root path")
    ap.add_argument("url", nargs="?", help="HAND RUNS ONLY; default: `target` of <ticket-dir>/ticket.json")
    ap.add_argument("--ticket-dir", help="the ticket's capture dir, wiki-relative — its ticket.json names the url")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    ap.add_argument("--settle-ms", type=int, default=8000, help="Wait after load for XHRs to fire / content to render")
    args = ap.parse_args()

    args.url = args.url or ticket_target(args.root, args.ticket_dir)
    if not args.url:
        print(json.dumps({"fixed": False, "error": "no url: give --ticket-dir <capture_dir> (its ticket.json names the target)"}))
        return 0
    domain = domain_of(args.url)

    try:
        profile, refused = profile_dir(args.root, domain)
    except OSError as e:
        print(json.dumps({"fixed": False, "error": f"credential store unreachable ({e.__class__.__name__}: {e})"}))
        return 0
    if refused:
        kind, why = refused
        if kind == "absent":
            why += " — run llm-wiki-ops run scripts/login.py first"
        print(json.dumps({"fixed": False, "error": why}))
        return 0

    errors_5xx = []

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
