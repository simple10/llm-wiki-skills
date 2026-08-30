"""`channel-frameio`'s harvest half — emission and per-job capture, no queue verbs.

The unit used to drive a whole share itself — intake, claim, capture,
complete, drain — so a host that dispatched ONE leaf job had no instruction
but "re-run the share flow for it". It now does what every other worker
does: `harvest_share.py` emits one `discovered` row for the host to apply,
and `capture_job.py` captures exactly one dispatched job.

What is worth pinning is the part that is not obvious. The emission must
never hand back a payload larger than the host will accept — an oversized
report is refused WHOLE and every job in the slice goes back to `pending/` —
and on this venue the BYTE bound is the one that bites, because Frame.io
leaf URLs are ~106 bytes each where a substack slug is a dozen. And the
per-job path must refuse a capture dir outside its granted slice BEFORE
fetching, since afterwards the bytes are already down.

Loaded by path: unit scripts live under `skills/<unit>/scripts/` and are
launched with `uv run`, so there is no package to import them from.
"""
import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

UNIT = (Path(__file__).resolve().parents[1]
        / "skills/channel-frameio")
SCRIPTS = UNIT / "scripts"
EMIT = SCRIPTS / "harvest_share.py"
CAPTURE_JOB = SCRIPTS / "capture_job.py"

SHARE = "https://next.frame.io/share/11111111-1111-1111-1111-111111111111"


def _module(path, name=None):
    spec = importlib.util.spec_from_file_location(name or path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))          # sibling imports, as `uv run` does
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SCRIPTS))
    return mod


def _refusal(fn):
    """`fn()`'s SystemExit, or None if it returned.

    Not `pytest.raises`: a mutation that removes a refusal makes that report
    `Failed: DID NOT RAISE`, which is not an AssertionError and so reads as
    a red for the wrong reason. The property is asserted, so it reddens as
    one.
    """
    try:
        fn()
    except SystemExit as exc:
        return exc
    return None


def _leaf(n):
    """A real-shaped leaf: the asset id is a uuid, so every URL is 106 bytes."""
    return f"{SHARE}/view/{n:08d}-2222-3333-4444-555555555555"


def _manifest(tmp_path, count, domain="next.frame.io"):
    path = tmp_path / "tree.json"
    path.write_text(json.dumps({
        "domain": domain, "leaf_count": count,
        "leaves": [{"asset_id": str(i), "name": f"asset-{i}.pdf",
                    "path": ["Top"], "view_url": _leaf(i)}
                   for i in range(count)]}), encoding="utf-8")
    return path


def _emit(monkeypatch, capsys, manifest, *argv):
    mod = _module(EMIT)
    if "--max-urls" not in argv:  # required since the host's ceiling rides the assignment
        argv = (*argv, "--max-urls", "2000")
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(manifest),
                                      "--slug", "w1", "--parent", "j1",
                                      *argv])
    mod.main()
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# the emitter
# --------------------------------------------------------------------------- #

def test_it_emits_one_report_row_and_queues_nothing(monkeypatch, capsys,
                                                    tmp_path):
    out = _emit(monkeypatch, capsys, _manifest(tmp_path, 2))
    assert out["discovered"] == {
        "parent": "j1", "watch_id": "w1", "urls": [_leaf(0), _leaf(1)]}
    assert out["summary"]["emitted"] == 2
    assert out["summary"]["truncated"] is False
    assert out["summary"]["resume_skip"] is None
    # The queue-side counts are gone, not renamed: the host owns them now.
    for gone in ("ok", "failed", "skipped", "drained", "by_url_out"):
        assert gone not in out["summary"]


