# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Capture ONE Frame.io leaf into its own capture dir. Harvest only — bytes.

platform: frameio
scope: platform-general (no hardcoded share ids or hosts). The per-leaf half
of a share harvest: `harvest_share.py` calls it once per planned leaf, and it
is also what a hand run uses for a single asset. It wraps `capture_asset.py`
— one asset, one capture dir — and then leaves that dir holding what the venue
served plus a FLAT `capture.json` naming it:

- a **document** (pdf/pptx/xlsx/…): `body` is `document.<ext>`, as downloaded.
  The page is the PROCESS step's — `frameio_doc_note.py` over these bytes.
- a **video**: `body` is `video.mp4`. A media body is the transcriber's, and
  only the transcribe stage mints the page that waits for a transcript.

No page is rendered here, no summary is written, and `capture.json` carries no
`frontmatter` object: harvest captures bytes. What the harvest knew and the
bytes do not carry — the leaf's folder path, the operator's
`--author`/`--group`/`--title-strip` — is recorded in `meta.json`, which is
where the process step reads it back.

It runs no queue verb and posts no update: that is `harvest_share.py`'s own,
from what this left on disk.

The leaf dir must be exactly `_raw/<slug>/<one component>` — the slice is
granted the job's whole `_raw/<slug>/`, and `apply` mints a process ticket for
no other shape — and that is checked BEFORE anything is fetched, because
afterwards the bytes are already down. Resolved paths, compared by component
against `--root` when the caller passes one (the driver always does): `..`
and a symlinked component are both refused.

Usage:
  uv run capture_job.py <leaf-dir> [--root <wiki root>] [--url <view-url>]
      [--slug <slug>]
      [--name=<original filename>] [--path=<folder> ...] [--crumb-skip=N]
      [--title-strip S] [--author A] [--group G] [--group-type T]
      [--timeout-ms N] [--deadline-seconds N] [--fresh]

Venue text — a filename, a folder — goes in as `--name=<v>`, never as a
separate item: a file called `-rf.pdf` is an option to argparse otherwise.
`--deadline-seconds` is what is left of the slice: each child gets a little
less and is killed, with everything it started, when it runs out (exit 1,
`timeout` in the error). `--fresh` is a refresh ticket's: the `video.mp4` an
earlier capture left is dropped first, or yt-dlp would call it downloaded.

`--url` and `--slug` default from `--ticket <id>`, opened through the front
door (a ticket whose target is itself a leaf viewer); `harvest_share.py`
always passes both explicitly.
`--name`/`--path` come off the share's leaf manifest; without them the name
falls back to the URL's asset id and the document extension comes off the
signed proxy route.

Outputs one JSON object on stdout: {"ok", "item", "dir", "kind", "bytes",
"title", "body"} — or {"ok": false, "item", "dir", "error"}.

Exit 0 when the leaf is captured, 1 when the fetch failed (the dir then holds
no `capture.json`, which is how the driver reads it), 2 when the arguments do
not add up — nothing was fetched.

History:
  2026-08-18  created — the per-job capture path.
  2026-08-20  `--capture-dir` became required and host-derived.
  2026-09-19  ported to the ticket contract: no `assignment.json`, no job id
              and no report row — those belonged to a host that dispatched one
              job per leaf. Takes the leaf dir and URL directly, writes the
              extractor's `capture.json` (media body for a video), and hands
              documents to `frameio_doc_note.py` for their `page.md`.
  2026-09-19  review fixes: the title is made filename-safe where it is
              written (`capture_record()`); children get a deadline; venue
              text crosses argv as `--name=<v>`; what an earlier attempt left
              in the leaf dir is cleared before the fetch.
  2026-09-19  the unit's two steps restored: harvest is bytes. `page.md` and
              the `frontmatter` object are gone from here, the document's
              `body` is the downloaded file, and the manifest and operator
              facts are recorded in `meta.json` for the process step.
  2026-09-25  `--url`/`--slug` default through `--ticket <id>` (`tickets
              open`), not a file beside the leaf dir.
