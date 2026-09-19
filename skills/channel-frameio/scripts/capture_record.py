"""Shared helpers for the channel-frameio scripts (imported sibling module).

Not run directly: `harvest_share.py`, `capture_job.py` and
`frameio_doc_note.py` import it — `uv run` puts the script's own directory on
sys.path, so a within-unit sibling import is the sanctioned way to share code
inside a skill unit (stdlib only; never import plugin or package modules).

Holds the two file contracts this unit has with the pipeline, in one place,
so a second caller can never drift from them:

  - `capture_record()` — the `capture.json` dict the generic extractor reads:
    `slug, item, title, body, content_type, fetched_at` as text, plus the
    `frontmatter` object of this unit's exact facts. The extractor ignores
    `frontmatter` today, so the same facts also go in the `page.md` body.
  - `leaf_dir_name()` — `<slugified path and name>--<hash8 of the view url>`,
    the same shape the host's own namer gives a capture dir. The host has no
    verb for it, so this unit composes that shape itself for every leaf it
    captures beside the ticket's own capture dir.

  - `settle_titles()` — the page-name rule. The extractor files a page under
    its TITLE and overwrites what is there, so two assets of one share with
    one name (`Brief.pdf` in two folders) would be ONE page; the driver calls
    this before every report, and a later namesake is retitled
    `<title> (<folder breadcrumb>)` in its own `capture.json`.

  - `safe_title()` — the title the page's FILE can be named from. Applied
    inside `capture_record()`, where a title is first written, so no caller
    can write a raw one — and BEFORE `settle_titles()`, because two titles
    differing only in a refused character collide only once both are safe.

Plus the small utilities the per-leaf path is built from: `run()` (a child
with a deadline, killed with everything it started), `source_hosts_for()`,
`strip_title()`, `one_line()`, `read_json()`/`write_json()` (atomic).

What left, and why: the old record carried the host's job fields (`id`,
`tags`, `areas`, `dest`, a `verdict` block). A download ticket carries none
of those — `dest` is null on it and a harvest slice cannot write there — so
nothing here reads or writes them.
"""

import hashlib
import json
import os
import re
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

CAPTURE_NAME = "capture.json"
TICKET_NAME = "ticket.json"
REPORT_NAME = "report.json"
META_NAME = "meta.json"

#: Frontmatter keys another verb owns. A unit that writes one into its
#: `frontmatter` object is asking the extractor to let a fetched page decide
#: a page's lifecycle, so they are dropped here rather than trusted to callers.
OWNED_KEYS = frozenset({"status", "document_id", "document_revision", "harvested", "extracted", "title", "resource"})

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_LEAF_RE = re.compile(r"/share/([0-9a-f-]{36})/view/([^/?#]+)")

#: The host's own cap on the slug half of a capture dir name.
LEAF_SLUG_MAX = 60


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: `run()`'s exit code for a child that outlived its deadline — timeout(1)'s.
TIMED_OUT = 124


def run(cmd, timeout=None):
    """`(exit, stdout, stderr)` of one child, which gets `timeout` seconds.

    A slice is killed at thirty minutes and a killed slice reports nothing, so
    no child may run open-ended. The child is started in its OWN process group
    and the whole group is killed when the deadline passes: `uv run` is a
    parent of the script, which is a parent of yt-dlp, and killing only the
    first leaves the download running. Exit `TIMED_OUT`, with `timeout` in
    stderr, is how a caller reads it. Callers hand an inner child a SHORTER
    deadline than their own, so the innermost expires first and every level
    gets to say what happened.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # already gone
            proc.kill()
        out, err = proc.communicate()
        note = f"timeout: killed after {timeout:.0f}s"
        return TIMED_OUT, (out or "").strip(), f"{(err or '').strip()}\n{note}".strip()
    return proc.returncode, out.strip(), err.strip()


def inner_deadline(seconds, margin: float = 10.0):
    """The deadline a child's own child gets: shorter, so it expires first."""
    return None if seconds is None else max(1.0, float(seconds) - margin)


