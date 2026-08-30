"""Shared helpers for the channel-frameio scripts (imported sibling module).

Not run directly: `capture_job.py` imports it — `uv run` puts the script's
own directory on sys.path, so a within-unit sibling import is the sanctioned
way to share code inside a skill unit (stdlib only; never import plugin or
package modules).

Holds the capture contract itself, in one place, so a second caller can
never drift from it:
  - `capture_record()` — the capture.json dict.
  - `run()` / `domain_of_url()` — the subprocess and domain utilities the
    per-job path is built from.

`normalize_url()` lived here and went with the queue verbs: it
mirrored the pipeline intake's normalization so a by-url lookup keyed off
raw view URLs would hit `job["url"]`, and there is no by-url lookup any
more — the host dispatches one leaf per job.

`capture_dir_for()` lived here and went the same way:
the capture dir is now HOST-derived (`core/watches.capture_dir_for`, inside
the watch's own slice) and handed to this unit as `--capture-dir` — a unit
never composes a path, so there is nothing left for a second caller to
drift from here.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# Wiki-relative path of the capture script, recorded in capture.json's
# `script` field per the pipeline's capture contract (wiki-relative, so the
# provenance survives any machine's checkout location).
CAPTURE_SCRIPT = "ops/skills/channel-frameio/scripts/capture_asset.py"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def domain_of_url(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def capture_record(job: dict, cap_result: dict, *, name: str, path_bits, domain: str, abs_capture_dir: Path) -> dict:
    """The capture.json dict for a completed Frame.io asset capture.

    Emits `fetched_at` (now-UTC) — the doc-note scaffolder keys its
    `generated.at` provenance off it, so it must never be missing — and
    copies `dest` from the job when the watch chose a bundle destination.
    The `files` block is deliberately omitted: a Frame.io capture has no
    page.html/page.md, only the downloaded asset + meta.json.
    """
    is_video = cap_result["kind"] == "video"
    record = {
        "id": job["id"],
        "url": job["url"],
        "final_url": job["url"],
        "title": cap_result.get("title") or name,
        "domain": domain,
        "fetched_at": now_utc(),
        "tool": "playwright",
        "script": CAPTURE_SCRIPT,
        "tags": job.get("tags", []),
        "areas": job.get("areas", []),
        "author": job.get("author", ""),
        "group": job.get("group", ""),
        "group_type": job.get("group_type", ""),
        "path": list(path_bits),
        "assets": [
            {
                "id": "asset-001",
                "type": "hls" if is_video else "file",
                "src_url": job["url"],
                "local_path": ("video.mp4" if is_video else next(abs_capture_dir.glob("document.*")).name),
                "bytes": cap_result["bytes"],
                "status": "downloaded",
            }
        ],
        "verdict": {"status": "complete", "reasons": [], "escalations_used": ["playwright"]},
    }
    if job.get("dest"):
        record["dest"] = job["dest"]
    return record
