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
import re
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


def jsonc(text: str) -> object:
    """JSON with `//` line comments, which the sandbox snippets carry. A `//`
    inside a string (a url in a description) is not a comment."""
    out, in_string, i = [], False, 0
    while i < len(text):
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\":
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                break
            continue
        else:
            out.append(c)
        i += 1
    return json.loads("".join(out))


def snippet(reference: str) -> str:
    """The one fenced `jsonc` block of a sandbox reference: the profile a wiki
    sandbox is written from."""
    fences = re.findall(r"^```jsonc\n(.*?)^```$", reference, re.M | re.S)
    assert len(fences) == 1, f"a sandbox reference carries {len(fences)} jsonc blocks, not one"
    return fences[0]


def bound(ops: list, env: dict, wiki: Path, name: str) -> None:
    """Every stage of the installed unit that names a `sandbox_ref`, bound the
    way `/llm-wiki:sandbox` and `/llm-wiki:enable` do it: the reference read
    through the CLI, its snippet written and committed as a wiki sandbox, that
    sandbox enabled, the stage bound. A snippet key the policy reader does not
    admit fails here, at `sandboxes enable`."""
    ops_dir = run(ops, rooted(env, wiki), "--json", "whereami").data["wiki"]["ops_dir"]
    for stage, spec in unit_manifest(name)["stages"].items():
        ref = spec.get("sandbox_ref")
        if not ref:
            continue
        package, rel = ref.rsplit(":", 1)
        r = run(ops, rooted(env, wiki), "--json", "packages", "reference", package, f"references/sandboxes/{rel}.md")
        assert r.returncode == 0, r.stdout + r.stderr
        sandbox = f"{name}-{stage}"
        template = wiki / ops_dir / "sandboxes" / f"{sandbox}.jsonc"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(snippet(r.data["text"]), encoding="utf-8")
        for verb in (
            ["git", "commit", str(template.relative_to(wiki)), f"message=sandboxes: {sandbox} from {ref}"],
            ["sandboxes", "enable", sandbox, "--confirm"],
            ["skills", "bind", name, f"stage={stage}", f"sandbox={sandbox}", "--confirm"],
        ):
            r = run(ops, rooted(env, wiki), "--json", *verb)
            assert r.returncode == 0, r.stdout + r.stderr


def enabled(ops: list, env: dict, wiki: Path, name: str) -> None:
    """The unit, installed, bound and enabled in the session wiki — by
    whichever case gets there first. Each verb is a no-op over an identical
    copy, so a case that needs the unit asks for it rather than leaning on
    another having run (`-k`, `--lf`, a shuffled or split run)."""
    r = run(ops, rooted(env, wiki), "--json", "skills", "install", name)
    assert r.returncode == 0, r.stdout + r.stderr
    bound(ops, env, wiki, name)
    r = run(ops, rooted(env, wiki), "--json", "skills", "enable", name, "--confirm")
    assert r.returncode == 0, r.stdout + r.stderr


@dataclass
class Job:
    slug: str
    dest: str
    record: dict


def declared_job(ops: list, env: dict, wiki: Path, unit: str, target: str, *extra: str, slug: str | None = None) -> Job:
    """A real job for `unit` in the session wiki, declared the way
    references/enable.md says to (A-11: `jobs add`/`jobs show`) — `pipeline
    extract` reads the job a capture belongs to, so a
    capture with no job behind it is refused. Idempotent for one
    (slug, target) pair; a wiki holds ONE job per target and a slug names one
    source for good, so a case wanting a job of its own passes both."""
    enabled(ops, env, wiki, unit)
    slug = slug or f"port-{unit}"
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "add", target, f"slug={slug}", f"skill={unit}", f"description=port: {unit}", *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    record = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "show", slug).data["job"]
    return Job(slug, record["dest"], record)


def live_ticket(ops: list, env: dict, wiki: Path, job: Job) -> tuple[str, Path]:
    """A real ticket, minted and moved to `active/` by the CLI — never a hand
    fixture (side note; A-8, A-9). `jobs claim <slug>` leases the job and
    mints its harvest ticket, pending; `tickets run <id> spawn=self` moves it
    to `active/` under THIS SESSION's own worker id, matched against
    `LLM_WIKI_SESSION_ID` (`conftest.py`'s `env` fixture) the way `open`
    checks it against `env.current().session`. Returns the id and its
    capture directory, the latter read back through `open` (A-1) rather than
    guessed off `run`'s own answer shape."""
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "jobs", "claim", job.slug)
    assert r.returncode == 0, r.stdout + r.stderr
    claimed = next(c for c in r.data["claimed"] if c["slug"] == job.slug)
    ticket_id = claimed["tickets"][0]["id"]
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "run", ticket_id, "spawn=self")
    assert r.returncode == 0, r.stdout + r.stderr
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "open", ticket_id)
    assert r.returncode == 0, r.stdout + r.stderr
    return ticket_id, wiki / r.data["ticket"]["capture_dir"]


def landed(ops: list, env: dict, wiki: Path, ticket: str) -> dict:
    """The ticket, closed by the host (A-10) — the real `close`, reading
    whatever `update` the worker last posted and routing it."""
    r = run(ops, rooted(env, wiki), "--json", "pipeline", "tickets", "close", ticket)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.data



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