def test_the_emitter_shells_nothing():
    """A confined worker has no queue and no ledger, so this must not
    reach either.

    Over the AST, not the text: an allow-list of imported top-level modules,
    because a deny-list is what a `from subprocess import run as _shell`
    walks past.
    """
    tree = ast.parse(EMIT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert imported == {"argparse", "json", "sys"}, imported

    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__"}


def test_no_unit_script_names_a_pipeline_queue_script():
    """The seam itself, over every script the unit ships.

    `harvest_share.py` alone could be clean while `drain_pending.py` still
    shelled `job.py claim` — which is exactly the state this issue found, and
    an import allow-list on one file cannot see it. So: every string constant
    in every script, checked for the pipeline's own entry points.
    """
    scripts = sorted(p.name for p in SCRIPTS.glob("*.py"))
    # Exact, not a floor: a floor cannot see a queue-verb script like
    # `drain_pending.py` coming back.
    assert scripts == ["capture_asset.py", "capture_job.py",
                       "capture_record.py", "enumerate_tree.py",
                       "frameio_doc_note.py", "harvest_share.py"]

    named = {}
    for path in SCRIPTS.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for verb in ("intake.py", "job.py"):
                    if node.value.endswith(verb):
                        named.setdefault(path.name, []).append(node.value)
    assert not named, f"unit scripts still name pipeline queue scripts: {named}"


def test_the_emission_stops_at_the_url_ceiling(monkeypatch, capsys, tmp_path):
    """An oversized report is refused WHOLE, so walking a share bigger than
    the host's ceiling and handing it over would cost the slice its work,
    not just the tail."""
    out = _emit(monkeypatch, capsys, _manifest(tmp_path, 40),
                "--max-urls", "25", "--max-bytes", "1000000")
    # Exact, not `<=`: halving the cap emits 12 and a `<=` assertion still
    # passes, pinning "never over" rather than "stops at the ceiling".
    assert out["summary"]["emitted"] == 25
    assert len(out["discovered"]["urls"]) == 25
    assert out["summary"]["bound"] == "urls"
    assert out["summary"]["truncated"] is True
    assert out["summary"]["dropped"] == 15
    assert out["summary"]["resume_skip"] == 25


def test_the_byte_bound_bites_before_the_url_ceiling_on_this_venue(
        monkeypatch, capsys, tmp_path):
    """The whole reason this unit carries a second bound.

    2000 Frame.io leaf URLs are ~220 KiB on their own, and the host refuses
    any report over 256 KiB. So the defaults must truncate a 2000-leaf share
    on BYTES with room left for the slice's job rows — a URL-count bound
    alone (which is all `channel-substack` needs) would emit a payload that
    poisons the whole slice.
    """
    out = _emit(monkeypatch, capsys, _manifest(tmp_path, 2000))
    assert out["summary"]["bound"] == "bytes"
    assert out["summary"]["emitted"] < 2000
    # The row fits under the default with headroom for the rest of a report.
    mod = _module(EMIT)
    assert out["summary"]["row_bytes"] <= mod.MAX_ROW_BYTES
    assert mod.MAX_ROW_BYTES < 256 * 1024  # the host's default report_max_bytes; the script restates no host number
    # …and `row_bytes` is the real serialized size, not an estimate.
    assert out["summary"]["row_bytes"] == len(json.dumps(out["discovered"]))


def test_skip_resumes_a_truncated_share_without_re_emitting(
        monkeypatch, capsys, tmp_path):
    """The point of `resume_skip`. The cap is on the REPORT, not on what
    intake queues, so a second pass from the start drops the same tail
    again and makes no progress at all."""
    manifest = _manifest(tmp_path, 40)
    first = _emit(monkeypatch, capsys, manifest, "--max-urls", "25")
    second = _emit(monkeypatch, capsys, manifest, "--max-urls", "25",
                   "--skip", str(first["summary"]["resume_skip"]))

    assert not (set(first["discovered"]["urls"])
                & set(second["discovered"]["urls"]))
    assert (first["discovered"]["urls"] + second["discovered"]["urls"]
            == [_leaf(i) for i in range(40)])
    assert second["summary"]["truncated"] is False
    assert second["summary"]["skipped_leading"] == 25


def test_parent_is_required(monkeypatch, tmp_path):
    """The host refuses a discovered row whose parent it did not dispatch,
    so a row without one can never apply — the refusal belongs at the call,
    not three stages later in `apply`'s output."""
    mod = _module(EMIT)
    monkeypatch.setattr(sys, "argv", ["harvest_share.py",
                                      str(_manifest(tmp_path, 1)),
                                      "--slug", "w1"])
    with pytest.raises(SystemExit):
        mod.main()


def test_a_manifest_without_leaves_is_refused(monkeypatch, tmp_path):
    """Emitting an empty row for a manifest that is not one reads as a share
    with nothing in it — the host applies it, the share never harvests, and
    nothing anywhere says why."""
    mod = _module(EMIT)
    bad = tmp_path / "not-a-tree.json"
    bad.write_text('{"ok": true, "leaf_count": 3}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["harvest_share.py", str(bad),
                                      "--slug", "w1", "--parent", "j1",
                                      "--max-urls", "2000"])
    # `pytest.raises` would report a missing refusal as `Failed: DID NOT
    # RAISE`, which reads as a non-assertion red under mutation — so the
    # refusal is captured and asserted on.
    refusal = _refusal(mod.main)
    assert refusal is not None and "leaves" in str(refusal), (
        f"emitted a row instead of refusing: {refusal!r}")


