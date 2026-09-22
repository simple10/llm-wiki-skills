# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.44"]
# ///
"""Capture a single Circle.so page — a space root or one lesson — into a capture dir.

platform: circle
scope: platform-general (any Circle-hosted community: *.circle.so or a
custom domain fronted by Circle). No hardcoded domain/slug — takes the URL
as an arg, or off the `ticket.json` the spawner wrote into the capture dir.

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

It does NOT write page.md, capture.json, report.json, or download assets —
the caller runs this unit's `to_markdown.py` and `section_plan.py` and the
harvest skill's `assets.py` on the outputs (keeps this script pure I/O). One
harvest ticket runs it once for the job's target and once per planned lesson:
`meta.json`'s `discovered_lesson_links` is what `section_plan.py plan` reads.

Usage:
  llm-wiki-ops run ops/skills/channel-circle/scripts/capture_lesson.py \
         <root> --out <capture_dir>                       # the ticket's target
         <root> --plan <capture_dir>/plan.json --leaf N   # one planned lesson
         <root> <url> --out <dir>                         # HAND RUNS ONLY
         [--headed] [--timeout-ms 45000]

`<root>` is the wiki root (`.` under `llm-wiki-ops run`, which starts a script
there) — auth profiles are reached through the credential store's
`profile-dir` lookup, keyed by domain. `--out` and `--plan` are WIKI-RELATIVE:
a relative one is resolved against `<root>`, not against wherever the caller
stands. A worker never types a url — a lesson's address is venue data and a
command line is a shell: with no url, the `target` of `<capture_dir>/ticket.json`
is captured; with `--leaf N`, leaf N of `plan.json` (its `order`) is captured
into the `dir` the plan gave it. A url on the command line is for hand runs.

Capturing the ticket's own target also REMOVES a stale `report.json` from the
capture dir first — the directory is stable across pulls, and a respawn must
never be read as a success it did not have.

Exit 0 on capture, 2 if there is no auth profile yet or the session
had expired (landed on a sign_in page) — either way, re-run the login
helper. 3 on a Cloudflare challenge that didn't clear. 5 if the credential
store itself could not be reached (denied/unreadable) — a REAL failure,
distinct from "no profile yet"; re-running the login helper will not fix it.
4 if there is nothing usable to capture: no url and no `ticket.json` naming a
target, a `--leaf` the plan does not hold, or a url that is not http(s).
6 if `--leaf` was asked after the plan's `deadline`: NOTHING was started — run
`section_plan.py report` and exit (the slice is killed at 30 minutes, and a
killed slice leaves no report).

History:
  2026-07-11  created — first Circle course capture.
  2026-07-29  packaged into the channel-circle skill unit; wiki root is a
              positional and auth profiles resolve under a machine-local
              auth directory.
  2026-08-04  auth profile lookup moves through the credential store's
              `profile-dir` verb instead of a hardcoded path.
  2026-09-19  the url may come off the capture dir's `ticket.json`; sidebar
              links are what `section_plan.py` plans a section from — no
              host queues them any more.
  2026-09-19  `--plan … --leaf N`: a lesson is named by number, never by a url
              on a command line; paths resolve against <root>; refuses to start
              a lesson past the plan's deadline; meta.json carries the
              navigation's `http_status`.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

STREAM_RE = re.compile(
    r"\.(m3u8|mpd|mp4|m4a|webm)(\?|$)|wistia|vimeocdn|vimeo\.com|"
    r"cloudfront|embedwistia|player\.",
    re.I,
)


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


def ticket_target(out: Path) -> str | None:
    """The `target` of the `ticket.json` the spawner wrote into the capture
    dir, or None where nothing spawned this capture."""
    try:
        ticket = json.loads((out / "ticket.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    target = ticket.get("target") if isinstance(ticket, dict) else None
    return target if isinstance(target, str) and target else None


EXIT_NOTHING_TO_CAPTURE = 4
EXIT_PAST_DEADLINE = 6


def under(root, given) -> Path:
    """A path argument as the wiki sees it: relative means relative to the
    wiki ROOT, which is what `capture_dir` and a leaf's `dir` are."""
    path = Path(given)
    return path if path.is_absolute() else Path(root) / path


def is_http(url) -> bool:
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
    except ValueError:
        return False
    return bool(parts and parts.scheme.lower() in ("http", "https") and parts.hostname)


