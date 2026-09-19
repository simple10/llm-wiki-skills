"""`channel-frameio`'s scripts — what holds whatever drives them.

The unit has been driven three ways: its own queue loop, then one bounded
`discovered` row for a host to fan out into per-leaf jobs, and now one ticket
that captures the whole share itself (`tests/test_port_frameio.py` pins that
flow, end to end through the real extractor). This file keeps what none of
those changed: the unit ships exactly its own scripts and names nobody else's,
the listing-card and proxy-route parsing, and the capture record's shape.

Loaded by path: unit scripts live under `skills/<unit>/scripts/` and are
launched with `uv run`, so there is no package to import them from.
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

UNIT = (Path(__file__).resolve().parents[1]
        / "skills/channel-frameio")
SCRIPTS = UNIT / "scripts"

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


# --------------------------------------------------------------------------- #
# the seam: the unit's own scripts, and nobody else's
# --------------------------------------------------------------------------- #

#: What a unit script may import: the stdlib it uses, its PEP 723
#: dependencies, and the one sibling module. Nothing of the plugin's.
ALLOWED_IMPORTS = {
    "argparse", "fnmatch", "hashlib", "json", "re", "shutil", "subprocess",
    "sys", "time", "datetime", "pathlib", "urllib",
    # `os`/`signal`: a child with a deadline is killed by process GROUP, and
    # `report.json` is written through `os.replace`. `unicodedata`: stdlib,
    # for folding a title's Unicode form in the page-name rule (APFS folds it).
    "os", "signal", "unicodedata",
    "playwright", "httpx", "pptx", "openpyxl", "pypdf",
    "capture_record",
}


def test_unit_scripts_import_nothing_of_the_plugins():
    """Over the AST, not the text: an allow-list of imported top-level
    modules, because a deny-list is what a `from x import y as _z` walks
    past. A unit reaches the machinery by verb, never by import."""
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        assert imported <= ALLOWED_IMPORTS, (path.name, imported - ALLOWED_IMPORTS)

        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not called & {"eval", "exec", "compile", "__import__"}, path.name


def test_no_unit_script_names_a_script_it_does_not_ship():
    """The seam itself, over every script the unit ships.

    Every string constant in every script, checked for the entry points of a
    host that no longer exists — a unit that still shells `job.py claim` or
    waits on `harvest_apply.py` is one that does not run.
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
                # Docstrings are history and may name what left; a constant
                # whose LAST path component is one of these is a path somebody
                # runs. By component, not suffix: `capture_job.py` is ours.
                for verb in ("intake.py", "job.py", "harvest_apply.py",
                             "scaffold.py", "drain_pending.py", "watch.py",
                             "assignment.json"):
                    if node.value.rsplit("/", 1)[-1] == verb:
                        named.setdefault(path.name, []).append(node.value)
    assert not named, f"unit scripts still name a retired host's files: {named}"


# --------------------------------------------------------------------------- #
# the listing: card text and share URLs
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, is_folder, name", [
    ("pg. 12\nDeck Q3.pdf", False, "Deck Q3.pdf"),          # a page-count badge first
    ("01:23:45\nKeynote.mov\nStatus", False, "Keynote.mov"),  # a duration badge first
    ("12:30\nUntitled recording", False, "Untitled recording"),  # no extension: first non-badge line
    ("Day 1\n14 Items", True, "Day 1"),                      # a folder is its first line
    ("   ", False, ""),
])
def test_a_leaf_cards_name_is_the_filename_not_the_badge(text, is_folder, name):
    mod = _module(SCRIPTS / "enumerate_tree.py", "enumerate_tree_probe")
    assert mod.card_name(text, is_folder) == name


def test_a_share_id_is_read_off_a_root_a_folder_or_a_leaf_and_nothing_else():
    mod = _module(SCRIPTS / "enumerate_tree.py", "enumerate_tree_probe")
    share = "11111111-1111-1111-1111-111111111111"
    for url in (SHARE, f"{SHARE}/22222222-2222-2222-2222-222222222222", f"{SHARE}/view/abc"):
        assert mod.share_id_of(url) == share
    with pytest.raises(SystemExit):
        mod.share_id_of("https://f.io/AbCdEfGh")   # a short link names no share id