# --------------------------------------------------------------------------- #
# the per-job capture path
# --------------------------------------------------------------------------- #

JOB_ID = "aa11bb22cc33"
JOB_URL = f"{SHARE}/view/99999999-9999-9999-9999-999999999999"
#: The capture dir the HOST computes and hands the unit on argv. The unit
#: no longer derives one — `core/watches.capture_dir_for` is the single
#: spelling, so the dir written to and the slice the sandbox granted
#: cannot disagree. Spelled out rather than recomputed with that helper:
#: two hand-written sets compared to each other agree by construction.
CAPTURE_DIR = ("_raw/next.frame.io/"
               "99999999-9999-9999-9999-999999999999--6078c839")


def _assignment(tmp_path, slices=("_raw/next.frame.io",), url=JOB_URL):
    path = tmp_path / "assignment.json"
    path.write_text(json.dumps({
        "v": 1, "session": "s1", "slices": list(slices),
        "jobs": [{"id": JOB_ID, "url": url, "watch_id": "w1",
                  "tags": ["Video"], "areas": ["Craft"], "author": "A",
                  "group": "G", "group_type": "course",
                  "dest": "sources/scrapes/thing"}]}), encoding="utf-8")
    return path


def _fake_run(calls, rc=0, kind="document", stdout=None):
    """A stub that behaves like a SUCCESSFUL `capture_asset.py`.

    Realistic on purpose, even where the test expects the run never to
    happen: a thin stub makes a skipped refusal die on its own output
    instead of on the assertion, which reddens the test for the wrong
    reason and proves nothing (measured — it returned `{}` and the
    slice-grant mutation surfaced as a KeyError rather than as `calls == []`).

    `stdout` replaces that result verbatim, for the shapes where the
    subprocess exits 0 and hands back something the caller cannot use — it
    still writes the bytes first, which is the whole point of that failure.
    """
    def run(cmd):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "document.pdf").write_bytes(b"%PDF-")
        if stdout is not None:
            return rc, stdout, ""
        return rc, json.dumps({"ok": True, "kind": kind, "bytes": 5,
                               "title": "Real Title"}), ""
    return run


def _run_capture_job(monkeypatch, capsys, tmp_path, calls, *, rc=0,
                     kind="document", stdout=None, argv_extra=(),
                     assignment=None):
    mod = _module(CAPTURE_JOB)
    monkeypatch.setattr(mod, "run",
                        _fake_run(calls, rc=rc, kind=kind, stdout=stdout))
    supplied = () if "--capture-dir" in argv_extra else \
        ("--capture-dir", CAPTURE_DIR)
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment",
        str(assignment or _assignment(tmp_path)), "--job-id", JOB_ID,
        *supplied, *argv_extra])
    code = mod.main()
    return code, json.loads(capsys.readouterr().out)