def read_json(path: Path):
    """The JSON object in `path`, or None when it is absent or not one."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict) -> None:
    """Atomically: a temp file beside `path`, then `os.replace`. A slice can be
    killed at any instant, and half a `report.json` is no report at all."""
    path = Path(path)
    scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        scratch.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def host_of(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def source_hosts_for(host: str) -> list:
    """`www.a.com` -> `["www.a.com", "a.com"]` — progressively broader hosts so
    a query for either matches. No Public Suffix List; `co.uk` as a trailing
    entry is a correct host and a useless filter value, which is the cheaper
    trade."""
    if not host:
        return []
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(max(1, len(labels) - 1))]


def leaf_ids(url: str):
    """`(share_id, asset_id)` off a leaf viewer URL, or `(None, None)`."""
    m = _LEAF_RE.search(url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def slugify(parts, max_len: int = LEAF_SLUG_MAX, fallback: str = "item") -> str:
    folded = _NON_SLUG.sub("-", "-".join(str(p) for p in parts).lower()).strip("-")
    return folded[:max_len].rstrip("-") or fallback


def hash8(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def name_stem(name) -> str:
    """A display name without its extension. Not `Path(name).stem`: a card's
    name is venue text, and one with a `/` in it is still one name."""
    return _EXT_RE.sub("", (name or "").strip())


def shared_top(paths) -> int:
    """How many leading folders EVERY leaf of a manifest carries: 1 or 0.

    `enumerate_tree.py` starts a leaf's `path` empty at the URL it was given
    and appends each folder it walks into, so `path[0]` is the first folder
    BELOW the target — never the share's own name (that is `root_title`). A
    share whose root lists one folder puts that folder on every leaf, where it
    tells nothing apart and is left off breadcrumbs, dir names and title
    qualifiers. A root listing several folders makes `path[0]` exactly what
    tells two leaves apart, and it is kept. (Which of the two a share's root
    shows is the sharer's choice; unverified beyond the shares seen so far.)
    """
    tops = {(tuple(p)[:1]) for p in paths}
    return 1 if len(tops) == 1 and tops != {()} else 0


def leaf_dir_name(view_url: str, name=None, crumb=()) -> str:
    """`<slug>--<hash8>` for one leaf: one path component, stable across runs.

    The slug is for a person reading `_raw/<slug>/`; the hash is the
    identity. `crumb` is the leaf's folders below any top folder the whole
    share has in common (`shared_top`), which would only spend the slug's
    60-character budget; a leaf with no name falls back to its asset id.
    """
    stem = name_stem(name)
    bits = [b for b in [*list(crumb), stem] if b]
    if not bits:
        bits = [leaf_ids(view_url)[1] or "asset"]
    return f"{slugify(bits)}--{hash8(view_url)}"


def one_line(value) -> str:
    """Venue text as ONE line: newlines, tabs and control characters become a
    space. A title or a fact value that kept its newline could open a heading,
    a rule or a list item in a body the extractor takes verbatim."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())


# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "'", "<": "(", ">": ")"}
TITLE_MAX = 120  # characters
TITLE_MAX_BYTES = 200  # UTF-8 bytes: the host checks no length, and a filename is capped in BYTES (255 on
#                        ext4/APFS) with `.md` appended — 100 CJK characters is 300 bytes and the REAL extractor
#                        dies `OSError: [Errno 36] File name too long` (measured, channel-youtube)


def safe_title(text, fallback="Untitled"):
    text = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))  # control chars, newlines, tabs
    for bad, good in _TITLE_SWAPS.items():
        text = text.replace(bad, good)
    text = " ".join(text.split()).lstrip(". ").rstrip(" .")
    cut = text[:TITLE_MAX]
    while len(cut.encode("utf-8")) > TITLE_MAX_BYTES:
        cut = cut[:-1]
    if cut != text:
        text = cut.rstrip(" .-") + "…"
    return text or fallback


def strip_title(title: str, suffix) -> str:
    """Trim a share-wide suffix off a captured title; never down to nothing."""
    title = (title or "").strip()
    if suffix:
        title = title.split(suffix)[0].strip() or title
    return title


def clean_frontmatter(facts: dict) -> dict:
    """Scalars and flat lists only, no empty values, no key another verb owns."""
    out = {}
    for key, value in facts.items():
        if key in OWNED_KEYS or value in (None, "", []):
            continue
        if isinstance(value, (list, tuple)):
            value = [v for v in value if isinstance(v, (str, int, float, bool))]
            if not value:
                continue
        elif not isinstance(value, (str, int, float, bool)):
            continue
        out[key] = value
    return out


