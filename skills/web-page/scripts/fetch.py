#!/usr/bin/env python3
"""The plain-url harvester: the ninth unit, and the only one with a single
`script` stage under the harvest sandbox — no model session, ever.

  llm-wiki-ops run ops/skills/web-page/scripts/fetch.py ticket=<id>
  llm-wiki-ops run ops/skills/web-page/scripts/fetch.py --target <url> --capture-dir <dir>

The pass starts it exactly the first way (G2): no `run`, no token, and this
script is its own whole worker — it reads the ticket through `tickets open`
(A-1), fetches, and posts `tickets update` (A-2) itself, through the front
door. The second form is a hand run with no ticket and no job behind it: it
fetches into `--capture-dir` and prints the would-be status as JSON, posting
no `update` — the one thing the ticket arm does not cover.

Three files land in the capture directory and nothing outside it: the body
as the server sent it, `capture.json` naming it — the whole of what
`pipeline extract` reads (P-11) — and nothing else; the report is
`tickets update`'s (P-2).

Stdlib only, and deliberately NO PEP 723 block: the block routes a script
through `uv run --script`, which needs a writable uv cache, and a slice
grants none (G2) — so `run` execs the interpreter directly instead.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CAPTURE_V = 1
CAPTURE_NAME = "capture.json"

# The two schemes a job target may carry. Anything else — `file:`, `data:` —
# is refused before a connection is attempted: a worker fetches from the
# world, and reading the local disk is what its jail exists to stop.
SCHEMES = ("http", "https")

# The body file is named for what the server said it was, because the suffix
# is what the extractor picks a reader by.
BODY_STEM = "page"
SUFFIXES = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "application/json": ".json",
    "text/xml": ".xml",
    "application/xml": ".xml",
}
DEFAULT_SUFFIX = ".bin"

# The two statuses that say the page itself is over rather than unreachable.
# Read only on a refresh: on a first pull the same answer means the address
# the job was given was wrong, which is a failure and not a page's end.
GONE_CODES = (404, 410)

MAX_BYTES = 32 * 1024 * 1024
TIMEOUT_S = 30
USER_AGENT = "llm-wiki web-page"

# Why one url is in `missing[]`. `denied` is the one the host acts on: it is
# a host the jail would not let this worker reach, and a widen is the
# decision it asks for. Everything else is the venue's own answer.
WHY_DENIED = "denied"
WHY_TIMEOUT = "timeout"
WHY_AUTH = "auth"
WHY_ERROR = "error"

AUTH_CODES = (401, 403, 407)

# MEASURED (nono 0.75.0, Linux, `nono run --profile nested-scraper.json
# --allow-domain github.com`): a host outside the slice's allowlist reaches
# urllib as
#   URLError: <urlopen error Tunnel connection failed: 403 Forbidden:
#             host example.com:443 is not in the allowlist>
# with an `OSError` as its `reason`. Matched on either phrase, because the
# first is the proxy's CONNECT failure and the second is nono's own wording.
DENIED_MARKERS = ("tunnel connection failed", "not in the allowlist")

# MEASURED, same run: for under a second after such a denial the slice proxy
# refuses connections to everything, including allowed hosts — `curl` sees
# `(7) Failed to connect ... Could not connect to server`, and to urllib a
# refused socket is
#   URLError: <urlopen error [Errno 111] Connection refused>
# with `ConnectionRefusedError` as its `reason`. The denial may have been
# another process's in this same slice, so the retry does not wait to have
# seen one. Once, after a pause; a DENIAL itself is never retried, because
# nothing about it changes in a second.
REFUSED_MARKER = "connection refused"
RETRY_PAUSE_S = 1.0


# --------------------------------------------------------------- the front door


OPS = "llm-wiki-ops"


def front_door() -> list:
    """The front door, as an argv prefix (copied in every unit that talks to
    the ticket). A hosted run exports `LLM_WIKI_OPS`, naming the CLI it was
    itself reached by — a command LINE, not a path — and that is the one
    spelling a jail is sure to carry. Otherwise the bare name on PATH. Empty
    when there is neither."""
    named = os.environ.get("LLM_WIKI_OPS")
    if named:
        return shlex.split(named)
    found = shutil.which(OPS)
    return [found] if found else []


def open_ticket(ticket: str, stage: str | None = None) -> dict:
    """This worker's own ticket (A-1), through the front door. Exits naming
    the refusal — there is no report to post yet, because there is no
    capture directory to post one into until this answers."""
    door = front_door()
    if not door:
        sys.exit(f"fetch: `{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — the front door is how this unit reaches the plugin")
    argv = [*door, "--json", "pipeline", "tickets", "open", ticket]
    if stage:
        argv.append(f"stage={stage}")
    cp = subprocess.run(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        sys.exit(f"fetch: `tickets open {ticket}` refused — {(cp.stdout + cp.stderr).strip()}")
    try:
        return json.loads(cp.stdout)["ticket"]
    except (ValueError, KeyError) as exc:
        sys.exit(f"fetch: `tickets open {ticket}` did not answer a ticket ({exc}) — {cp.stdout}")


def post_update(
    ticket: str,
    stage: str,
    status: str,
    *,
    reason: str | None = None,
    captured=(),
    missing=(),
    written_from: str | None = None,
    produced: int | None = None,
    note: str | None = None,
) -> int:
    """This worker's progress (A-2), through the front door. `missing` is an
    iterable of `(host, url, why)`; a `,` inside `url` is typed as `%2C`,
    the side note every unit's `missing=` build follows the same way."""
    door = front_door()
    if not door:
        sys.exit(f"fetch: `{OPS}` is not on PATH and `LLM_WIKI_OPS` names nothing — the front door is how this unit posts progress")
    argv = [*door, "--json", "pipeline", "tickets", "update", ticket, f"stage={stage}", f"status={status}"]
    if reason:
        argv.append(f"reason={reason}")
    for directory in captured:
        argv.append(f"captured={directory}")
    for host, url, why in missing:
        argv.append(f"missing={host},{url.replace(',', '%2C')},{why}")
    if written_from:
        argv.append(f"written_from={written_from}")
    if produced is not None:
        argv.append(f"produced={produced}")
    if note:
        argv.append(f"note={note}")
    cp = subprocess.run(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        print(f"fetch: `tickets update` refused — {(cp.stdout + cp.stderr).strip()}", file=sys.stderr)
    return cp.returncode


# --------------------------------------------------------------- the fetch


def body_name(content_type: str) -> str:
    kind = (content_type or "").split(";")[0].strip().lower()
    return BODY_STEM + SUFFIXES.get(kind, DEFAULT_SUFFIX)


def _fetch_once(url: str, timeout: float) -> tuple[bytes, str, str]:
    """The body, its content type, and the url that actually answered."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — a http(s) target is the job
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError(f"the response is over the {MAX_BYTES} byte read cap")
        # `response.url` and not the request: a redirect means the bytes came
        # from somewhere else, and the capture records where they came from.
        return data, response.headers.get("Content-Type", ""), response.url


def refused_socket(exc: BaseException) -> bool:
    """Is this the proxy refusing a connection, rather than denying a host?"""
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ConnectionRefusedError) or REFUSED_MARKER in str(exc).lower()


def fetch(url: str, *, timeout: float = TIMEOUT_S, sleep=time.sleep) -> tuple[bytes, str, str]:
    """The page, retrying the one measured proxy quirk and nothing else."""
    try:
        return _fetch_once(url, timeout)
    except urllib.error.URLError as exc:
        if not refused_socket(exc):
            raise
        sleep(RETRY_PAUSE_S)
        return _fetch_once(url, timeout)


def _says(exc: BaseException, markers) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in markers)


def why_for(exc: BaseException) -> str:
    """Which of the four `missing[]` reasons this failure is.

    The denial is asked FIRST: over plain http a proxy answers the request
    itself, so a denial can arrive as an `HTTPError` whose code is one of the
    auth codes, and the host's decision is different for each.
    """
    if _says(exc, DENIED_MARKERS):
        return WHY_DENIED
    if isinstance(exc, urllib.error.HTTPError):
        return WHY_AUTH if exc.code in AUTH_CODES else WHY_ERROR
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return WHY_TIMEOUT
    return WHY_ERROR


def already_held(ticket: dict, url: str) -> bool:
    """Is this target one of the pages `known[]` says this job already has?

    Matched on `resource` exactly, the key the page carries and the host
    finds it by; a url normalized here would stop naming the page it is
    about.
    """
    entries = ticket.get("known")
    if not isinstance(entries, list):
        return False
    return any(isinstance(entry, dict) and entry.get("resource") == url for entry in entries)


def gone_status(exc: BaseException) -> int | None:
    """The 404 or 410 a refresh reads as `gone`, or None for anything else.

    The denial is asked first for the reason `why_for` gives: over plain http
    the proxy answers the request itself, and a status it invented is not the
    venue saying the page is over.
    """
    if _says(exc, DENIED_MARKERS):
        return None
    if isinstance(exc, urllib.error.HTTPError) and exc.code in GONE_CODES:
        return exc.code
    return None


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clear_stale(directory: Path) -> None:
    """P-7: every harvest run removes the capture directory's `capture.json`
    and any `page.*` an earlier run left, before it fetches — so `update`'s
    directory default never reports a run that never happened this time."""
    (directory / CAPTURE_NAME).unlink(missing_ok=True)
    for stale in directory.glob(f"{BODY_STEM}.*"):
        stale.unlink()


def write_capture(directory: Path, slug, url: str, data: bytes, content_type: str) -> str:
    """The body and the record naming it (P-11: `capture.TEXT_FIELDS`).
    `title` is null: the extractor reads the page's own, and one HTML parser
    in the pipeline is enough."""
    name = body_name(content_type)
    (directory / name).write_bytes(data)
    record = {
        "v": CAPTURE_V,
        "slug": slug,
        "item": url,
        "title": None,
        "body": name,
        "content_type": content_type,
        "fetched_at": now(),
    }
    (directory / CAPTURE_NAME).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return name


def run_ticketed(ticket_id: str) -> int:
    ticket = open_ticket(ticket_id, "harvest")
    capture_rel = ticket.get("capture_dir")
    if not isinstance(capture_rel, str) or not capture_rel:
        sys.exit(f"fetch: {ticket_id}'s ticket carries no capture_dir")
    directory = Path(capture_rel)
    directory.mkdir(parents=True, exist_ok=True)
    clear_stale(directory)

    url = ticket.get("target")
    if not isinstance(url, str) or not url:
        return post_update(ticket_id, "harvest", "failed", reason="no target: the ticket names none")

    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in SCHEMES:
        return post_update(ticket_id, "harvest", "failed", reason=f"{scheme or 'that'} is not a scheme this fetches")

    refresh = bool(ticket.get("refresh"))
    if not refresh and already_held(ticket, url):
        # A pull only ever adds to the corpus, so a target already in it has
        # nothing to fetch. Re-reading it is a refresh, which the host mints
        # its own ticket for and this run would answer the wrong question for.
        return post_update(ticket_id, "harvest", "ok", reason=f"known: {url}")

    try:
        data, content_type, landed = fetch(url)
        if not refresh and landed != url and already_held(ticket, landed):
            # The pre-fetch check above cannot see this: a redirecting source
            # only reveals the url `known[]` was stamped with once the fetch
            # has already landed. One fetch either way — this discards it
            # instead of paying a second one to find out.
            return post_update(ticket_id, "harvest", "ok", reason=f"known: {landed}")
        # Inside the try: a body this machine cannot write is a report saying
        # so, never a traceback the host reads back as `no_report`.
        write_capture(directory, ticket.get("slug"), landed, data, content_type)
    except Exception as exc:  # noqa: BLE001 — every failure is one update, never a traceback
        status = gone_status(exc) if refresh else None
        if status is not None:
            # The source answered, and its answer was that the page is over.
            # Nothing is in `missing[]`: that list is what a widen would fix,
            # and no permission reaches a page the venue has deleted.
            return post_update(ticket_id, "harvest", "gone", reason=f"gone: {status}")
        why = why_for(exc)
        return post_update(
            ticket_id, "harvest", "failed", reason=f"{why}: {exc}", missing=[(host_of(url), url, why)]
        )
    # `captured=` is left to `update`'s own directory default — the capture
    # directory this ticket names already holds `capture.json` (A-3).
    return post_update(ticket_id, "harvest", "ok")


def run_hand(directory: Path, target: str) -> dict:
    """The `--target` arm: no ticket, no `update`. Fetches into `directory`
    and prints the would-be status as JSON — the one feature the ticket arm
    does not cover, a fetch with no job behind it."""
    scheme = urllib.parse.urlsplit(target).scheme.lower()
    if scheme not in SCHEMES:
        return {"status": "failed", "reason": f"{scheme or 'that'} is not a scheme this fetches"}
    try:
        data, content_type, landed = fetch(target)
        write_capture(directory, None, landed, data, content_type)
    except Exception as exc:  # noqa: BLE001
        why = why_for(exc)
        return {"status": "failed", "reason": f"{why}: {exc}", "missing": [{"host": host_of(target), "url": target, "why": why}]}
    return {"status": "ok", "item": landed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "ticket_kv", nargs="?", metavar="ticket=<id>",
        help="the ticket id, as `run` invokes this script (G2) — `ticket=<id>`, not a flag",
    )
    parser.add_argument("--target", help="fetch this url directly, with no ticket and no `update` (a hand run)")
    parser.add_argument("--capture-dir", help="required with --target: where the hand run writes")
    args = parser.parse_args(argv)

    if args.target:
        if not args.capture_dir:
            sys.exit("fetch: --target needs --capture-dir")
        directory = Path(args.capture_dir)
        directory.mkdir(parents=True, exist_ok=True)
        result = run_hand(directory, args.target)
        print(json.dumps(result))
        return 0 if result["status"] == "ok" else 1

    if not args.ticket_kv or not args.ticket_kv.startswith("ticket="):
        sys.exit("fetch: pass ticket=<id> (as `run` invokes this script) or --target <url> --capture-dir <dir> for a hand run")
    ticket_id = args.ticket_kv[len("ticket=") :]
    if not ticket_id:
        sys.exit("fetch: ticket=<id> names no id")
    return run_ticketed(ticket_id)


if __name__ == "__main__":
    raise SystemExit(main())
