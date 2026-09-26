"""The plain-url harvester: what it fetches, what it posts, and what it
refuses. Ported from the plugin's `harvest-page` (`fetch.py` was identical
line for line in behavior; this unit differs only in how it reaches its
ticket and posts progress — `tickets open`/`tickets update`, through a stub
front door, never a `ticket.json`/`report.json` file pair)."""

import json
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

UNIT_DIR = Path(__file__).resolve().parents[1]  # the unit, wherever its tree sits
SCRIPT = UNIT_DIR / "scripts" / "fetch.py"

PAGE = b"<html><title>A Page</title><body><article><p>Hello from the venue.</p></article></body></html>"
TICKET = "abc123def456"
CAPTURE_REL = "_raw/ex/article--abcd1234"

# MEASURED under `nono run --profile nested-scraper.json --allow-domain
# github.com` (nono 0.75.0, Linux): what a host outside the allowlist, and a
# proxy socket that refuses, look like to urllib.
DENIAL = "<urlopen error Tunnel connection failed: 403 Forbidden: host example.com:443 is not in the allowlist>"


def _load():
    import importlib.util

    spec = importlib.util.spec_from_file_location("web_page_fetch", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fetch():
    return _load()


class _Handler(BaseHTTPRequestHandler):
    # Every path this served, so a test can assert nothing was fetched at
    # all — an empty argv alone cannot tell a skip from a failed request.
    SEEN: list[str] = []

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's own spelling
        _Handler.SEEN.append(self.path)
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/article")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path in ("/missing", "/gone"):
            self.send_error(404 if self.path == "/missing" else 410)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        pass


@pytest.fixture
def served():
    _Handler.SEEN = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/article"
    server.shutdown()
    server.server_close()


@pytest.fixture
def closed_port():
    """A port nothing listens on: bound to find a free one, then released."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}/article"


# --------------------------------------------------------- the stub front door


def _stub_ops(root: Path, ticket: dict) -> str:
    """A stand-in for `tickets open`/`tickets update`, for the cases whose
    wiki is a bare tmp directory. `open` always answers `ticket`; `update`
    appends its argv, verb included, to `update-calls.jsonl` and exits 0."""
    stub = root / "ops_stub.py"
    stub.write_text(
        "import json, pathlib, sys\n"
        "argv = [a for a in sys.argv[1:] if a != '--json']\n"
        f"TICKET = json.loads({json.dumps(json.dumps(ticket))})\n"
        "if argv[:3] == ['pipeline', 'tickets', 'open']:\n"
        "    print(json.dumps({'ticket': TICKET}))\n"
        "    sys.exit(0)\n"
        "if argv[:3] == ['pipeline', 'tickets', 'update']:\n"
        "    pathlib.Path('update-calls.jsonl').open('a').write(json.dumps(argv) + '\\n')\n"
        "    sys.exit(0)\n"
        "sys.exit('ops_stub: unhandled ' + repr(argv))\n"
    )
    return shlex.join([sys.executable, str(stub)])


def _updates(root: Path) -> list:
    path = root / "update-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _kv(argv: list) -> dict:
    """The `key=value` tail of an update call, as a dict — `missing=` and
    `captured=` repeat, so those two collect into lists."""
    out: dict = {}
    for tok in argv:
        if "=" not in tok or tok.startswith("--"):
            continue
        k, v = tok.split("=", 1)
        if k in ("missing", "captured"):
            out.setdefault(k, []).append(v)
        else:
            out[k] = v
    return out


def _ticket(target: str, **over) -> dict:
    ticket = {
        "ticket": TICKET, "stage": "harvest", "slug": "ex", "capture_dir": CAPTURE_REL,
        "item": target, "target": target, "hosts": ["127.0.0.1"], "known": [], "options": {},
    }
    ticket.update(over)
    return ticket


def _run(root: Path, ticket: dict, *extra: str, check=False) -> subprocess.CompletedProcess:
    (root / Path(ticket["capture_dir"])).mkdir(parents=True, exist_ok=True)
    env = {**__import__("os").environ, "LLM_WIKI_OPS": _stub_ops(root, ticket)}
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), f"ticket={ticket['ticket']}", *extra],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    if check:
        assert cp.returncode == 0, cp.stderr
    return cp


def _capture_dir(root: Path, ticket: dict) -> Path:
    return root / Path(ticket["capture_dir"])


# ---------------------------------------------------------------- the body


def test_the_script_carries_no_pep723_block():
    """The block routes it through `uv run --script`, and a slice has no uv
    cache (G2). Read as text, never through the plugin's own probe — a
    unit's tests ship standalone and import nothing of it."""
    assert not re.search(r"^# /// script", SCRIPT.read_text(encoding="utf-8"), re.M)


def test_the_script_imports_only_the_standard_library():
    """It runs inside a slice, where the only packages are the interpreter's own."""
    imported = {
        line.removeprefix("import ").removeprefix("from ").split()[0].split(".")[0]
        for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {"__future__", "argparse", "json", "os", "pathlib", "shlex", "shutil", "subprocess", "sys", "time", "urllib"}


def test_no_argv_is_ever_passed_to_a_shell():
    """P-6: no venue-authored string reaches a shell line."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "shell=True" not in text and "shell = True" not in text


# ---------------------------------------------------------------- the fetch


def test_a_served_page_lands_as_a_capture(tmp_path, served):
    ticket = _ticket(served)
    result = _run(tmp_path, ticket, check=True)
    cap = _capture_dir(tmp_path, ticket)
    record = json.loads((cap / "capture.json").read_text(encoding="utf-8"))
    assert record["item"] == served and record["slug"] == "ex"
    assert (cap / record["body"]).read_bytes() == PAGE
    (call,) = _updates(tmp_path)
    kv = _kv(call)
    assert call[:5] == ["pipeline", "tickets", "update", TICKET, "stage=harvest"]
    assert kv["status"] == "ok" and "captured" not in kv and "missing" not in kv, result.stderr


def test_a_closed_port_is_reported_missing_with_a_why(tmp_path, closed_port):
    ticket = _ticket(closed_port)
    _run(tmp_path, ticket, check=True)
    (call,) = _updates(tmp_path)
    kv = _kv(call)
    assert kv["status"] == "failed"
    assert kv["missing"] == [f"127.0.0.1,{closed_port},error"]
    assert not (_capture_dir(tmp_path, ticket) / "capture.json").exists()


def test_a_redirect_lands_the_url_that_answered(tmp_path, served):
    """`item` is where the bytes came from, which a redirect moves."""
    ticket = _ticket(served.replace("/article", "/moved"))
    _run(tmp_path, ticket, check=True)
    record = json.loads((_capture_dir(tmp_path, ticket) / "capture.json").read_text(encoding="utf-8"))
    assert record["item"] == served
    assert _kv(_updates(tmp_path)[0])["status"] == "ok"


def test_a_redirecting_target_already_held_at_its_landed_url_is_known(tmp_path, served):
    """`already_held` misses the pre-fetch url on a redirect; the landed url still catches it."""
    ticket = _ticket(served.replace("/article", "/moved"), known=[{"resource": served}])
    _run(tmp_path, ticket, check=True)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "ok" and "known" in kv["reason"]
    assert not (_capture_dir(tmp_path, ticket) / "capture.json").exists()


def test_a_target_named_on_the_command_line_needs_no_ticket(tmp_path, served):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--target", served, "--capture-dir", str(tmp_path / "hand")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == "ok" and out["item"] == served
    assert not (tmp_path / "update-calls.jsonl").exists(), "a hand run posts no update"


def test_no_target_on_the_ticket_is_a_report_and_not_a_traceback(tmp_path):
    ticket = _ticket(None)
    result = _run(tmp_path, ticket, check=True)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "failed" and "traceback" not in result.stderr.lower()


# ------------------------------------------------- what this watch already holds


def test_a_target_the_watch_already_holds_is_not_fetched(tmp_path, served):
    ticket = _ticket(served, known=[{"resource": served}])
    _run(tmp_path, ticket, check=True)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "ok" and "known" in kv["reason"]
    assert _Handler.SEEN == []
    assert not (_capture_dir(tmp_path, ticket) / "capture.json").exists()


def test_a_known_page_that_is_not_the_target_is_fetched_anyway(tmp_path, served):
    """`known[]` is the whole corpus; only an entry naming THIS target skips it."""
    ticket = _ticket(served, known=[{"resource": served + "-other"}])
    _run(tmp_path, ticket, check=True)
    assert _kv(_updates(tmp_path)[0])["status"] == "ok"
    assert _Handler.SEEN == ["/article"]


def test_a_refresh_fetches_a_target_the_watch_already_holds(tmp_path, served):
    ticket = _ticket(served, known=[{"resource": served}], refresh=True)
    _run(tmp_path, ticket, check=True)
    assert _kv(_updates(tmp_path)[0])["status"] == "ok"
    assert _Handler.SEEN == ["/article"]


@pytest.mark.parametrize("path", ("/missing", "/gone"))
def test_a_refresh_of_a_source_that_answers_404_or_410_is_gone(tmp_path, served, path):
    url = served.replace("/article", path)
    ticket = _ticket(url, known=[{"resource": url}], refresh=True)
    _run(tmp_path, ticket, check=True)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "gone"
    # Nothing to widen: the source answered, and its answer was the page's end.
    assert "missing" not in kv
    assert _Handler.SEEN == [path]


def test_a_404_on_a_first_pull_is_still_a_failure(tmp_path, served):
    """`gone` is a refresh ticket's alone: a first pull that 404s was pointed wrong."""
    url = served.replace("/article", "/missing")
    ticket = _ticket(url)
    _run(tmp_path, ticket, check=True)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "failed"
    assert kv["missing"][0].endswith(",error")


# ------------------------------------------------- the denial and the retry


def _failing(monkeypatch, fetch, exc, *, then=None):
    """`_fetch_once` raising one measured failure, then answering."""
    calls = []

    def once(url, timeout):
        calls.append(url)
        if then is not None and len(calls) > 1:
            return then
        raise exc

    monkeypatch.setattr(fetch, "_fetch_once", once)
    return calls


def _refusal():
    return URLError(ConnectionRefusedError(111, "Connection refused"))


def test_a_denied_host_is_classified_denied(fetch):
    assert fetch.why_for(URLError(DENIAL)) == fetch.WHY_DENIED


def test_a_refused_socket_is_not_a_denial(fetch):
    assert fetch.why_for(_refusal()) == fetch.WHY_ERROR


def test_a_proxy_that_answers_403_over_plain_http_is_still_a_denial(fetch):
    """The denial is asked before the status: over http the proxy answers itself."""
    denied = HTTPError("http://example.com/a", 403, "Forbidden: host example.com:80 is not in the allowlist", {}, None)
    assert fetch.why_for(denied) == fetch.WHY_DENIED


def test_a_proxy_that_answers_404_is_not_a_gone_page(fetch):
    """The denial is asked before the status here too: a 404 it invented is not the venue's."""
    denied = HTTPError("http://example.com/a", 404, "host example.com:80 is not in the allowlist", {}, None)
    assert fetch.gone_status(denied) is None


def test_a_plain_403_from_the_venue_is_auth(fetch):
    assert fetch.why_for(HTTPError("https://example.com/a", 403, "Forbidden", {}, None)) == fetch.WHY_AUTH


def test_a_timeout_is_a_timeout(fetch):
    assert fetch.why_for(URLError(TimeoutError("timed out"))) == fetch.WHY_TIMEOUT


def test_a_denial_reaches_the_update_as_the_host_to_widen(tmp_path, monkeypatch, fetch):
    ticket = _ticket("https://example.com/a")
    (tmp_path / Path(ticket["capture_dir"])).mkdir(parents=True, exist_ok=True)
    calls = _failing(monkeypatch, fetch, URLError(DENIAL))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch, "front_door", lambda: [sys.executable, str(tmp_path / "ops_stub.py")])
    _stub_ops(tmp_path, ticket)
    code = fetch.run_ticketed(TICKET)
    assert len(calls) == 1, "a denial is final — nothing about it changes in a second"
    assert code == 0
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "failed"
    assert kv["missing"] == ["example.com,https://example.com/a,denied"]


def test_a_refused_socket_is_retried_once(monkeypatch, fetch):
    """Measured: for under a second after a denial the proxy refuses every connection."""
    slept = []
    answer = (b"ok", "text/plain", "https://example.com/a")
    calls = _failing(monkeypatch, fetch, _refusal(), then=answer)
    assert fetch.fetch("https://example.com/a", sleep=slept.append) == answer
    assert len(calls) == 2
    assert slept == [fetch.RETRY_PAUSE_S]


def test_a_refusal_that_outlasts_the_pause_is_reported_rather_than_retried_again(monkeypatch, fetch):
    calls = _failing(monkeypatch, fetch, _refusal())
    with pytest.raises(URLError):
        fetch.fetch("https://example.com/a", sleep=lambda _s: None)
    assert len(calls) == 2


def test_a_denial_is_never_retried(monkeypatch, fetch):
    calls = _failing(monkeypatch, fetch, URLError(DENIAL))
    with pytest.raises(URLError):
        fetch.fetch("https://example.com/a", sleep=lambda _s: None)
    assert len(calls) == 1


# ---------------------------------------------------------------- what it refuses


@pytest.mark.parametrize("url", ("file:///etc/passwd", "data:text/html,<p>hi", "ftp://example.com/a"))
def test_only_http_and_https_are_fetched(tmp_path, url):
    ticket = _ticket(url)
    _run(tmp_path, ticket, check=True)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "failed" and "scheme" in kv["reason"]
    assert not (_capture_dir(tmp_path, ticket) / "capture.json").exists()


def test_it_runs_with_no_uv_on_path(tmp_path, served):
    """Plain `.py`, run through `sys.executable` — never `uv run --script`."""
    ticket = _ticket(served)
    empty = tmp_path / "bin"
    empty.mkdir()
    (tmp_path / Path(ticket["capture_dir"])).mkdir(parents=True, exist_ok=True)
    env = {"PATH": str(empty), "HOME": str(tmp_path), "LLM_WIKI_OPS": _stub_ops(tmp_path, ticket)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), f"ticket={TICKET}"], cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert shutil.which("uv", path=str(empty)) is None