def test_a_dispatched_leaf_becomes_one_row_and_one_capture(
        monkeypatch, capsys, tmp_path):
    calls = []
    code, out = _run_capture_job(monkeypatch, capsys, tmp_path, calls)

    assert code == 0
    # The unit writes where it was TOLD to and reports that same dir back —
    # it composes nothing. This is the value the host resolves against its
    # granted slice.
    rel = CAPTURE_DIR
    # The row carries nothing else: `title`, `verdict` and the asset summary
    # are read out of capture.json by the host, so restating them in the row
    # is a claim it would have to disbelieve.
    assert out["job"] == {"id": JOB_ID, "outcome": "complete",
                          "capture_dir": rel}

    record = json.loads((tmp_path / rel / "capture.json").read_text())
    # The watch's scoping dimensions ride the JOB, and this is the seam that
    # copies them onto the capture — hand-typing them off an assignment is
    # how a capture.json quietly loses them.
    assert record["tags"] == ["Video"]
    assert record["areas"] == ["Craft"]
    assert record["group_type"] == "course"
    assert record["dest"] == "sources/scrapes/thing"
    assert record["id"] == JOB_ID and record["url"] == JOB_URL
    assert record["assets"][0]["local_path"] == "document.pdf"

    # exactly one subprocess, and it is the capture script — no queue verb.
    assert len(calls) == 1
    assert Path(calls[0][2]).name == "capture_asset.py"


def test_a_capture_dir_outside_every_granted_slice_refuses_before_fetching(
        monkeypatch, capsys, tmp_path):
    """The host refuses a path under no granted slice when it applies the
    report — by which time the bytes are already downloaded. Checking it
    here is what makes the refusal cost nothing."""
    mod = _module(CAPTURE_JOB)
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment",
        str(_assignment(tmp_path, slices=("_raw/other.example",))),
        "--job-id", JOB_ID])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"captured into an ungranted slice instead of refusing: {refusal!r}")
    assert calls == [], "nothing may be fetched once the dir is refused"


def test_an_undispatched_job_id_is_refused(monkeypatch, tmp_path):
    """A job nobody dispatched has no row to report: the host would refuse
    one, and a capture written for it lands in a slice it was not granted."""
    mod = _module(CAPTURE_JOB)
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment",
        str(_assignment(tmp_path)), "--job-id", "ffffffffffff"])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"captured an undispatched job instead of refusing: {refusal!r}")
    assert calls == []


def test_a_failed_capture_is_a_fail_row_the_worker_still_reports(
        monkeypatch, capsys, tmp_path):
    calls = []
    code, out = _run_capture_job(monkeypatch, capsys, tmp_path, calls, rc=2)
    assert code == 1
    assert out["job"]["id"] == JOB_ID
    assert out["job"]["outcome"] == "fail"
    assert "capture_asset exit 2" in out["job"]["error"]
    assert "capture_dir" not in out["job"], (
        "a fail row names no capture dir — the host would resolve it")


@pytest.mark.parametrize("stdout, why", [
    ('{"ok": true, "kind": "docum', "truncated json"),
    ('{"ok": true, "kind": "document"}', "no bytes field"),
    ('{"ok": true, "bytes": 5}', "no kind field"),
    ('null', "not an object at all"),
    ('', "nothing on stdout"),
])
def test_an_unusable_capture_result_is_still_a_fail_row(
        monkeypatch, capsys, tmp_path, stdout, why):
    """`rc == 0` is `capture_asset.py`'s word that the bytes are down, not a
    promise about its stdout. Parsing it unguarded made every shape here a
    traceback and NO row — with the asset already on disk — while the
    docstring promises exit 1 and a `fail` row the worker reports.
    """
    calls = []
    code, out = _run_capture_job(monkeypatch, capsys, tmp_path, calls,
                                 stdout=stdout)
    assert code == 1, why
    assert set(out["job"]) == {"id", "outcome", "error"}, why
    assert out["job"]["id"] == JOB_ID
    assert out["job"]["outcome"] == "fail", why
    assert out["job"]["error"], f"a fail row must say why ({why})"
    assert out["summary"]["url"] == JOB_URL
    # The fetch DID happen — that is why the row matters.
    assert len(calls) == 1
    # …and no half-written capture.json is left claiming the capture worked.
    assert not list(tmp_path.rglob("capture.json")), why


@pytest.mark.parametrize("payload, why", [
    ('[{"id": "aa11bb22cc33", "url": "https://x/y"}]', "a JSON list"),
    ('"an assignment"', "a JSON string"),
    ('42', "a JSON number"),
    ('null', "JSON null"),
])
def test_an_assignment_that_is_not_an_object_exits_two(
        monkeypatch, tmp_path, payload, why):
    """`json.loads` succeeding says the bytes were JSON, not that they were
    an assignment. Without the isinstance check these reach `.get("jobs")`
    on a list or a scalar and die on an AttributeError — exit 1 on a
    traceback, which tells a worker to look for a `fail` row on stdout that
    was never printed. That is the confusion the exit contract exists to
    end, so it is exit 2 and no row.
    """
    mod = _module(CAPTURE_JOB)
    bad = tmp_path / "assignment.json"
    bad.write_text(payload, encoding="utf-8")
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment", str(bad),
        "--job-id", JOB_ID])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"{why}: expected exit 2, got {refusal!r}")
    assert calls == [], why


