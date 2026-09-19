# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Capture ONE Frame.io leaf into its own capture dir, ready for the extractor.

platform: frameio
scope: platform-general (no hardcoded share ids or hosts). The per-leaf half
of a share harvest: `harvest_share.py` calls it once per planned leaf, and it
is also what a hand run uses for a single asset. It wraps `capture_asset.py`
— one asset, one capture dir — and then leaves that dir in the one shape the
generic extractor reads:

- a **document** (pdf/pptx/xlsx/…): `frameio_doc_note.py` renders `page.md`
  and writes `capture.json` naming it, `content_type: text/markdown`.
- a **video**: `capture.json` names `video.mp4` itself as the `body`. The
  extractor treats a media body as the transcriber's: it writes the page
  empty, flags it for transcription and reports the file as discovered media.
  A `page.md` that merely LINKED the video would be taken verbatim as the
  page, and the recording would never be transcribed — and the link would be
  a signed HLS URL that dies within hours anyway. The cost is that a video's
  facts reach only the `frontmatter` object today, not a page body.

Processing is the generic extractor's, so everything venue-specific happens
here, at harvest. It runs no queue verb and writes no report: `report.json`
is the ticket's, and `harvest_share.py` writes it from what this left on disk.

The leaf dir must be exactly `_raw/<slug>/<one component>` — the slice is
granted the job's whole `_raw/<slug>/`, and `apply` mints a process ticket for
no other shape — and that is checked BEFORE anything is fetched, because
afterwards the bytes are already down. Resolved paths, compared by component
against `--root` when the caller passes one (the driver always does): `..`
and a symlinked component are both refused.

Usage:
  uv run capture_job.py <leaf-dir> [--root <wiki root>] [--url <view-url>]
      [--slug <slug>]
      [--name <original filename>] [--path <folder> ...]
      [--title-strip S] [--author A] [--group G] [--group-type T]
      [--timeout-ms N]

`--url` and `--slug` default from a `ticket.json` in `<leaf-dir>` (a ticket
whose target is itself a leaf viewer); `harvest_share.py` always passes both.
`--name`/`--path` come off the share's leaf manifest; without them the name
falls back to the URL's asset id and the document extension comes off the
signed proxy route.

Outputs one JSON object on stdout: {"ok", "item", "dir", "kind", "bytes",
"title", "body"} — or {"ok": false, "item", "dir", "error"}.

Exit 0 when the leaf is captured, 1 when the fetch or the render failed (the
dir then holds no `capture.json`, which is how the driver reads it), 2 when
the arguments do not add up — nothing was fetched.

History:
  2026-08-18  created — the per-job capture path.
  2026-08-20  `--capture-dir` became required and host-derived.
  2026-09-19  ported to the ticket contract: no `assignment.json`, no job id
              and no report row — those belonged to a host that dispatched one
              job per leaf. Takes the leaf dir and URL directly, writes the
              extractor's `capture.json` (media body for a video), and hands
              documents to `frameio_doc_note.py` for their `page.md`.
