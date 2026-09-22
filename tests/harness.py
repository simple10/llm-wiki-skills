"""The package harness, as plain code: every test runs the REAL ops CLI
against a throwaway wiki, with this checkout served as the package. The
fixtures are `conftest.py`'s; everything a test imports is here, so each
module loads once (pytest loads `conftest` itself as a plugin, and a
`from conftest import` under importlib mode would load a second copy).

    LLM_WIKI_OPS   the ops CLI to run (a command line; default: `llm-wiki-ops`
                   on PATH). Absent → the install tests skip, saying so.

The checkout is symlinked as `marketplaces/<owner>/<repo>` under a tmp
packages home, which the CLI treats as a developer's clone: `latest` is
this checkout's HEAD — commit before you run — and nothing fetches.
`LLM_WIKI_PACKAGES_OFFLINE` is set, so nothing here touches the network.
"""

from __future__ import annotations

import ast
import atexit
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "llm-wiki-package.json").read_text(encoding="utf-8"))
SOURCE = MANIFEST["repository"]
SKILLS = [a["name"] for a in MANIFEST["artifacts"] if a["type"] == "skill"]


def unit_manifest(name: str) -> dict:
    art = next(a for a in MANIFEST["artifacts"] if a["type"] == "skill" and a["name"] == name)
    return json.loads((ROOT / art["path"] / "manifest.json").read_text(encoding="utf-8"))


def _ops_argv() -> list | None:
    spec = os.environ.get("LLM_WIKI_OPS")
    if spec:
        return shlex.split(spec)
    exe = shutil.which("llm-wiki-ops")
    return [exe] if exe else None


def _cli(ops: list) -> list:
    """The machine CLI, out of the same command line. `init` is the one verb
    outside both scopes — it acts on a directory that is not a wiki yet, so it
    has no root to be dispatched with — and both scripts ship in one
    distribution, so only the last word differs. A path-spelled last word
    keeps its directory: the dispatch sets `LLM_WIKI_OPS` to the console
    script by ABSOLUTE path (the plugins' `env` contract), and `which` answers
    one too, and that directory need not be on PATH."""
    last = ops[-1]
    return [*ops[:-1], str(Path(last).with_name("llm-wiki-cli")) if os.sep in last else "llm-wiki-cli"]


# `run()` starts every call here, never in pytest's cwd: the CLI is root-bound
# and the cwd's owner must agree with `LLM_WIKI_ROOT`'s, so a checkout that
# sits INSIDE a wiki would have every `rooted()` call refused at the first
# fixture, as an opaque error. A directory nobody owns binds nothing.
NEUTRAL_CWD = Path(tempfile.mkdtemp(prefix="llm-wiki-harness-cwd-"))
atexit.register(shutil.rmtree, NEUTRAL_CWD, ignore_errors=True)


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def data(self):
        return json.loads(self.stdout)


def run(ops: list, env: dict, *args, cwd=None) -> Result:
    cp = subprocess.run([*ops, *args], env=env, cwd=cwd or NEUTRAL_CWD, capture_output=True, text=True, check=False)
    return Result(cp.returncode, cp.stdout, cp.stderr)


def rooted(env: dict, wiki: Path) -> dict:
    """The environment that binds a call to `wiki`. The CLI is root-bound and
    no verb takes a wiki argument: `LLM_WIKI_ROOT` names the wiki a caller
    standing outside it means, and it must be ABSOLUTE. A `run` child owns its
    whole argv, so that one verb binds by `cwd=` instead — and a case that
    stands inside the wiki may pass both, because they agree."""
    return {**env, "LLM_WIKI_ROOT": str(Path(wiki).resolve())}


def enabled(ops: list, env: dict, wiki: Path, name: str) -> None:
    """The unit, installed and enabled in the session wiki — by whichever case
    gets there first. Both verbs are no-ops over an identical copy, so a case
    that needs the unit asks for it rather than leaning on another having run
    (`-k`, `--lf`, a shuffled or split run)."""
    for verb in (["skills", "install", name], ["skills", "enable", name, "--confirm"]):
        r = run(ops, rooted(env, wiki), "--json", *verb)
        assert r.returncode == 0, r.stdout + r.stderr


@dataclass
class Job:
    slug: str
    dest: str
    record: dict


def declared_job(ops: list, env: dict, wiki: Path, unit: str, target: str, *extra: str, slug: str | None = None) -> Job:
    """A real job for `unit` in the session wiki, declared the way
    references/enable.md says to — `pipeline extract` reads the job a capture
    belongs to, so a
    capture with no job behind it is refused. Idempotent for one
    (slug, target) pair; a wiki holds ONE job per target and a slug names one
    source for good, so a case wanting a job of its own passes both."""
    enabled(ops, env, wiki, unit)
    slug = slug or f"port-{unit}"
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "add", target, f"slug={slug}", f"skill={unit}", f"description=port: {unit}", *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    record = run(ops, rooted(env, wiki), "--json", "pipeline", "show", slug).data["job"]
    return Job(slug, record["dest"], record)


def ticket_in(wiki: Path, job: Job, leaf: str, *, unit: str, item: str, **over) -> Path:
    """A capture directory holding the `ticket.json` a download worker is
    started beside — every key `pipeline/dispatch.py` writes, the job's own
    sections riding along. Returns the directory; `leaf` is `<page>--<hash8>`
    for an item with an address, `<YYYY-MM-DD>` for a channel's pull."""
    rel = f"_raw/{job.slug}/{leaf}"
    directory = wiki / rel
    directory.mkdir(parents=True, exist_ok=True)
    ticket = {
        "v": 1, "ticket": "0123456789ab", "unit": unit, "slug": job.slug, "item": item, "target": item,
        "capture_dir": rel, "dest": None, "hosts": [], "harvest": job.record["harvest"],
        "options": job.record.get("options") or {}, "credential": None, "min_date": None, "known": [],
    }
    ticket.update(over)
    (directory / "ticket.json").write_text(json.dumps(ticket, indent=1), encoding="utf-8")
    return directory


def extracted(ops: list, env: dict, wiki: Path, capture_dir: Path) -> list:
    """The REAL extractor over one capture — the pages it wrote, as paths. The
    whole point of a unit's harvest is that this works on what it left."""
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "extract", str(capture_dir.relative_to(wiki)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.data["pages"] or r.data["ledgers"], r.data
    return [wiki / rel for rel in [*r.data["pages"], *r.data["ledgers"]]]



def unit_tests(unit: str, module: str) -> dict:
    """A shipped test module's namespace — helpers, constants, imports —
    without its tests. A unit's tests ship with it (`skills/<unit>/tests/`)
    and know nothing of this harness; the harness tier for that unit lives
    here and reads exactly as it did beside them, by taking their names.
    `test_*` stays out, or pytest would collect those cases a second time;
    so does everything the module merely imported (the stdlib, pytest), which
    a harness file imports for itself — a name borrowed from another file's
    import list is a NameError the day that file stops needing it."""
    path = ROOT / "skills" / unit / "tests" / f"{module}.py"
    spec = importlib.util.spec_from_file_location(f"_unit_tests_{unit}_{module}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    imported = {
        alias.asname or alias.name.split(".")[0]
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    return {k: v for k, v in vars(mod).items() if not k.startswith("__") and not k.startswith("test_") and k not in imported}