@pytest.mark.parametrize("url, why", [
    (None, "no url key at all"),
    ("", "an empty url"),
    (7, "a url that is not a string"),
    ({"href": "https://x/y"}, "a url that is an object"),
])
def test_a_dispatched_job_without_a_usable_url_exits_two(
        monkeypatch, tmp_path, url, why):
    """A job record is host-written but not therefore well-formed. Without
    the type check, `domain_of_url(None)` and `url.rstrip` die on a
    TypeError or an AttributeError — again exit 1 with no row, and again
    before anything is fetched, so exit 2 is what the contract says.
    """
    mod = _module(CAPTURE_JOB)
    job = {"id": JOB_ID, "watch_id": "w1"}
    if url is not None:
        job["url"] = url
    path = tmp_path / "assignment.json"
    path.write_text(json.dumps({
        "v": 1, "session": "s1", "slices": ["_raw/next.frame.io"],
        "jobs": [job]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment", str(path),
        "--job-id", JOB_ID])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"{why}: expected exit 2, got {refusal!r}")
    assert calls == [], why


def test_an_unreadable_assignment_exits_two_and_fetches_nothing(
        monkeypatch, tmp_path):
    """Exit 1 means "there is a fail row on stdout"; exit 2 means "nothing
    was fetched and there is no row". An uncaught `JSONDecodeError` gave a
    worker the first while meaning the second."""
    mod = _module(CAPTURE_JOB)
    bad = tmp_path / "assignment.json"
    bad.write_text('{"v": 1, "jobs": [', encoding="utf-8")
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment", str(bad),
        "--job-id", JOB_ID])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"a malformed assignment must exit 2, got {refusal!r}")
    assert calls == []


# `test_the_derived_dir_is_stable_and_url_keyed` lived here and went with
# the function it tested: `capture_record.capture_dir_for` was lifted into
# `core/watches.py` so the HOST computes every capture dir, and the stability
# and url-keying it asserted are pinned there now (the runtime suite's
# `test_watches.py`). A unit-side copy would be a second answer to a
# question that must have exactly one.


GRANTED = "_raw/next.frame.io"

#: Family -> the escape that stands for it. Every shape here must NOT be
#: granted, and every one is a raw string prefix of `GRANTED` — asserted
#: below before any of them is used, because a case that is not a prefix
#: match would be refused by the broken implementation too and would prove
#: nothing about the fix.
#:
#: Keyed by FAMILY rather than listed, and the key is checked against the
#: value's SHAPE rather than against a copy of the names: each
#: family is a different reason the shipped check refuses, so a family
#: that is dropped, or one kept by name and handed another family's
#: value, has stopped being measured either way. Dropping the two symlink
#: entries is the specific loss that would leave `resolve()`
#: interchangeable with `normpath()`.
#:
#: The names carry that classification, and `_classify_escape` reads it
#: back off the path. `traversal-*` reaches past the grant with a `..`;
#: `symlink-*` with a symlinked component, the mechanism `normpath` would
#: NOT close; `*-out` lands outside the venue area entirely where
#: `*-sideways` lands in another venue's slice beside the grant;
#: `-absent` means nothing is on disk where it lands.
#:
#: Every target here is absent on disk except `traversal-out`'s, and that
#: is deliberate in both directions. `resolve()` is non-strict, which is
#: what lets the legitimate case pass at all — the host creates the
#: capture dir AFTER this check — so the refusing direction has to be
#: exercised against absent targets too, or a later edit could leave the
#: coverage accidental; `traversal-out-absent` is the family that keeps
#: that exercised where nothing on the way there exists either. It can
#: only BE that family while its sibling lands somewhere real, which is
#: why the fixture puts a `.git/` on disk.
ESCAPES_BY_FAMILY = {
    # up and out of the content tree, into the real `.git/` every wiki has
    "traversal-out": "_raw/next.frame.io/../../.git/hooks",
    # up and out, with nothing on disk anywhere it lands
    "traversal-out-absent": "_raw/next.frame.io/../../nowhere/x",
    # up and sideways, into another venue's slice
    "traversal-sideways": "_raw/next.frame.io/../other.example/a--1",
    # a symlinked component pointing out of the wiki
    "symlink-out": "_raw/next.frame.io/outbound/a--1",
    # a symlinked component pointing into another slice
    "symlink-sideways": "_raw/next.frame.io/sideways/a--1",
    # a different domain whose name merely starts with the granted one
    "prefix-sibling": "_raw/next.frame.iox/a--1",
}
ESCAPES = list(ESCAPES_BY_FAMILY.values())

