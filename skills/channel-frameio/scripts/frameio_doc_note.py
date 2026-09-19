# /// script
# requires-python = ">=3.10"
# dependencies = ["python-pptx>=0.6", "openpyxl>=3.1", "pypdf>=4"]
# ///
"""PROCESS a captured Frame.io leaf into the page under the ticket's dest.

platform: frameio
scope: platform-general. This is the unit's own process step — the reason the
unit keeps one. A Frame.io document capture is not an HTML page: it is a
downloaded file plus `meta.json`, and nothing generic can turn a PDF into a
readable page. So this reads the bytes harvest left under `capture_dir` and
writes ONE page under `dest`:

  <capture-dir>/document.<ext>   what harvest downloaded, the only input
  <capture-dir>/capture.json     harvest's flat record: the settled title,
                                 the item url, the body's name
  <capture-dir>/meta.json        what the harvest knew: the venue's own title,
                                 the original filename, the folder path, the
                                 operator's author/group/title-strip
  <dest>/<title>.md              the page, written through the front door
  <capture-dir>/page.md          a document's body as it was written, kept
                                 beside the capture so a retried ticket is
                                 comparable. A video's page has no body
  <capture-dir>/report.json      LAST, naming the page in `written[]`

**The page is written by this script, as a subprocess with an argv LIST and
never a shell line.** Every value on that line is venue text — a title, a
filename, a folder name — and a venue that can type onto a Bash line can run a
command:

    llm-wiki-ops page create "title=<safe title>" "dest=<dest>" \
        "resource=<view url>" "type=doc" "extracted=true" --stdin

with the body on stdin. On the one refusal that means this job already landed
the page (`<path> already exists — the filename is the title`, exit 2) it runs
`page edit <dest>/<title>.md` with the same keys instead. Any other non-zero
status ABORTS before `report.json` is written: a report that claimed a page
nobody wrote would be read as work done.

`title` is `capture.json`'s, which harvest already made filename-safe and
settled against its namesakes (`capture_record.py::safe_title`,
`settle_titles`), so `<dest>/<title>.md` IS the page's path. The H1 keeps the
venue's own spelling off `meta.json`.

**The body does NOT point at the captured file, and must not be "fixed" to.**
It names the original file (the `File:` fact) and carries its text; it links
nothing under `_raw/`, because a committed page never links into `_raw/` — that
tree is machine-local and prunable, so the link is dead on every other clone
and on this one after a prune. The PDF/PPTX ITSELF stays in
`_raw/<slug>/<leaf>/` on the capturing machine, and only its extracted text
reaches the page: `bundle_media` is a MEDIA key, and a document is not media.

**A video leaf gets the transcribe stage's stub**, not a page about the video:
an EMPTY body, `extracted=queued` and `media=` wiki-relative — pointing under
`<dest>/assets/` when the ticket's `process.bundle_media` is true (this step
makes that copy; the host's own no longer sees these pages) and at
`<capture_dir>/video.mp4` when it is false. MEASURED against ops 1.88.3 that `page create` takes both as
ordinary `key=value`, and that `pipeline/media.py::queued_under` then finds the
page — so the stub is the unit's to mint, and no page about a video is written
that the recording would never be transcribed behind. The only link to give
would be a signed HLS URL that dies within hours anyway.

Text is extracted for pdf (pypdf), pptx and xlsx. Extraction is best effort:
a failure is said in the body, never raised, because the file itself is
captured either way. The three imports are lazy, so a format that needs none
of them runs without them.

Usage:
  llm-wiki-ops run ops/skills/channel-frameio/scripts/frameio_doc_note.py . \
      --capture-dir <capture_dir> --dest <dest>
      [--stage process] [--item <view-url>] [--name=<original filename>]
      [--path=<folder> ...] [--crumb-skip=N] [--title-strip "<suffix>"]
      [--author NAME] [--group "<bundle name>"] [--group-type <kind>]
      [--skip "<reason>"]

The positional is the WIKI ROOT — `.`, because `run` starts a script there —
and it is what binds the nested front door to this wiki. `--capture-dir` and
`--dest` are the ticket's own, verbatim. Everything else defaults off
`meta.json`, which harvest wrote; pass it only for a hand run over a capture
harvest did not record. `--skip "<reason>"` writes the `skipped` report and no
page — it is how the agent records a capture that `process.exclude_rules`,
`options` or `min_date` ruled out. `--stage` is accepted and must be `process`
where it is given: this script is the process step and has no other mode.

Pass venue text as `--name=<v>`: a name starting with `-` is an option to
argparse otherwise.

Prints {"dir", "written", "title", "ext", "extracted_chars"}.

History:
  2026-07-15  created — batch speaker-slides share (25 docs, no page.md).
  2026-07-29  packaged into the channel-frameio skill unit.
  2026-08-20  `--notes-dir` defaulted from the `dest` on capture.json.
  2026-09-19  folded into harvest: rendered `page.md` + `capture.json` into the
              leaf's capture dir, because no process ticket reached a unit.
  2026-09-19  review fixes: `capture.json`'s title is filename-safe and one
              line; `document.pdf` is preferred over a stray `document.bin`;
              `--crumb-skip`; the docstring no longer promises a pointer into
              `_raw/` that the body rightly does not carry.
  2026-09-19  the unit's two steps restored: this is the PROCESS step again.
              It takes the wiki root, `--capture-dir` and `--dest`, writes the
              page through `page create`/`page edit` itself, and writes
              `report.json` naming it in `written[]`.
  2026-09-19  a video leaf gets a page too — the transcribe stage's queued
              stub, minted here. It was reported `skipped` for a day, on the
              reading that only `pipeline extract` could set `extracted` and
              `media`; measuring `page create` showed otherwise.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from capture_record import (
    CAPTURE_NAME,
    META_NAME,
    OPS,
    REPORT_NAME,
    TICKET_NAME,
    asset_facts,
    front_door_env,
    is_media,
    name_stem,
    one_line,
    read_json,
    strip_title,
    write_json,
)

BODY_NAME = "page.md"

#: `pipeline/pages.py`'s own frontmatter vocabulary, which `page create` takes
#: as ordinary `key=value` (measured against ops 1.88.3). `FLAG` is a STRING,
#: never a boolean: a page still reading `false` is picked up again. `QUEUED`
#: plus a non-empty `MEDIA` is exactly what `pipeline/media.py::queued_under`
#: reads as an item for the transcribe stage.
FLAG = "extracted"
EXTRACTED = "true"
QUEUED = "queued"
MEDIA = "media"

#: `page create`'s one refusal that means "edit it instead". Matched on the
#: tail, which is fixed prose; the head is the path.
EXISTS = "already exists"

# The note format requires a `type` on every page, and `page create` defaults a
# missing one to `note`. A share holds documents and videos, and nothing else.
TYPE_DOC = "doc"
TYPE_VIDEO = "video"

# Where `process.bundle_media: true` puts the media, relative to `dest`. The
# process slice is granted rw on `dest`, so the copy is this step's to make;
# `pipeline extract` used to make it and no longer sees these pages.
ASSETS = "assets"

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


def _plain(value) -> str:
    """One fact on one line: a captured title is data, never markup."""
    return one_line(value).replace("`", "'")


def render_body(*, title, url, facts, orig_name, breadcrumb, extracted) -> str:
    """The page body: no YAML block, facts up top, text last."""
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


# ------------------------------------------------------------ the front door


def write_page(wiki: Path, dest: str, title: str, keys, body: str) -> str:
    """The page under `dest`, through the front door. Returns it, wiki-relative.

    `page create` first, `page edit` on the one refusal that means the title is
    already a page here. The arguments are an argv LIST and `shell=False`:
    every value on them is venue text — a title, a filename, a folder name —
    and a venue that can type onto a Bash line can run a command.
    """
    ops = shutil.which(OPS)
    if ops is None:
        sys.exit(
            f"frameio_doc_note: `{OPS}` is not on PATH — the front door is how "
            "this unit writes a page; install the ops plugin on this machine"
        )
    where = {"cwd": str(wiki), "env": front_door_env()}
    created = subprocess.run(
        [ops, "--json", "page", "create", f"title={title}", f"dest={dest}", *keys, "--stdin"],
        input=body, capture_output=True, text=True, **where,
    )
    rel = f"{str(dest).rstrip('/')}/{title}.md"
    if created.returncode == 0:
        return rel
    if EXISTS not in (created.stdout or "") + (created.stderr or ""):
        sys.exit(
            f"frameio_doc_note: `page create` refused (exit {created.returncode}) — "
            f"no page was written.\n{((created.stderr or '') + (created.stdout or '')).strip()[-500:]}"
        )
    # The one refusal that is not a failure: this job landed the page before,
    # and the one under `dest` is the one to replace.
    edited = subprocess.run(
        [ops, "--json", "page", "edit", rel, *keys, "--stdin"],
        input=body, capture_output=True, text=True, **where,
    )
    if edited.returncode != 0:
        sys.exit(
            f"frameio_doc_note: `page edit` refused (exit {edited.returncode}) over "
            f"{rel} — no page was written.\n{((edited.stderr or '') + (edited.stdout or '')).strip()[-500:]}"
        )
    return rel


def write_report(cap_dir: Path, capture_rel: str, *, outcome: str, reason=None, written=()) -> dict:
    """The ticket's PROCESS report, written LAST.

    `written[]` is the pages this run landed and `captured[]` is empty: a
    process ticket captures nothing. The capture-freshness check a harvest
    report carries does not apply — the spawner rewrites `ticket.json` long
    after harvest wrote `capture.json`, so it would refuse every honest one.
    """
    report = {
        "v": 1,
        "ticket": (read_json(cap_dir / TICKET_NAME) or {}).get("ticket"),
        "outcome": outcome,
        "reason": reason,
        "captured": [],
        "written": list(written),
        "missing": [],
        "discovered": [],
    }
    write_json(cap_dir / REPORT_NAME, report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wiki", type=Path, help="the wiki root — what binds the front door to this wiki; `.` under `run`")
    ap.add_argument("--capture-dir", required=True, help="wiki-relative capture dir: `capture_dir` off ticket.json")
    ap.add_argument("--dest", required=True, help="the ticket's `dest`, verbatim — the one directory the page may land in")
    ap.add_argument("--stage", default="process", choices=("process",), help="this script is the process step; accepted so the prose can pass the stage through")
    ap.add_argument("--skip", default=None, metavar="REASON", help="write the `skipped` report and no page: what exclude_rules, options or min_date ruled out")
    ap.add_argument("--item", default=None, help="the leaf's view URL (default: capture.json's item, then meta.json's url)")
    ap.add_argument("--name", default=None, help="the asset's original filename (default: meta.json's name)")
    ap.add_argument("--path", action="append", default=None, help="folder breadcrumb bit, repeatable (default: meta.json's path)")
    ap.add_argument("--crumb-skip", type=int, default=None, help="leading --path folders every leaf of the share carries, left off the breadcrumb (default: meta.json's, else 1)")
    ap.add_argument("--group", default=None)
    ap.add_argument("--group-type", dest="group_type", default=None)
    ap.add_argument("--author", default=None)
    ap.add_argument("--title-strip", default=None, help="suffix to trim off the captured title (e.g. the share's own name)")
    args = ap.parse_args()

    capture_rel = args.capture_dir.rstrip("/")
    cap_dir = (args.wiki / capture_rel).resolve()
    if not cap_dir.is_dir():
        sys.exit(f"{cap_dir} is not a directory — pass the ticket's capture_dir, wiki-relative")
    # A stale report first: this capture dir is the same one on every pull, and
    # `apply` does not check whose ticket the report it reads answers.
    (cap_dir / REPORT_NAME).unlink(missing_ok=True)

    if args.skip:
        write_report(cap_dir, capture_rel, outcome="skipped", reason=one_line(args.skip))
        print(json.dumps({"dir": capture_rel, "written": [], "skipped": one_line(args.skip)}))
        return 0

    record = read_json(cap_dir / CAPTURE_NAME) or {}
    meta = read_json(cap_dir / META_NAME) or {}
    name = args.name or meta.get("name")
    body_name = record.get("body") if isinstance(record.get("body"), str) else None
    src_path = (cap_dir / body_name) if body_name else None
    if src_path is None or not src_path.is_file():
        sys.exit(
            f"{cap_dir} holds no capture.json naming a body that is there — harvest writes one "
            "per captured leaf, and the process step reads the bytes it names"
        )
    ext = src_path.suffix.lstrip(".").lower()
    item = args.item or record.get("item") or meta.get("url")
    if not isinstance(item, str) or not item:
        sys.exit(f"no --item, no `item` in {cap_dir / CAPTURE_NAME} and no `url` in {cap_dir / META_NAME} — a page with no resource is one nothing can find")
    title = record.get("title") if isinstance(record.get("title"), str) and record["title"].strip() else None
    if not title:
        sys.exit(f"{cap_dir / CAPTURE_NAME} carries no title — it is what the page's FILE is named from")

    ticket = read_json(cap_dir / TICKET_NAME) or {}
    process = ticket.get("process") if isinstance(ticket.get("process"), dict) else {}
    bundle = process.get("bundle_media") is True

    if is_media(record.get("content_type")):
        # A video's page is the stub the transcribe stage reads: empty body,
        # `extracted=queued` and the media it waits on, wiki-relative
        # (`pipeline/media.py::queued_under` asks for exactly those two). The
        # title is the leaf's own, settled at harvest — one leaf, one page, so
        # nothing here has to be told apart from a page beside it.
        media_rel = f"{capture_rel}/{body_name}"
        if bundle:
            # Beside the page, under `dest`: a peer that pulls the corpus gets
            # the media, where `_raw/` is machine-local and never committed.
            beside = args.wiki / args.dest.strip("/") / ASSETS
            beside.mkdir(parents=True, exist_ok=True)
            copy = beside / f"{title}.{ext}" if ext else beside / title
            shutil.copyfile(src_path, copy)
            media_rel = f"{args.dest.strip('/')}/{ASSETS}/{copy.name}"
        written = write_page(
            args.wiki, args.dest, title,
            [f"resource={item}", f"type={TYPE_VIDEO}", f"{FLAG}={QUEUED}", f"{MEDIA}={media_rel}"], "",
        )
        write_report(cap_dir, capture_rel, outcome="ok", written=[written])
        print(json.dumps({"dir": capture_rel, "written": [written], "title": title, "ext": ext, "queued": f"{capture_rel}/{body_name}"}))
        return 0

    path_bits = list(args.path if args.path is not None else [p for p in (meta.get("path") or []) if isinstance(p, str)])
    crumb_skip = args.crumb_skip
    if crumb_skip is None:
        crumb_skip = meta["crumb_skip"] if isinstance(meta.get("crumb_skip"), int) else 1
    title_strip = args.title_strip if args.title_strip is not None else meta.get("title_strip")

    # capture_asset.py names every doc `document.<ext>`; the real filename is
    # the manifest's, carried on meta.json only when the capture was given it.
    orig_name = one_line(name or "") or src_path.name
    # The venue's own title, on ONE line — it is the H1. What names the page's
    # FILE is `capture.json`'s, which harvest made safe and settled.
    h1 = one_line(strip_title(meta.get("title") or "", title_strip)) or name_stem(orig_name)

    facts = asset_facts(
        kind="document",
        url=item,
        name=orig_name,
        ext=ext,
        size=src_path.stat().st_size,
        path_bits=path_bits,
        author=args.author if args.author is not None else meta.get("author"),
        group=args.group if args.group is not None else meta.get("group"),
        group_type=args.group_type if args.group_type is not None else meta.get("group_type"),
    )
    extracted = extracted_text(src_path, ext)
    # `path_bits[0]` is the first folder BELOW the enumerated URL — never the
    # share's own name, which `enumerate_tree.py` keeps apart as `root_title`.
    # Where every leaf of the share carries the same one it distinguishes
    # nothing and the breadcrumb starts below it (`crumb_skip` 1); where the
    # share's root lists several folders it is exactly what tells two leaves
    # apart, and the driver — which sees the whole manifest — recorded 0.
    breadcrumb = " / ".join(path_bits[max(0, crumb_skip):])
    body = render_body(title=h1, url=item, facts=facts, orig_name=orig_name, breadcrumb=breadcrumb, extracted=extracted)
    # Kept beside the capture as well as written to the page: a process ticket
    # can be retried over the same bytes, and this is what the run produced.
    (cap_dir / BODY_NAME).write_text(body, encoding="utf-8")
    written = write_page(args.wiki, args.dest, title, [f"resource={item}", f"type={TYPE_DOC}", f"{FLAG}={EXTRACTED}"], body)
    # LAST: a report naming a page is the claim that the page is there.
    write_report(cap_dir, capture_rel, outcome="ok", written=[written])
    print(
        json.dumps(
            {
                "dir": capture_rel,
                "written": [written],
                "title": title,
                "ext": ext,
                "extracted_chars": len(extracted),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