def asset_facts(*, kind: str, url: str, name, ext, size, path_bits, author=None, group=None, group_type=None) -> dict:
    """This unit's exact facts about one captured asset — the `frontmatter`
    object, and the source of the facts block a document's `page.md` opens
    with. `kind` is capture_asset.py's own word: `video` or `document`."""
    share_id, asset_id = leaf_ids(url)
    return clean_frontmatter(
        {
            "type": "video" if kind == "video" else "doc",
            "source_host": source_hosts_for(host_of(url)),
            "share_id": share_id,
            "asset_id": asset_id,
            "original_name": name,
            "ext": ext,
            "bytes": size,
            "path": list(path_bits or []),
            "author": author,
            "group": group,
            "group_type": group_type,
        }
    )


def capture_record(
    *, slug: str, item: str, title, body: str, content_type: str, frontmatter: dict, fallback: str = "Untitled"
) -> dict:
    """The `capture.json` dict for one captured Frame.io asset.

    `item` is the leaf's view URL: it becomes the page's `resource`, which is
    what the next ticket's `known[]` names, so it must be the same string the
    leaf planner compares against.

    `title` is the VENUE's, as captured; what is written is `safe_title()` of
    it — never null, because the extractor falls back from a null title to the
    body's own H1, which is the raw one. Where the two differ the true title
    rides in `frontmatter` as `source_title`, on one line.
    """
    true = one_line(title)
    safe = safe_title(true, fallback=safe_title(fallback))
    facts = dict(frontmatter)
    if true and true != safe:
        facts["source_title"] = true
    return {
        "v": 1,
        "slug": slug,
        "item": item,
        "title": safe,
        "body": body,
        "content_type": content_type,
        "fetched_at": now_utc(),
        "frontmatter": clean_frontmatter(facts),
    }


# ---------------------------------------------------------------- page names
#
# KEEP IN SYNC with the host. `pipeline extract` names a page FILE from the
# capture's title and writes it with no existence check (llm-wiki-ops
# `commands/pipeline/extract.py::_capture_to_page` -> `pipeline/pages.py::
# name_for` -> `page/note.py::filename_for`): the filename is
# `title.strip() + ".md"` — nothing folded, nothing dropped; a title carrying
# one of `ILLEGAL` or a control character is REFUSED, not altered — and a
# capture with no title is filed under its body's stem (`page`). So two leaves
# of one run whose titles differ only in outer whitespace are ONE page, the
# second overwriting the first; on a filesystem that folds case (macOS,
# Windows) so are two that differ only in that. `page_key` folds both: a
# needless qualifier costs nothing, an overwritten page is lost. NOT folded:
# Unicode form (NFC/NFD), which the same filesystems also fold — stdlib has it
# only in `unicodedata`, and one venue spelling one title two ways is rare.
# Duplicated per unit on purpose — units install one by one, nothing is shared.

TITLE_ILLEGAL = '/\\:*?"<>|'  # `page/note.py::ILLEGAL`
QUALIFIER_MAX = 60


def page_key(title: str) -> str:
    """What two titles share when they make one page file."""
    return title.strip().casefold()


def qualifier(text) -> str:
    """Venue text made safe inside a title: one line, capped, and none of the
    characters the host refuses a title for."""
    if not isinstance(text, str):
        return ""
    safe = "".join("-" if (char in TITLE_ILLEGAL or ord(char) < 32) else char for char in text)
    return " ".join(safe.split())[:QUALIFIER_MAX].strip(" -.")