#: Every family the two axes can name: each mechanism crossed with where
#: it lands, plus the traversal that lands nowhere and the sibling that
#: needs no mechanism at all. Generated from the axes rather than listed,
#: so what is pinned is the classification space and not six strings.
ESCAPE_FAMILIES = {
    f"{mechanism}-{direction}"
    for mechanism in ("traversal", "symlink")
    for direction in ("out", "sideways")
} | {"traversal-out-absent", "prefix-sibling"}


def _grant_fixture(tmp_path):
    """`(root, slices)` — one granted slice, one ungranted sibling slice,
    and BOTH symlink shapes leading out of the grant.

    The wiki root is a subdir of `tmp_path` so that "outside the wiki
    entirely" is a real directory rather than a path that merely does not
    exist — `Path.resolve()` treats those differently and the test would
    otherwise not measure what it says.
    """
    root = tmp_path / "wiki"
    (root / "_raw/next.frame.io").mkdir(parents=True)
    (root / "_raw/other.example").mkdir(parents=True)
    # A wiki is a git checkout, so `.git/` is on disk in every real one.
    # It is also what makes `traversal-out` land in a real directory,
    # which is the whole difference between it and `traversal-out-absent`
    # — without it the two families are the same shape and the guard
    # below cannot tell them apart.
    (root / ".git/hooks").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "_raw/next.frame.io/outbound").symlink_to(
        outside, target_is_directory=True)
    # Sideways, into another venue's slice: still under `_raw/`, still
    # inside the wiki, and so the shape that crosses a grant boundary
    # without looking anything like an escape.
    (root / "_raw/next.frame.io/sideways").symlink_to(
        root / "_raw/other.example", target_is_directory=True)
    return root, [GRANTED]


def _under(path, ancestor):
    """Component-wise containment between two RESOLVED absolute paths."""
    return path == ancestor or ancestor in path.parents


def _classify_escape(root, rel):
    """Which family `rel` IS, read off its shape against a real tree.

    The family names encode a mechanism and a landing site, and this reads
    both back out of the path rather than trusting the key:

      * the mechanism — a `..` component, a symlinked component
        (`resolve()` diverging from the lexical collapse `normpath()`
        gives), or neither, which leaves only a sibling whose NAME extends
        the grant's;
      * where it lands — beside the grant in the venue area
        (`sideways`) or outside that area altogether (`out`), with
        `-absent` when nothing is on disk there at all.

    Derived against `_grant_fixture`'s real root, because a symlinked
    component is not visible in the string and neither is what exists.
    """
    base = root.resolve()
    grant = (base / GRANTED).resolve()
    venues = grant.parent          # `_raw/` — one child per claimed domain
    target = base / rel
    resolved = target.resolve()
    lexical = Path(os.path.normpath(target))
    assert not _under(resolved, grant), (
        f"{rel!r} resolves INSIDE the grant, so it is not an escape at all")
    if ".." in Path(rel).parts:
        mechanism = "traversal"
    elif resolved != lexical:
        mechanism = "symlink"
    else:
        return "prefix-sibling"
    direction = "sideways" if _under(resolved, venues) else "out"
    landing = "" if resolved.parent.exists() else "-absent"
    return f"{mechanism}-{direction}{landing}"