def planned_leaf(plan_path: Path, number: int, now: float | None = None):
    """`(leaf, None)` for leaf `number` of the plan, or `(None, (exit, why))`.

    Refuses BEFORE a browser is started: a leaf the plan does not hold, and any
    leaf once the plan's deadline has passed — what is on disk then is what the
    report can truthfully say, and a lesson begun now is one the cap kills."""
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, (EXIT_NOTHING_TO_CAPTURE, f"{plan_path}: no readable plan ({e.__class__.__name__}) — run section_plan.py plan first")
    leaves = plan.get("leaves") if isinstance(plan, dict) else None
    leaf = next((one for one in leaves or [] if isinstance(one, dict) and one.get("order") == number), None)
    if leaf is None or not is_http(leaf.get("url")) or not isinstance(leaf.get("dir"), str):
        return None, (EXIT_NOTHING_TO_CAPTURE, f"{plan_path}: no leaf {number} — `order` in plan.json is the number")
    deadline = plan.get("deadline_epoch")
    if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
        if (time.time() if now is None else now) >= deadline:
            return None, (
                EXIT_PAST_DEADLINE,
                f"past the plan's deadline ({plan.get('deadline')}): leaf {number} NOT started — run section_plan.py report and exit",
            )
    return leaf, None


def resolve_job(root, url=None, out=None, plan=None, leaf=None, now=None):
    """`(url, out_dir, is_ticket_target, None)` or `(None, None, False, (exit, why))`
    — everything `main` decides before it needs a browser."""
    if leaf is not None:
        if not plan:
            return None, None, False, (EXIT_NOTHING_TO_CAPTURE, "--leaf needs --plan <capture_dir>/plan.json")
        found, refused = planned_leaf(under(root, plan), leaf, now=now)
        if refused:
            return None, None, False, refused
        target_dir = under(root, found["dir"])
        if out is not None and under(root, out).resolve() != target_dir.resolve():
            return None, None, False, (EXIT_NOTHING_TO_CAPTURE, f"--out disagrees with leaf {leaf}'s dir in the plan; leave it out")
        return found["url"], target_dir, False, None
    if out is None:
        return None, None, False, (EXIT_NOTHING_TO_CAPTURE, "--out <capture_dir> is required (wiki-relative) unless --plan/--leaf name a lesson")
    out_dir = under(root, out)
    chosen, from_ticket = (url, False) if url else (ticket_target(out_dir), True)
    if not chosen:
        return None, None, False, (EXIT_NOTHING_TO_CAPTURE, f"no url given and {out_dir / 'ticket.json'} names no target")
    if not is_http(chosen):
        return None, None, False, (EXIT_NOTHING_TO_CAPTURE, "the url is not an http(s) address")
    return chosen, out_dir, from_ticket, None


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="wiki root path (`.` under `llm-wiki-ops run`)")
    ap.add_argument("url", nargs="?", help="HAND RUNS ONLY. Default: `target` of <out>/ticket.json, or --leaf's url")
    ap.add_argument("--out", help="capture dir, wiki-relative; not needed with --leaf (the plan names the dir)")
    ap.add_argument("--plan", help="<capture_dir>/plan.json, wiki-relative — with --leaf")
    ap.add_argument("--leaf", type=int, metavar="N", help="capture leaf N of --plan (its `order`)")
    ap.add_argument("--headed", action="store_true", help="Show the browser (safer vs Cloudflare; default headless)")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    args = ap.parse_args()

    if args.leaf is not None and args.url:
        print("error: a url and --leaf are two names for the lesson — give one", file=sys.stderr)
        return EXIT_NOTHING_TO_CAPTURE
    args.url, out, is_ticket_target, refused = resolve_job(args.root, args.url, args.out, args.plan, args.leaf)
    if refused:
        print(f"error: {refused[1]}", file=sys.stderr)
        return refused[0]
    domain = domain_of(args.url)
    out.mkdir(parents=True, exist_ok=True)
    if is_ticket_target:
        # The flow's FIRST act: whatever an earlier pull — or the extractor —
        # left here as `report.json` is not this run's answer.
        (out / "report.json").unlink(missing_ok=True)

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

    from playwright.sync_api import sync_playwright  # here: everything above runs, and is tested, without it

    net = []
    http_status = None

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
            response = page.goto(args.url, wait_until="commit", timeout=args.timeout_ms)
            http_status = response.status if response is not None else None
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

        # Sidebar / curriculum links, in the order the course lists them. Nothing
        # queues these: `section_plan.py plan` reads them off meta.json and applies
        # the ticket's scope itself.
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
                    "http_status": http_status,  # 404/410 on a refresh ticket is `gone`
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