def unique_title(title: str, qualifiers, taken: dict) -> str:
    """`title`, untouched, when no leaf before this one makes its filename;
    else `title (<qualifier>)` with the first qualifier that tells it apart.

    `taken` maps a `page_key` to the qualifiers of the leaf holding it, and the
    answer is claimed in it. A qualifier the holder shares distinguishes
    nothing and is passed over; callers end the list with the leaf's hash8,
    which no other leaf has, and a counter closes it, so the answer is always
    free. A title this already qualified is free on the next pass and comes
    back as it is — re-running never renames a leaf a second time.
    """
    given = list(dict.fromkeys(q for q in map(qualifier, qualifiers) if q))
    chosen = title
    holder = taken.get(page_key(title))
    if holder is not None:
        shared = {page_key(q) for q in holder}
        options = [q for q in given if page_key(q) not in shared]
        base = title.strip()
        chosen = next((f"{base} ({q})" for q in options if page_key(f"{base} ({q})") not in taken), None)
        stem, n = (f"{base} ({options[-1]})" if options else base), 2
        while chosen is None:
            if page_key(f"{stem} ({n})") not in taken:
                chosen = f"{stem} ({n})"
            n += 1
    taken[page_key(chosen)] = given
    return chosen


def leaf_qualifiers(leaf: dict) -> list:
    """What tells this asset from a namesake: the folders it sits in, below
    any top one every leaf of the share carries (the planner's `crumb` — the
    same breadcrumb a document's `page.md` shows, joined with ` - ` because a
    title cannot carry a `/`), then the hash of its view URL. A leaf with no
    `crumb` (a plan written before there was one) reads as it did then."""
    crumb = leaf.get("crumb") if isinstance(leaf.get("crumb"), list) else (leaf.get("path") or [])[1:]
    bits = [b for b in crumb if isinstance(b, str) and b.strip()]
    # Capped in BYTES here, because `qualifier()` caps only characters and the
    # qualified title still has to be a filename (`fitted_title`).
    return [_cut_bytes(" - ".join(bits), CRUMB_MAX_BYTES), hash8(leaf["item"])]


CRUMB_MAX_BYTES = 80


def _cut_bytes(text: str, limit: int) -> str:
    while len(text.encode("utf-8")) > limit:
        text = text[:-1]
    return text


def fitted_title(title: str, qualifiers, taken: dict) -> str:
    """`unique_title()`, and the answer still fits a filename.

    `safe_title()` caps a title in bytes, and a qualifier appended afterwards
    puts a long one back over the cap — the extractor then dies on `File name
    too long`. The BASE is what gives way: ` (<qualifier>)` is what tells the
    page from its namesake, so it is kept whole and the base is cut, with an
    ellipsis, to what is left of `TITLE_MAX_BYTES`. Done here, at this unit's
    call site, so the shared block above stays the shared block. A title that
    was fitted once fits on the next pass and comes back as it is.
    """
    probe = unique_title(title, qualifiers, dict(taken))
    if probe == title or len(probe.encode("utf-8")) <= TITLE_MAX_BYTES:
        return unique_title(title, qualifiers, taken)
    base = title.strip()
    suffix = probe[len(base):]
    room = max(8, TITLE_MAX_BYTES - len(suffix.encode("utf-8")) - len("…".encode("utf-8")))
    stem = _cut_bytes(base, room).rstrip(" .-") + "…"
    chosen, n = f"{stem}{suffix}", 2
    while page_key(chosen) in taken:
        chosen, n = f"{stem}{suffix} ({n})", n + 1
    given = list(dict.fromkeys(q for q in map(qualifier, qualifiers) if q))
    # The unfitted spelling is claimed as well: the next namesake's probe then
    # finds this qualifier taken and moves on to its own, as it would have.
    taken[page_key(probe)] = given
    taken[page_key(chosen)] = given
    return chosen


def settle_titles(root: Path, leaves) -> None:
    """One page per leaf: in manifest order the first leaf to make a filename
    keeps its title, and a later one is retitled in its own `capture.json`.

    In the driver's report step and not in `capture_job.py`, because a title
    is the captured page's own — unknown until the viewer has been opened —
    and one capture sees one leaf; the driver sees all of them, on every
    pass, before anything is extracted. A title already qualified is free on
    the next pass, so a later pass renames nothing a second time.
    """
    taken: dict = {}
    for leaf in leaves:
        directory = Path(root) / leaf["dir"]
        record = read_json(directory / CAPTURE_NAME)
        if not record or not isinstance(record.get("body"), str) or not (directory / record["body"]).is_file():
            continue
        title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
        held = title or Path(record["body"]).stem
        final = fitted_title(held, leaf_qualifiers(leaf), taken)
        if final != held:
            record["title"] = final
            write_json(directory / CAPTURE_NAME, record)