def test_folder_and_leaf_urls_are_built_on_the_given_urls_own_origin():
    """Not a hardcoded `next.frame.io`: a slice is granted the manifest's
    `*.frame.io` plus the TARGET's own host, so that is the host to build on."""
    mod = _module(SCRIPTS / "enumerate_tree.py", "enumerate_tree_probe")
    assert mod.origin_of(f"{SHARE}/22222222-2222-2222-2222-222222222222?x=1") == "https://next.frame.io"
    assert mod.origin_of("https://app.frame.io/share/11111111-1111-1111-1111-111111111111") == "https://app.frame.io"
    for bad in ("next.frame.io/share/x", "file:///etc/passwd", "javascript:alert(1)"):
        with pytest.raises(SystemExit):
            mod.origin_of(bad)
    # A card's id is venue text on its way into a URL.
    assert mod.ASSET_ID_RE.match("0a1b2c3d-0000-4000-8000-000000000000")
    for bad in ("", "../x", "a/b", "a?b", "a b", "x" * 65):
        assert not mod.ASSET_ID_RE.match(bad), bad
    source = (SCRIPTS / "enumerate_tree.py").read_text(encoding="utf-8")
    assert 'f"https://next.frame.io' not in source


# --------------------------------------------------------------------------- #
# the capture record
# --------------------------------------------------------------------------- #

def test_the_capture_record_is_the_extractors_shape_and_owns_no_lifecycle_key():
    """`slug,item,title,body,content_type,fetched_at` are text the extractor
    type-checks; `frontmatter` is scalars and flat lists, and never a key
    another verb owns — a fetched title must not get to set a page's status."""
    mod = _module(SCRIPTS / "capture_record.py")
    url = f"{SHARE}/view/abc"
    record = mod.capture_record(
        slug="talks", item=url, title="Deck", body="page.md", content_type="text/markdown",
        frontmatter={"type": "doc", "status": "published", "title": "x", "resource": "y", "harvested": {"at": 1},
                     "path": ["Share", {"nested": 1}, "Decks"], "author": "", "bytes": 5, "blob": {"a": 1}})

    assert list(record) == ["v", "slug", "item", "title", "body", "content_type", "fetched_at", "frontmatter"]
    for field in ("slug", "item", "title", "body", "content_type", "fetched_at"):
        assert isinstance(record[field], str), field
    assert record["frontmatter"] == {"type": "doc", "path": ["Share", "Decks"], "bytes": 5}
    assert "dest" not in record, "a download ticket carries no dest and a harvest slice cannot write one"


def test_asset_facts_read_the_ids_and_hosts_off_the_leaf_url():
    mod = _module(SCRIPTS / "capture_record.py")
    facts = mod.asset_facts(kind="document", url=f"{SHARE}/view/abc-123", name="Deck.pptx", ext="pptx",
                            size=9, path_bits=["Share", "Decks"], group="Speaker Decks", group_type="course")
    assert facts == {
        "type": "doc", "source_host": ["next.frame.io", "frame.io"],
        "share_id": "11111111-1111-1111-1111-111111111111", "asset_id": "abc-123",
        "original_name": "Deck.pptx", "ext": "pptx", "bytes": 9, "path": ["Share", "Decks"],
        "group": "Speaker Decks", "group_type": "course"}


def test_title_strip_trims_a_share_suffix_but_never_to_nothing():
    mod = _module(SCRIPTS / "capture_record.py")
    assert mod.strip_title("Deck One - Big Share", " - Big Share") == "Deck One"
    assert mod.strip_title("Big Share", "Big Share") == "Big Share"
    assert mod.strip_title("  Deck One  ", None) == "Deck One"


# --------------------------------------------------------------------------- #
# the extension a nameless capture has no other source for
# --------------------------------------------------------------------------- #

def test_the_document_extension_falls_back_to_the_proxy_route():
    """A ticket whose target is itself a leaf viewer has a URL and no leaf
    manifest, so `--name` is absent; with no other source of the extension,
    every document landed as `document.bin`. The signed conversion route
    spells it."""
    mod = _module(SCRIPTS / "capture_asset.py", "capture_asset_probe")
    doc = ("https://assets.frame.io/pptx/abc123/pptx_proxy.pptx"
           "?signature=deadbeef&x=1")
    assert mod.DOC_PROXY_RE.search(doc), "the fixture must be a real match"

    # No name, and the route answers.
    assert mod.document_ext(None, doc) == "pptx"
    # `--name` still wins where a caller has the leaf manifest — it is the
    # ORIGINAL filename, and the route names only what Frame.io converted to.
    assert mod.document_ext("Deck Q3.PPTX", doc) == "pptx"
    assert mod.document_ext("notes.pdf", doc) == "pdf"
    # A name without an extension is not one: fall through to the route.
    assert mod.document_ext("Deck Q3", doc) == "pptx"
    # And nothing is invented where neither source carries an extension.
    assert mod.document_ext(None, "https://assets.frame.io/x/y/z?sig=1") == "bin"
