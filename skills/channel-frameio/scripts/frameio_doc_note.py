# /// script
# requires-python = ">=3.10"
# dependencies = ["python-pptx>=0.6", "openpyxl>=3.1", "pypdf>=4"]
# ///
"""Render a captured Frame.io document (pdf/pptx/xlsx/…) as the page body.

platform: frameio
scope: platform-general. A Frame.io document capture is not an HTML page —
it is a downloaded file plus `meta.json` — and the generic extractor takes a
capture's `.md` body VERBATIM, so everything this venue knows about a document
has to be in that body before harvest ends. This is the step that puts it
there. It runs at HARVEST time, inside the leaf's own capture dir:

  <leaf-dir>/page.md        the body: the venue's own title as the H1, the
                            folder breadcrumb, a compact facts block naming
                            the original file, and the document's text under
                            a collapsed callout
  <leaf-dir>/capture.json   names `page.md` as the body, and carries the same
                            facts as a `frontmatter` object

The facts are in BOTH places on purpose. The extractor writes its own fixed
frontmatter (`title`, `status`, `resource`, `harvested`) and ignores the
`frontmatter` object today, so the facts block is what keeps them on the page;
the object is there for the day it is merged. `page.md` never opens with a
`---` block — the extractor prepends its own, and a second one corrupts the
page.

It writes nothing outside the leaf dir. The old builder copied the file into
`<dest>/assets/docs/` and wrote a note under `<dest>/pages/`; a harvest slice
cannot write `dest`, and a download ticket does not even carry it.

**The body does NOT point at the captured file, and must not be "fixed" to.**
It names the original file (the `File:` fact) and carries its text; it links
nothing under `_raw/`, because a committed page never links into `_raw/` — that
tree is machine-local and prunable, so the link is dead on every other clone
and on this one after a prune. The regression against the old builder is real
and is stated here plainly: the PDF/PPTX ITSELF stays in `_raw/<slug>/<leaf>/`
on the capturing machine, and only its extracted text reaches the page. The
host's `bundle_media` copies media files only, not documents (plugins issue
simple10/llm-wiki-plugins#2117, item 9); until it does, that is the whole of
what travels.

**The title is written twice, differently.** `capture.json`'s `title` names the
page's FILE, so it is `safe_title()` of the venue's (`capture_record()` does
it: no `/`, `:`, `?`…, no leading dot, one line). The H1 keeps the venue's own
title, folded to one line; where the two differ the true one is also
`frontmatter.source_title`.

Text is extracted for pdf (pypdf), pptx and xlsx. Extraction is best effort:
a failure is said in the body, never raised, because the file itself is
captured either way. The three imports are lazy, so a format that needs none
of them runs without them.

Usage:
  uv run frameio_doc_note.py <leaf-dir> [--slug <slug>] [--url <view-url>]
      [--name=<original filename>] [--path=<folder> ...] [--crumb-skip=N]
      [--group "<bundle name>"] [--group-type <kind>] [--author NAME]
      [--title-strip "<suffix>"]

`--url` and `--name` default from the `meta.json` `capture_asset.py` wrote;
`--slug` defaults to the leaf dir's parent name (`_raw/<slug>/<leaf>`).
`--title-strip` trims a share-wide suffix off the title (share viewers often
append the share's own name to each asset title); omit it to keep titles as
captured. `--crumb-skip` is how many leading `--path` folders the breadcrumb
leaves off: the driver passes 1 when every leaf of the share carries the same
top folder and 0 when the top folder tells leaves apart; by hand it defaults
to 1. Pass venue text as `--name=<v>`: a name starting with `-` is an option
to argparse otherwise.

Prints {"dir", "body", "title", "ext", "extracted_chars"}.

History:
  2026-07-15  created — batch speaker-slides share (25 docs, no page.md).
  2026-07-29  packaged into the channel-frameio skill unit.
  2026-08-20  `--notes-dir` defaulted from the `dest` on capture.json.
  2026-09-19  ported to the ticket contract: renders `page.md` + `capture.json`
              into the leaf's capture dir at harvest instead of a note under
              `dest` at process — no unit process step is ever invoked. The
              `<root>` positional, `--notes-dir`, `--force`, the asset copy,
              the YAML frontmatter and the TODO-SUMMARY placeholder went with
              the note; `tags`/`areas` went because a ticket carries neither.
              PDFs get their text inlined too, since no agent reads them later.
  2026-09-19  review fixes: `capture.json`'s title is filename-safe and one
              line (a display name with a `/` failed the process ticket after
              harvest said ok); `document.pdf` is preferred over a stray
              `document.bin`; `--crumb-skip`; the docstring no longer promises
              a pointer into `_raw/` that the body rightly does not carry.
"""