"""

import argparse
import json
import sys
from pathlib import Path

from capture_record import (
    CAPTURE_NAME,
    META_NAME,
    TIMED_OUT,
    capture_record,
    content_type_for,
    inner_deadline,
    leaf_ids,
    name_stem,
    open_ticket,
    pick_document,
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
    ap.add_argument("--url", default=None, help="the leaf's view URL (default: --ticket's own target)")
    ap.add_argument("--slug", default=None, help="the job's slug (default: --ticket's own slug)")
    ap.add_argument("--ticket", default=None, help="the ticket id, opened for --url/--slug's defaults")
    ap.add_argument("--name", default=None, help="the asset's original filename, off the share's leaf manifest")
    ap.add_argument("--path", action="append", default=None, help="folder breadcrumb bit, repeatable — same source as --name")
    ap.add_argument("--title-strip", default=None, help="share-wide suffix to trim off the captured title")
    ap.add_argument("--author", default=None)
    ap.add_argument("--group", default=None)
    ap.add_argument("--group-type", dest="group_type", default=None)
    ap.add_argument("--timeout-ms", type=int, default=None, help="passed through to capture_asset.py")
    ap.add_argument("--crumb-skip", type=int, default=None, help="leading --path folders every leaf of the share carries; recorded in meta.json for the process step")
    ap.add_argument("--deadline-seconds", type=float, default=None, help="what is left of the slice: children are killed when it runs out")
    ap.add_argument("--fresh", action="store_true", help="a refresh: drop the media an earlier capture left, so it is fetched again")
    args = ap.parse_args()

    ticket = open_ticket(args.ticket) if args.ticket else {}
    url = args.url or ticket.get("target")
    slug = args.slug or ticket.get("slug")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")) or not leaf_ids(url)[1]:
        print(f"error: {url!r} is not a leaf viewer URL (https://.../share/<share-id>/view/<asset-id>)", file=sys.stderr)
        return 2
    if not isinstance(slug, str) or not slug:
        print("error: no --slug and no --ticket to read one from", file=sys.stderr)
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
    # What an earlier attempt left is not this one's: a `capture.json` would be
    # read as landed, and a half-written `document.bin` beside the new
    # `document.pdf` is the one a glob finds first. A partial `video.mp4.part`
    # stays — yt-dlp resumes it — unless this is a refresh.
    stale = [CAPTURE_NAME, "page.md", META_NAME, *(p.name for p in leaf_dir.glob("document.*"))]
    if args.fresh:
        stale += [p.name for p in leaf_dir.glob(f"{VIDEO_BODY}*")]
    for name in stale:
        (leaf_dir / name).unlink(missing_ok=True)

    inner = inner_deadline(args.deadline_seconds)
    cmd = ["uv", "run", str(SCRIPTS / "capture_asset.py"), url, f"--out={leaf_dir}"]
    if args.name:
        cmd.append(f"--name={args.name}")
    if args.timeout_ms is not None:
        cmd.append(f"--timeout-ms={args.timeout_ms}")
    if inner is not None:
        cmd.append(f"--deadline-seconds={inner_deadline(inner):.0f}")
    rc, out, err = run(cmd, timeout=inner)
    if rc != 0:
        if rc == TIMED_OUT:
            for part in leaf_dir.glob("document.*"):  # killed mid-download
                part.unlink(missing_ok=True)
        return fail(url, rel, f"capture_asset exit {rc}: {err or out}")

    # Everything past the fetch still ends in a JSON answer and an exit code:
    # truncated JSON, a missing `kind`, a body that is not on disk — each used
    # to be a traceback with the bytes already down and nothing said about them.
    try:
        result = json.loads(out.splitlines()[-1])
        kind, size = result["kind"], result["bytes"]
        meta = read_json(leaf_dir / META_NAME) or {}
        name = args.name or meta.get("name")
        title = strip_title(result.get("title") or meta.get("title") or "", args.title_strip)
        if kind == "video":
            body, content_type = VIDEO_BODY, "video/mp4"
        else:
            document = pick_document([p for p in leaf_dir.glob("document.*") if p.is_file()], name)
            if document is None:
                raise FileNotFoundError(f"no document.<ext> in {rel}")
            body, content_type = document.name, content_type_for(document.suffix.lstrip("."))
        if not (leaf_dir / body).is_file():
            raise FileNotFoundError(f"{body} is not in {rel}")
        # What the harvest knew and the bytes do not carry. The process step
        # reads it back from here: nothing renders a page at harvest, and the
        # leaf's place in the share is not in the file it downloaded.
        meta.update(
            {
                "url": meta.get("url") or url,
                "name": name,
                # capture_asset.py's stdout title beats the one it left in
                # meta.json, as it always has here; the process step's H1 is
                # this, trimmed by `title_strip`.
                "title": result.get("title") or meta.get("title") or "",
                "path": path_bits,
                "crumb_skip": args.crumb_skip if args.crumb_skip is not None else 0,
                "title_strip": args.title_strip,
                "author": args.author,
                "group": args.group,
                "group_type": args.group_type,
                "kind": kind,
                "bytes": size,
            }
        )
        write_json(leaf_dir / META_NAME, meta)
        record = capture_record(
            slug=slug,
            item=url,
            title=title or name_stem(name),
            fallback=leaf_ids(url)[1],
            body=body,
            content_type=content_type,
        )
        write_json(leaf_dir / CAPTURE_NAME, record)
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