# ---------------------------------------------------------------- P-7, P-1


def test_a_stale_capture_is_cleared_before_a_failing_fetch_leaves_nothing(tmp_path, closed_port):
    """P-7: an earlier run's `capture.json` and `page.*` must not survive a
    run that fetches nothing this time."""
    ticket = _ticket(closed_port)
    cap = _capture_dir(tmp_path, ticket)
    cap.mkdir(parents=True)
    (cap / "capture.json").write_text('{"slug": "old"}')
    (cap / "page.html").write_bytes(b"stale")
    _run(tmp_path, ticket, check=True)
    assert not (cap / "capture.json").exists() and not (cap / "page.html").exists()
    kv = _kv(_updates(tmp_path)[0])
    assert kv["status"] == "failed" and "captured" not in kv


def test_a_decoy_ticket_json_in_the_directory_is_never_read(tmp_path, served):
    """P-1: the worker reads its ticket through `tickets open` and nothing
    else — a `ticket.json` left in the capture directory naming a different
    target must not be read."""
    ticket = _ticket(served)
    cap = _capture_dir(tmp_path, ticket)
    cap.mkdir(parents=True)
    (cap / "ticket.json").write_text(json.dumps({"target": "https://example.invalid/decoy"}))
    _run(tmp_path, ticket, check=True)
    assert _Handler.SEEN == ["/article"]
    record = json.loads((cap / "capture.json").read_text())
    assert record["item"] == served


def test_a_denied_targets_url_with_a_comma_is_percent_encoded_in_missing(tmp_path, monkeypatch, fetch):
    """A-2 side note: a `,` inside a `missing=` url is typed as `%2C`."""
    url = "https://example.com/a,b"
    ticket = _ticket(url)
    cap = tmp_path / Path(ticket["capture_dir"])
    cap.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch, "front_door", lambda: [sys.executable, str(tmp_path / "ops_stub.py")])
    _stub_ops(tmp_path, ticket)
    monkeypatch.setattr(fetch, "fetch", lambda *a, **k: (_ for _ in ()).throw(URLError(DENIAL)))
    fetch.run_ticketed(TICKET)
    kv = _kv(_updates(tmp_path)[0])
    assert kv["missing"] == ["example.com,https://example.com/a%2Cb,denied"]