import argparse
import json
import sys
from pathlib import Path

from capture_record import (
    CAPTURE_NAME,
    META_NAME,
    asset_facts,
    capture_record,
    name_stem,
    one_line,
    read_json,
    strip_title,
    write_json,
)

BODY_NAME = "page.md"

#: Cap on inlined text. A page is read by people and by search; a 300-page
#: deck's every word is neither, and the file itself is still in the capture.
MAX_EXTRACT_CHARS = 200_000


def extract_pdf_text(path):
    from pypdf import PdfReader

    with open(path, "rb") as handle:
        pages = [(page.extract_text() or "").strip() for page in PdfReader(handle).pages]
    return "\n\n".join(f"**Page {i}**\n{text}" for i, text in enumerate(pages, 1) if text)


def extract_pptx_text(path):
    from pptx import Presentation

    prs = Presentation(path)
    slides = []
    for i, slide in enumerate(prs.slides, 1):
        lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs).strip()
                    if t:
                        lines.append(t)
        slides.append(f"**Slide {i}**\n" + "\n".join(lines))
    return "\n\n".join(slides)


def extract_xlsx_text(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for name in wb.sheetnames:
        ws = wb[name]
        out.append(f"**Sheet: {name}** ({ws.max_row} rows x {ws.max_column} cols)")
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 40), values_only=True):
            if any(c is not None for c in row):
                out.append(" | ".join("" if c is None else str(c) for c in row))
        if ws.max_row > 40:
            out.append(f"... ({ws.max_row - 40} more rows)")
    return "\n".join(out)


EXTRACTORS = {"pdf": extract_pdf_text, "pptx": extract_pptx_text, "xlsx": extract_xlsx_text}


def extracted_text(path: Path, ext: str) -> str:
    """The document's text, or a one-line note saying why there is none."""
    reader = EXTRACTORS.get(ext)
    if reader is None:
        return ""
    try:
        text = reader(path)
    except Exception as e:  # noqa: BLE001 — best effort; the file is captured either way
        return f"(extraction failed: {type(e).__name__}: {e})"
    if len(text) > MAX_EXTRACT_CHARS:
        text = text[:MAX_EXTRACT_CHARS] + f"\n\n(truncated at {MAX_EXTRACT_CHARS} characters — the captured file has the rest)"
    return text


#: Extensions a captured document is known to land with, in the order one is
#: preferred when a leaf dir holds several `document.*`. `bin` is what
#: `capture_asset.py` names a file it could not type — and it sorts FIRST.
DOC_EXTS = ("pdf", "pptx", "xlsx", "docx", "ppt", "xls", "doc", "key", "numbers", "pages", "mht", "txt", "csv")


def pick_document(docs, name=None):
    """The one `document.<ext>` to render: the extension the asset's own name
    carries, else the first known document extension, else by name with
    `document.bin` last. None when there is none."""
    wanted = name.rsplit(".", 1)[-1].lower() if isinstance(name, str) and "." in name else None

    def rank(path):
        ext = path.suffix.lstrip(".").lower()
        known = DOC_EXTS.index(ext) if ext in DOC_EXTS else len(DOC_EXTS)
        return (ext != wanted, known, ext == "bin", path.name)

    return min(docs, key=rank, default=None)


def _plain(value) -> str:
    """One fact on one line: a captured title is data, never markup."""
    return one_line(value).replace("`", "'")