def test_every_escape_is_the_shape_its_family_name_claims(tmp_path):
    """The corpus, pinned by SHAPE instead of by value.

    Two properties, and between them they replace the three value facts
    the earlier rounds accumulated — the spelled-out family names, the
    literal count, and the distinctness line:

      * the corpus covers exactly the families the axes can name, so one
        cannot be dropped and a seventh cannot be invented; and
      * each value really is the shape its family claims, classified
        against a real tree, so a family retired into ANOTHER family's
        shape is red rather than green.

    The second is what no earlier round had, and it is the case that kept
    reappearing. Measured, `37 passed` for each of
    four retirements: `prefix-sibling` given a second traversal's shape,
    `symlink-out` given a distinct traversal, `symlink-sideways` given a
    second prefix sibling, and `traversal-out-absent` given a traversal
    whose target EXISTS — that last silently dropping the non-strict
    resolution property the corpus comment spends a paragraph on. Each of
    the four classifies as some OTHER family, which is the point of
    classifying rather than counting.

    Vacuity is still guarded here too, and separately, because a guard
    against vacuity that is itself vacuous is the sharper half of the same
    defect. With `ESCAPES = []` the loops
    below iterate nothing, the in-loop refusals iterate nothing, and the
    parametrized refusal test degrades to a SKIP rather than a red —
    measured, `23 passed, 1 skipped`. And a case that is not a string
    prefix of the granted slice would be refused by the BROKEN
    implementation too, so it would prove nothing about the fix.
    """
    root, _ = _grant_fixture(tmp_path)

    assert set(ESCAPES_BY_FAMILY) == ESCAPE_FAMILIES, (
        "an escape family was added or lost — every family is a different "
        "reason the check refuses, so losing one silently stops measuring it")
    for family, rel in ESCAPES_BY_FAMILY.items():
        assert _classify_escape(root, rel) == family, (
            f"the {family!r} slot holds a "
            f"{_classify_escape(root, rel)!r} shape — that family is no "
            "longer measured, and another one is measured twice")

    # `ESCAPES` is what the parametrize and the loops below read, so it has
    # to BE the map's values: trimming it independently is the shape that
    # empties the corpus without touching the families above, and nothing
    # in the classification can see it.
    assert ESCAPES == list(ESCAPES_BY_FAMILY.values())

    for rel in ESCAPES:
        assert rel.startswith(GRANTED), rel


def test_a_granted_slice_is_a_resolved_path_not_a_string_prefix(tmp_path):
    """The property the check exists for, in general rather than for one
    shape.

    A slice grant is about where the BYTES land, so the comparison is
    between RESOLVED absolute paths, by path component. Two families walked
    past the prefix test this replaces, and a third is closed structurally
    rather than by the `/` that test happened to append:

      * `..` — `Path.as_posix()` does not collapse it, so
        `_raw/<granted>/../../.git/hooks` reads as granted. Measured True on
        the pre-fix tip.
      * a symlinked component — the path reads as granted the whole way
        down and the write lands wherever the link points, either out of
        the wiki or sideways into a slice this worker was not given. This
        is the family `os.path.normpath` would NOT have closed: it
        collapses `..` lexically and follows no link.
      * a prefix sibling — `_raw/next.frame.iox` is a different domain's
        slice, and so is a granted string that merely extends the dir. The
        old `startswith(s + "/")` refused this one already; the component
        comparison is what makes refusing it structural rather than a
        consequence of where the separator landed.
    """
    mod = _module(CAPTURE_JOB)
    root, granted = _grant_fixture(tmp_path)

    # Controls first: without them a blanket `return False` scores green.
    assert mod.in_granted_slice(root, Path("_raw/next.frame.io/a--1"), granted)
    assert mod.in_granted_slice(root, Path(GRANTED), granted)
    # Non-strict resolution in the GRANTING direction: nothing below the
    # slice exists yet, because the host creates the capture dir after this
    # check says yes.
    assert mod.in_granted_slice(root, Path("_raw/next.frame.io/x/y/z"), granted)

    for rel in ESCAPES:
        assert not mod.in_granted_slice(root, Path(rel), granted), rel
    # The symlinks themselves, not only a child of one.
    for rel in ("_raw/next.frame.io/outbound", "_raw/next.frame.io/sideways"):
        assert not mod.in_granted_slice(root, Path(rel), granted), rel
    # The mirror image: a grant that merely extends the dir's name.
    assert not mod.in_granted_slice(
        root, Path("_raw/next.frame.io/a--1"), ["_raw/next.frame.io.evil"])
    # An absolute dir ignores the root entirely, so it can never be under it.
    assert not mod.in_granted_slice(root, Path("/etc"), granted)
    # And a traversing GRANT widens nothing: the host writes one
    # `_raw/<netloc>` per claimed domain, so anything else is a broken
    # assignment rather than a bigger permission.
    assert not mod.in_granted_slice(
        root, Path("_raw/other.example/a--1"), ["_raw/next.frame.io/.."])