"""

import argparse
import json
import sys
from pathlib import Path

from capture_record import (
    CAPTURE_NAME,
    META_NAME,
    TICKET_NAME,
    asset_facts,
    capture_record,
    leaf_ids,
    name_stem,
    read_json,
    run,
    strip_title,
    write_json,
)

RAW_DIRNAME = "_raw"
VIDEO_BODY = "video.mp4"
SCRIPTS = Path(__file__).resolve().parent


def slice_leaf(leaf_dir: Path, slug: str, root=None):
    """The leaf dir as wiki-relative `_raw/<slug>/<one>`, or None when it is not.

    Resolved and compared by path COMPONENT, never by string prefix: the dir
    is caller-supplied, `_raw/<slug>/../other/x` starts with the right string,
    and a symlinked component lands the bytes somewhere the slice never
    granted. `resolve()` rather than `normpath()` because a grant is about
    where the BYTES land, and only `resolve` follows a symlink.

    With `root` (the driver always passes it) the resolved dir must sit at
    exactly that depth under THAT wiki. Without it — a hand run — the root is
    read off the dir itself, three levels up, so only the shape is checked.
    """
    target = leaf_dir.resolve()
    base = Path(root).resolve() if root is not None else (target.parents[2] if len(target.parents) > 2 else None)
    if base is None:
        return None
    try:
        parts = target.relative_to(base).parts
    except ValueError:
        return None
    if len(parts) != 3 or parts[0] != RAW_DIRNAME or parts[1] != slug:
        return None
    return "/".join(parts)


def fail(item, rel, error, code=1):
    print(json.dumps({"ok": False, "item": item, "dir": rel, "error": error[:600]}, indent=2))
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("leaf_dir", type=Path, help="the leaf's capture dir: _raw/<slug>/<one component>")
    ap.add_argument("--root", type=Path, default=None, help="the wiki root the leaf dir must sit under (default: read off the dir)")
    ap.add_argument("--url", default=None, help="the leaf's view URL (default: ticket.json's target)")
    ap.add_argument("--slug", default=None, help="the job's slug (default: ticket.json's slug)")
    ap.add_argument("--name", default=None, help="the asset's original filename, off the share's leaf manifest")
    ap.add_argument("--path", action="append", default=None, help="folder breadcrumb bit, repeatable — same source as --name")
    ap.add_argument("--title-strip", default=None, help="share-wide suffix to trim off the captured title")
    ap.add_argument("--author", default=None)
    ap.add_argument("--group", default=None)
    ap.add_argument("--group-type", dest="group_type", default=None)
    ap.add_argument("--timeout-ms", type=int, default=None, help="passed through to capture_asset.py")
    args = ap.parse_args()

    ticket = read_json(args.leaf_dir / TICKET_NAME) or {}
    url = args.url or ticket.get("target")
    slug = args.slug or ticket.get("slug")
    if not isinstance(url, str) or not leaf_ids(url)[1]:
        print(f"error: {url!r} is not a leaf viewer URL (.../share/<share-id>/view/<asset-id>)", file=sys.stderr)
        return 2
    if not isinstance(slug, str) or not slug:
        print(f"error: no --slug and no {TICKET_NAME} in {args.leaf_dir} to read one from", file=sys.stderr)
        return 2
    rel = slice_leaf(args.leaf_dir, slug, args.root)
    if rel is None:
        print(
            f"error: {args.leaf_dir} is not {RAW_DIRNAME}/{slug}/<one component> — the slice grants nothing else "
            f"and `apply` would refuse the capture, so nothing was fetched",
            file=sys.stderr,
        )
        return 2

    leaf_dir = args.leaf_dir
    path_bits = list(args.path or [])
    cmd = ["uv", "run", str(SCRIPTS / "capture_asset.py"), url, "--out", str(leaf_dir)]
    if args.name:
        cmd += ["--name", args.name]
    if args.timeout_ms is not None:
        cmd += ["--timeout-ms", str(args.timeout_ms)]
    rc, out, err = run(cmd)
    if rc != 0:
        return fail(url, rel, f"capture_asset exit {rc}: {err or out}")

    # Everything past the fetch still ends in a JSON answer and an exit code:
    # truncated JSON, a missing `kind`, a render that died — each used to be a
    # traceback with the bytes already on disk and nothing said about them.
    try:
        result = json.loads(out.splitlines()[-1])
        kind, size = result["kind"], result["bytes"]
        if kind == "video":
            if not (leaf_dir / VIDEO_BODY).is_file():
                raise FileNotFoundError(f"{VIDEO_BODY} is not in {rel}")
            meta = read_json(leaf_dir / META_NAME) or {}
            name = args.name or meta.get("name")
            title = strip_title(result.get("title") or meta.get("title") or "", args.title_strip)
            title = title or name_stem(name) or leaf_ids(url)[1]
            record = capture_record(
                slug=slug,
                item=url,
                title=title,
                body=VIDEO_BODY,
                content_type="video/mp4",
                frontmatter=asset_facts(
                    kind="video", url=url, name=name, ext="mp4", size=size, path_bits=path_bits,
                    author=args.author, group=args.group, group_type=args.group_type,
                ),
            )
            write_json(leaf_dir / CAPTURE_NAME, record)
        else:
            note = ["uv", "run", str(SCRIPTS / "frameio_doc_note.py"), str(leaf_dir), "--slug", slug, "--url", url]
            if args.name:
                note += ["--name", args.name]
            for bit in path_bits:
                note += ["--path", bit]
            for flag, value in (
                ("--title-strip", args.title_strip),
                ("--author", args.author),
                ("--group", args.group),
                ("--group-type", args.group_type),
            ):
                if value is not None:
                    note += [flag, value]
            nrc, nout, nerr = run(note)
            if nrc != 0:
                raise RuntimeError(f"frameio_doc_note exit {nrc}: {nerr or nout}")
            record = read_json(leaf_dir / CAPTURE_NAME)
            if not record or not (leaf_dir / str(record.get("body"))).is_file():
                raise RuntimeError("frameio_doc_note exited 0 and left no capture.json naming a body that is there")
    except Exception as exc:  # noqa: BLE001 — see the comment above
        (leaf_dir / CAPTURE_NAME).unlink(missing_ok=True)
        return fail(url, rel, f"capture_asset exited 0 but the capture did not add up: {type(exc).__name__}: {exc}")

    print(
        json.dumps(
            {
                "ok": True,
                "item": url,
                "dir": rel,
                "kind": kind,
                "bytes": size,
                "title": record.get("title"),
                "body": record.get("body"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
