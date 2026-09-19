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

Plus the small utilities the per-leaf path is built from: `run()`,
`source_hosts_for()`, `strip_title()`, `read_json()`/`write_json()`.

What left, and why: the old record carried the host's job fields (`id`,
`tags`, `areas`, `dest`, a `verdict` block). A download ticket carries none
of those — `dest` is null on it and a harvest slice cannot write there — so
nothing here reads or writes them.
"""

import hashlib
import json
import re
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


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def read_json(path: Path):
    """The JSON object in `path`, or None when it is absent or not one."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict) -> None:
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


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


def leaf_dir_name(view_url: str, name=None, path_bits=()) -> str:
    """`<slug>--<hash8>` for one leaf: one path component, stable across runs.

    The slug is for a person reading `_raw/<slug>/`; the hash is the
    identity. `path_bits[0]` is the shared top folder every leaf of a share
    carries, so it is left out of the slug's 60-character budget, and a leaf
    with no name falls back to its asset id.
    """
    stem = name_stem(name)
    bits = [b for b in [*list(path_bits)[1:], stem] if b]
    if not bits:
        bits = [leaf_ids(view_url)[1] or "asset"]
    return f"{slugify(bits)}--{hash8(view_url)}"


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


def capture_record(*, slug: str, item: str, title, body: str, content_type: str, frontmatter: dict) -> dict:
    """The `capture.json` dict for one captured Frame.io asset.

    `item` is the leaf's view URL: it becomes the page's `resource`, which is
    what the next ticket's `known[]` names, so it must be the same string the
    leaf planner compares against.
    """
    return {
        "v": 1,
        "slug": slug,
        "item": item,
        "title": title or None,
        "body": body,
        "content_type": content_type,
        "fetched_at": now_utc(),
        "frontmatter": clean_frontmatter(frontmatter),
    }