@pytest.mark.parametrize("capture_dir", ESCAPES)
def test_an_escaping_capture_dir_refuses_before_fetching(
        monkeypatch, tmp_path, capture_dir):
    """The reachable shape: `--capture-dir` is caller-supplied — it is in
    this skill's own `argument-hint` — so a worker forwarding what it was
    handed is how these reach the check at all.

    Asserted on the exit code and on `calls`, never on a substring of the
    refusal: that message quotes paths, and `tmp_path` is named after the
    test function, so a substring assertion can pass on words the message
    never said.
    """
    mod = _module(CAPTURE_JOB)
    root, _ = _grant_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(root), "--assignment",
        str(_assignment(tmp_path)), "--job-id", JOB_ID,
        "--capture-dir", capture_dir])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"captured outside the grant instead of refusing: {refusal!r}")
    assert calls == [], "nothing may be fetched once the dir is refused"


def test_an_assignment_granting_no_slices_refuses_rather_than_capturing(
        monkeypatch, tmp_path):
    """Guarding the containment check with `if slices and ...` skips it
    entirely for an assignment carrying no `slices` — a fetch into anywhere
    on the strength of a field being absent.

    No branch of its own does the refusing: the containment check is
    unconditional and an empty grant matches nothing, so the refusal comes
    from the same line every other out-of-slice path hits. The dedicated
    `if not slices:` this file used to pin was measured redundant in the
    re-review — same exit 2, same empty `calls` — and deleting it beat
    testing it.

    Refusing is what the host does: `harvest_apply.py` reads `slices` back
    off the assignment and calls it `bad_assignment` unless it is a
    non-empty list of `_raw/<domain>`, so an empty grant is a broken
    assignment and never an unconstrained one.
    """
    mod = _module(CAPTURE_JOB)
    calls = []
    monkeypatch.setattr(mod, "run", _fake_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "capture_job.py", str(tmp_path), "--assignment",
        str(_assignment(tmp_path, slices=())), "--job-id", JOB_ID])
    refusal = _refusal(mod.main)
    assert refusal is not None and refusal.code == 2, (
        f"captured under an empty grant instead of refusing: {refusal!r}")
    assert calls == []


# --------------------------------------------------------------------------- #
# the extension a per-job capture has no name for
# --------------------------------------------------------------------------- #

def test_the_document_extension_falls_back_to_the_proxy_route():
    """A dispatched leaf job carries a URL and nothing else, so `--name` is
    absent on the per-job path; with no other source of the extension,
    every document landed as `document.bin`. The signed conversion route
    spells it."""
    mod = _module(SCRIPTS / "capture_asset.py", "capture_asset_probe")
    doc = ("https://assets.frame.io/pptx/abc123/pptx_proxy.pptx"
           "?signature=deadbeef&x=1")
    assert mod.DOC_PROXY_RE.search(doc), "the fixture must be a real match"

    # The per-job path: no name, and the route answers.
    assert mod.document_ext(None, doc) == "pptx"
    # `--name` still wins where a caller has the leaf manifest — it is the
    # ORIGINAL filename, and the route names only what Frame.io converted to.
    assert mod.document_ext("Deck Q3.PPTX", doc) == "pptx"
    assert mod.document_ext("notes.pdf", doc) == "pdf"
    # A name without an extension is not one: fall through to the route.
    assert mod.document_ext("Deck Q3", doc) == "pptx"
    # And nothing is invented where neither source carries an extension.
    assert mod.document_ext(None, "https://assets.frame.io/x/y/z?sig=1") == "bin"