def render_body(*, title, url, facts, orig_name, breadcrumb, extracted) -> str:
    """`page.md`: no YAML block, facts up top, text last."""
    lines = [f"# {_plain(title)}", ""]
    if breadcrumb:
        lines += [f"*{_plain(breadcrumb)}*", ""]
    lines.append(f"- **Type:** {facts.get('type', 'doc')}")
    lines.append(f"- **Source:** <{url}>")
    lines.append(f"- **File:** `{_plain(orig_name)}` ({facts.get('ext', '?')}, {facts.get('bytes', '?')} bytes)")
    for label, key in (("Author", "author"), ("Group", "group"), ("Group type", "group_type")):
        if facts.get(key):
            lines.append(f"- **{label}:** {_plain(facts[key])}")
    if extracted:
        lines += ["", "> [!note]- Extracted text"]
        lines += [f"> {line}" if line else ">" for line in extracted.splitlines()]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("leaf_dir", type=Path, help="the leaf's capture dir, holding document.<ext> and meta.json")
    ap.add_argument("--slug", default=None, help="the job's slug (default: the leaf dir's parent name)")
    ap.add_argument("--url", default=None, help="the leaf's view URL (default: meta.json's url)")
    ap.add_argument("--name", default=None, help="the asset's original filename (default: meta.json's name)")
    ap.add_argument("--path", action="append", default=None, help="folder breadcrumb bit, repeatable")
    ap.add_argument("--crumb-skip", type=int, default=1, help="leading --path folders every leaf of the share carries, left off the breadcrumb (default 1)")
    ap.add_argument("--group", default=None)
    ap.add_argument("--group-type", dest="group_type", default=None)
    ap.add_argument("--author", default=None)
    ap.add_argument(
        "--title-strip", default=None, help="suffix to trim off captured titles (e.g. the share's own name)"
    )
    args = ap.parse_args()

    leaf_dir = args.leaf_dir
    meta = read_json(leaf_dir / META_NAME) or {}
    src_path = pick_document([p for p in leaf_dir.glob("document.*") if p.is_file()], args.name or meta.get("name"))
    if src_path is None:
        sys.exit(f"{leaf_dir} holds no document.<ext> — capture_asset.py writes one for a document asset")
    ext = src_path.suffix.lstrip(".").lower()
    url = args.url or meta.get("url")
    if not isinstance(url, str) or not url:
        sys.exit(f"no --url and no `url` in {leaf_dir / META_NAME} — a capture with no item is one nothing can find")
    resolved = leaf_dir.resolve()
    slug = args.slug or resolved.parent.name
    path_bits = list(args.path or [])

    # capture_asset.py names every doc `document.<ext>`; the real filename is
    # the manifest's, carried on meta.json only when the capture was given it.
    orig_name = one_line(args.name or meta.get("name") or "") or src_path.name
    # The venue's own title, on ONE line — it is the H1. What names the page's
    # file is `capture_record()`'s safe form of it.
    title = one_line(strip_title(meta.get("title") or "", args.title_strip)) or name_stem(orig_name)

    facts = asset_facts(
        kind="document",
        url=url,
        name=orig_name,
        ext=ext,
        size=src_path.stat().st_size,
        path_bits=path_bits,
        author=args.author,
        group=args.group,
        group_type=args.group_type,
    )
    extracted = extracted_text(src_path, ext)
    # `path_bits[0]` is the first folder BELOW the enumerated URL — never the
    # share's own name, which `enumerate_tree.py` keeps apart as `root_title`.
    # Where every leaf of the share carries the same one it distinguishes
    # nothing and the breadcrumb starts below it (`--crumb-skip=1`); where the
    # share's root lists several folders it is exactly what tells two leaves
    # apart, and the driver — which sees the whole manifest — passes 0.
    breadcrumb = " / ".join(path_bits[max(0, args.crumb_skip):])
    body = render_body(
        title=title,
        url=url,
        facts=facts,
        orig_name=orig_name,
        breadcrumb=breadcrumb,
        extracted=extracted,
    )
    # Body first, record second: a `capture.json` is the claim that the body
    # it names is there.
    (leaf_dir / BODY_NAME).write_text(body, encoding="utf-8")
    record = capture_record(
        slug=slug, item=url, title=title, body=BODY_NAME, content_type="text/markdown", frontmatter=facts,
        fallback=facts.get("asset_id") or "Untitled",
    )
    write_json(leaf_dir / CAPTURE_NAME, record)
    print(
        json.dumps(
            {
                "dir": f"_raw/{slug}/{resolved.name}",
                "body": BODY_NAME,
                "title": record["title"],
                "ext": ext,
                "extracted_chars": len(extracted),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
