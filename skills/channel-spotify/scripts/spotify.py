#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///
"""Spotify entity capture and search for the llm-wiki pipeline.

Venue: open.spotify.com — playlists, shows (podcasts), episodes, albums,
tracks, audiobooks. Metadata comes from the official Web API (client
credentials; public catalog only). Audio is downloaded ONLY when the content
is openly distributed: podcast episodes are matched to their show's public
RSS feed (resolved via the keyless iTunes Search API) and the feed's MP3
enclosures go into an assets.json the plugin's assets.py can download.
Music tracks and Spotify-exclusive audiobook chapters are DRM catalog
audio — they are recorded as drm_protected references (metadata + link),
never ripped. That mirrors the harvest boundary: flag DRM, move on.

Subcommands:
  auth          store/test client credentials (the `spotify` credential)
  search        resolve a natural request ("lex fridman #400") to entity URLs
  meta          full metadata JSON for any supported entity URL/URI
  resolve-feed  show/episode -> public RSS feed + episode list
  capture       full capture into --capture-dir: meta.json, items.json,
                assets.json (pending cover art + audio enclosures), page.md
                (the rendered page BODY) and capture.json (names page.md as
                the body, carries the entity's facts under `frontmatter`).
                Reads the URL, slug, min_date and asset policy off the
                ticket.json the spawner left in that directory; flags override.
  report        report.json into --capture-dir, read off what is there — the
                last thing a harvest worker writes.

Inputs:  entity URL (https://open.spotify.com/<type>/<id>) or spotify:<type>:<id>
Outputs: JSON on stdout (all subcommands); capture writes files, stdout JSON
         is the run summary. Exit 4 from capture = entity captured but no
         audio was resolvable (all-DRM or no feed match) — metadata-only,
         and still a complete capture (capture.json is written).

The harvest worker is the only stage that reaches this unit: the pipeline's
generic extractor turns the capture into a page, taking page.md verbatim and
prepending its own frontmatter. Everything venue-specific is therefore
rendered here, at harvest time, into the capture directory — never into the
job's `dest`, which a harvest slice cannot write.

Auth: the `spotify` credential, {"client_id": ..., "client_secret": ...}
      (env SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET override). `auth
      --client-id <id>` prompts for the secret without echo, or reads one line
      of stdin — never argv. The token is held in memory for the process
      lifetime, never written to the store. No user OAuth: private playlists
      and the user library are out of scope.

Paths: `llm-wiki-ops run` starts this script in the WIKI ROOT, so
      `--capture-dir` is the ticket's `capture_dir` verbatim (wiki-relative),
      and so is any other relative path.

Keyless degradation: with no credentials, `meta` and `capture` fall back to
the public embed endpoint (open.spotify.com/embed/...), which exposes the
entity name and a POSSIBLY TRUNCATED item list. The output carries
"keyless": true so nobody mistakes it for a complete enumeration.

History:
  2026-08-01  Written for this wiki (first target: the $100M Money Models
              audiobook playlist, which turned out to be episodes of the
              openly-distributed show "The Game with Alex Hormozi").
  2026-08-04  Auth resolves through the wiki's credential store (`credential
              get|set spotify` via the front door); the token is held in memory
              for the process lifetime, never cached to disk.
  2026-09-19  Ported to the rebuilt worker contract: `capture` reads
              ticket.json, writes capture.json (body page.md + `frontmatter`
              facts) and a facts list in page.md; `report` writes report.json;
              unreachable feed hosts are recorded instead of swallowed.
  2026-09-19  Review fixes: the title is filename-safe (`safe_title`), venue
              text cannot forge page structure, URLs are http(s) only, a
              404/410 is `gone`/`failed` instead of a traceback, a page of
              items that cannot be fetched makes the capture `partial`, a
              `skipped` report repeats nothing from an earlier pull, and the
              client secret is prompted for instead of taken from argv.
"""

import argparse
import getpass
import json
import datetime
import os
import re
import stat
import subprocess
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ITUNES_SEARCH = "https://itunes.apple.com/search"
ENTITY_TYPES = ("playlist", "show", "episode", "album", "track", "audiobook", "artist")
UA = {"User-Agent": "llm-wiki channel-spotify (+local knowledgebase pipeline)"}


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


class NotFound(Exception):
    """The venue answered 404 or 410 for the entity itself: it is gone (or was
    never there, or is not offered in this market). Raised rather than
    returned, so no caller can dereference the absence."""

    def __init__(self, url, status):
        super().__init__(f"{url} -> {status}")
        self.url, self.status = url, status


# ------------------------------------------------- venue text is data, not structure
#
# `page.md` is the FINAL page body, taken verbatim, and `capture.json`'s title
# names the page's file. Everything below exists so that a name, a description
# or a URL the venue (or a same-named hostile feed) supplied can never forge a
# heading, a rule, a fence, a table row, a link scheme or a filename.

# The page's FILE is named from this title, and the host refuses a title its filename rule
# cannot hold (llm_wiki_ops/commands/page/note.py::filename_for — ILLEGAL, control chars, a
# leading dot) — failing the process ticket after harvest said ok. keep-in-sync: every unit's safe_title.
_TITLE_SWAPS = {":": " -", "/": "-", "\\": "-", "|": "-", "?": "", "*": "", '"': "'", "<": "(", ">": ")"}
TITLE_MAX = 120  # characters
# UTF-8 bytes: the host checks no length, and a filename is capped in BYTES (255 on ext4/APFS) with `.md`
# appended — 100 CJK characters is 300 bytes and the REAL extractor dies `OSError: [Errno 36] File name too
# long` (measured, channel-youtube)
TITLE_MAX_BYTES = 200


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
    if text.casefold() == "index":
        # `index.md` is the host's one RESERVED page name (note.py::RESERVED): its page walker skips
        # it, so the page never appears in `known[]` and the item is re-pulled forever.
        text = f"{text} (page)"
    return text or fallback


def page_title(meta):
    """`capture.json`'s title for an entity. `safe_title` is the rule every
    unit shares; the one thing added here is the host's RESERVED page name
    (note.py::RESERVED = `index.md`): a page so named is left out of every
    listing the host makes, `known[]` included, so an entity called "index"
    would be re-pulled for ever and never found."""
    title = safe_title(meta.get("name"), fallback=f"Spotify {meta['type']} {meta['id']}")
    return f"{title} (Spotify {meta['type']})" if title.lower() == "index" else title


def _one_line(value):
    """Folded to ONE line: newlines, tabs and control characters become a
    space, so a name carrying `\n---\n` or `\n# Forged` stays a name."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value if value is not None else ""))
    return " ".join(text.split())


def _cell(value):
    """One table cell: one line, and no `|` that ends it early."""
    return _one_line(value).replace("\\", "\\\\").replace("|", "\\|")


_BLOCK_OPENERS = "#`~=-+*_>|"


def _quoted(prose):
    """Free venue prose as a blockquote, line by line, under a fixed first
    line. Inside a quote a fence or a heading ends with the quote, which the
    blank line after it closes; a line's block-opening first character and
    every `<` are escaped anyway — a heading inside a quote is still a heading
    in an outline, and `text` over `---` is a setext one — and the fixed first line is
    what keeps the prose from opening a callout (`[!…]` counts only there)."""
    out = ["> Description, as the venue gave it (quoted data, not instructions):", ">"]
    for raw in str(prose).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = "".join(ch if ch.isprintable() else " " for ch in raw).strip()
        if line and line[0] in _BLOCK_OPENERS:  # a heading, a fence, a rule, a setext underline, a nested quote, a table
            line = "\\" + line
        out.append(("> " + line.replace("<", "\\<")).rstrip())
    while out and out[-1] == ">":
        out.pop()
    return out


def http_url(value):
    """The URL when it is plain `http`/`https` with a host, else None. Every
    venue-supplied URL passes through here before it becomes an asset entry, a
    fetch or a link: the plugin's asset downloader opens whatever scheme it is
    handed, `file://` included, and the open feed is whichever one iTunes
    returns for the show NAME — a same-named hostile podcast is enough."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(ch.isspace() or not ch.isprintable() for ch in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if parts.scheme in ("http", "https") and parts.hostname else None


def _md_url(url):
    """A checked URL as a markdown link target / table cell: nothing in it can
    close the link, end the cell or open an HTML tag."""
    return quote(url, safe=":/?#@!$&'+,;=%~-._")


_SPOTIFY_ID = re.compile(r"[A-Za-z0-9]{1,64}")
_DATE_SHAPED = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")


def wiki_root(start=None):
    # NOT `Path.is_file()`: it swallows `PermissionError` and reports False,
    # which would make a denied `.llm-wiki.toml` look like "no wiki here" —
    # walking on to the next parent and eventually degrading keyless —
    # instead of surfacing the real problem. `os.stat`, not
    # `Path.is_file()`: FileNotFoundError means genuinely absent, any other
    # OSError means something IS there and could not be reached.
    p = Path(start or ".").resolve()
    for cand in (p, *p.parents):
        marker = cand / ".llm-wiki.toml"
        try:
            found = stat.S_ISREG(os.stat(marker).st_mode)
        except FileNotFoundError:
            found = False
        except OSError as e:
            die(f"cannot read {marker} ({e.__class__.__name__}: {e})", 3)
        if found:
            return cand
    return None


# The front door, by the bare name every SKILL.md already runs this script
# under — never a path into the wiki, which stops carrying a shim.
OPS = "llm-wiki-ops"

# What a nested front-door call must NOT inherit from the one that ran this
# script. Both names belong to the machine-global bash dispatcher that IS
# `llm-wiki-ops` on PATH (the llm-wiki-global plugin's `scripts/llm-wiki-ops`),
# not to the CLI package it hands off to — read there, 2026-09: it sets
# `LLM_WIKI_OPS_DISPATCHED` as its re-entry guard and refuses (exit 127) a
# call that already carries it, and it picks the wiki from `CLAUDE_PROJECT_DIR`, when
# that names one, AHEAD of the cwd. This script runs below that dispatcher, so
# the guard is still set in here and a nested call is not the loop it guards
# against; and without dropping the second, `cwd=<root>` would not be what
# picks the wiki.
NOT_INHERITED = ("LLM_WIKI_OPS_DISPATCHED", "CLAUDE_PROJECT_DIR")


def _ops(root, *args, **kw):
    """One front-door command, bound to the wiki by running from its root,
    answered as JSON — the CLI's plain answer is prose for a person. Returns
    `(exit code, answer)`, the answer always a dict: a failure that printed
    no JSON object is `{"error": <what it did print>}`. An `llm-wiki-ops` that
    is not on PATH raises `FileNotFoundError` — an `OSError`, which every
    caller reports as an unreachable store."""
    env = {k: v for k, v in os.environ.items() if k not in NOT_INHERITED}
    proc = subprocess.run([OPS, "--json", *args], cwd=str(root), env=env, capture_output=True, **kw)
    try:
        answer = json.loads(proc.stdout)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        said = (proc.stderr or proc.stdout).decode(errors="replace").strip()[:200]
        answer = {"error": said} if proc.returncode else {}
    return proc.returncode, answer


CREDENTIAL = "spotify"  # the machine's own payload; a ticket naming another (`credential`) wins

# How the CLI words a payload that IS there and could not be opened
# (common/wiki/secrets.py::get — `cannot read credential '<name>': <OSError>`),
# as against one that is not (`no credential '<name>' on this machine`).
UNREADABLE = "cannot read credential "


def load_auth(root, name=CREDENTIAL, unreadable=None):
    """Env override, else the wiki's credential store.

    Returns a bare dict (the old tuple's second element was the file path,
    which no longer exists). `{}` means no credentials — the keyless path is
    a documented degradation, not an error.

    A payload that exists and cannot be READ is an error, never a quiet
    keyless run — unless the caller passes `unreadable`, a list: then what the
    CLI said is appended to it and `{}` comes back, and the CALLER owns saying
    so out loud. Only a ticketed capture does that (see `cmd_capture`).
    """
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if cid and sec:
        return {"client_id": cid, "client_secret": sec}
    if root is None:  # outside a wiki there is no store to reach — degrade keyless
        return {}
    try:
        rc, answer = _ops(root, "credential", "get", name)
    except OSError as e:
        die(f"credential store unreachable ({e.__class__.__name__}: {e})")
    if rc != 0:
        # Exit 1 is every "could not answer" — no wiki, an unreadable store —
        # and only ONE of them is the documented keyless degradation, so it is
        # told apart by what the CLI said. Anything else is a real error.
        said = str(answer.get("error", ""))
        if said.startswith("no credential "):
            return {}
        if unreadable is not None and said.startswith(UNREADABLE):
            unreadable.append(said)
            return {}
        die(f"credential lookup failed ({rc}): {said}")
    try:
        data = json.loads(answer.get("value") or "")  # the payload `auth` stored, as the text it was
    except ValueError as e:
        die(f"credential store returned invalid JSON: {e}")
    if not isinstance(data, dict):
        die("credential store returned a non-object payload")
    return data


# Client-credentials bearer token, held for this process only — never
# written back to the credential store. {"token": ..., "expires_at": ...}.
_token_cache = None


def get_token(root, name=CREDENTIAL, unreadable=None):
    """Client-credentials token, cached in memory for this process. None = no creds."""
    global _token_cache
    data = load_auth(root, name, unreadable)
    if not data.get("client_id") or not data.get("client_secret"):
        return None
    if _token_cache and _token_cache["expires_at"] > time.time() + 30:
        return _token_cache["token"]
    for attempt in range(BACKOFF_TRIES):
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(data["client_id"], data["client_secret"]),
            timeout=30,
        )
        if r.status_code != 429 or attempt == BACKOFF_TRIES - 1:
            break
        time.sleep(backoff_seconds(r))
    if r.status_code != 200:
        die(f"token request failed ({r.status_code}): {r.text[:200]}")
    tok = r.json()
    _token_cache = {"token": tok["access_token"], "expires_at": time.time() + tok.get("expires_in", 3600)}
    return _token_cache["token"]


# A 429 is a soft block (agent-loop: back off, and if it persists fail — never
# evade). The venue's own `Retry-After` is the wait; with none, sixty seconds.
# Capped, because a slice dies at thirty minutes and an honest `partial` beats
# a killed worker.
BACKOFF_TRIES = 4
BACKOFF_DEFAULT_S = 60
BACKOFF_MAX_S = 300


def backoff_seconds(response):
    try:
        asked = int(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        asked = BACKOFF_DEFAULT_S
    return max(1, min(asked + 1, BACKOFF_MAX_S))


def api_get(token, path, params=None):
    """One API object. 404/410 raise `NotFound`; any other refusal ends the run."""
    for attempt in range(BACKOFF_TRIES):
        r = requests.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}", **UA}, timeout=30)
        if r.status_code == 429:
            if attempt < BACKOFF_TRIES - 1:
                time.sleep(backoff_seconds(r))
            continue
        if r.status_code in (404, 410):
            raise NotFound(f"{API}{path}", r.status_code)
        if r.status_code != 200:
            die(f"API {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()
    die(f"API {path}: rate-limited (429) after {BACKOFF_TRIES} tries")


def parse_entity(ref):
    """URL or spotify: URI -> (type, id)."""
    m = re.match(r"spotify:([a-z]+):([A-Za-z0-9]+)$", ref.strip())
    if m:
        typ, eid = m.group(1), m.group(2)
    else:
        u = urlsplit(ref.strip())
        if "spotify.com" not in u.netloc:
            die(f"not a spotify URL or URI: {ref}")
        parts = [p for p in u.path.split("/") if p and p != "embed"]
        # tolerate /intl-xx/ locale prefixes
        parts = [p for p in parts if not p.startswith("intl-")]
        if len(parts) < 2:
            die(f"cannot read an entity from {ref}")
        typ, eid = parts[0], parts[1]
    if typ not in ENTITY_TYPES:
        die(f"unsupported entity type {typ!r} (supported: {', '.join(ENTITY_TYPES)})")
    if not _SPOTIFY_ID.fullmatch(eid):  # it goes into URLs, the page and a fallback title
        die(f"not a Spotify id in {ref}")
    return typ, eid


def canonical_url(typ, eid):
    return f"https://open.spotify.com/{typ}/{eid}"


def paged(token, first_page, key="items"):
    """Every item behind a paging object -> `(items, truncated)`.

    `truncated` is None when the last page was reached. A page that could not
    be had — a 429 that outlived the backoff, any other status, a refused or
    dead connection — ends the walk and comes back as `{url, why, said, got,
    expected}`: what was fetched is kept, and the capture says out loud that
    the list stops short (`report` makes it `partial`). It used to `break`
    here in silence, and a truncated list reported `ok`.
    """
    out, page = [], first_page
    while page:
        out.extend(page.get(key) or [])
        nxt = page.get("next")
        if not nxt:
            break
        said, r = None, None
        if not http_url(nxt) or not host_of(nxt).endswith(".spotify.com"):
            said = "the next page is not a Spotify URL"  # the bearer token goes nowhere else
        for attempt in range(BACKOFF_TRIES if said is None else 0):
            try:
                r = requests.get(nxt, headers={"Authorization": f"Bearer {token}", **UA}, timeout=30)
            except Exception as e:  # noqa: BLE001 — a dead page is a report line, never a traceback
                said, r = f"{e.__class__.__name__}: {e}", None
                break
            if r.status_code != 429:
                break
            if attempt < BACKOFF_TRIES - 1:
                time.sleep(backoff_seconds(r))
        if said is None and r.status_code != 200:
            said = f"HTTP {r.status_code}" + (f" after {BACKOFF_TRIES} tries" if r.status_code == 429 else "")
        if said is not None:
            why = why_for(said) if r is None else ("auth" if r.status_code in (401, 403) else "error")
            return out, {"url": nxt, "why": why, "said": said[:200], "got": len(out), "expected": first_page.get("total")}
        page = r.json()
    return out, None


def ms_to_hms(ms):
    if ms is None:
        return "?"
    s = int(ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------- entity meta


def fetch_entity(token, typ, eid, market):
    """Normalized: {type, id, url, name, creator, description, images,
    release_date, items: [{n, type, id, url, name, show/artist, duration_ms,
    release_date, explicit, preview_url}]}"""
    p = {"market": market}
    items, truncated = [], None

    def sublist(path):
        # The entity answered and its list did not: that is a short list, not a gone entity.
        try:
            return api_get(token, path, {**p, "limit": 50}) or {}
        except NotFound:
            return {}

    if typ == "playlist":
        e = api_get(token, f"/playlists/{eid}", p)
        raw, truncated = paged(token, e.get("tracks") or {})
        for r in raw:
            t = r.get("track") or r.get("item") or {}
            if not t:
                continue
            items.append(_norm_item(t))
        creator = (e.get("owner") or {}).get("display_name")
    elif typ == "show":
        e = api_get(token, f"/shows/{eid}", p)
        raw, truncated = paged(token, sublist(f"/shows/{eid}/episodes"))
        items = [
            _norm_item({**x, "type": "episode", "show": {"name": e.get("name"), "publisher": e.get("publisher")}})
            for x in raw
            if x
        ]
        creator = e.get("publisher")
    elif typ == "episode":
        e = api_get(token, f"/episodes/{eid}", p)
        items = [_norm_item({**e, "type": "episode"})]
        creator = (e.get("show") or {}).get("publisher")
    elif typ == "album":
        e = api_get(token, f"/albums/{eid}", p)
        raw, truncated = paged(token, e.get("tracks") or {})
        items = [_norm_item({**x, "type": "track", "album": {"name": e.get("name")}}) for x in raw if x]
        creator = ", ".join(a["name"] for a in e.get("artists") or [])
    elif typ == "track":
        e = api_get(token, f"/tracks/{eid}", p)
        items = [_norm_item({**e, "type": "track"})]
        creator = ", ".join(a["name"] for a in e.get("artists") or [])
    elif typ == "audiobook":
        e = api_get(token, f"/audiobooks/{eid}", p)
        raw, truncated = paged(token, sublist(f"/audiobooks/{eid}/chapters"))
        items = [_norm_item({**x, "type": "chapter"}) for x in raw if x]
        creator = ", ".join(a.get("name", "") for a in e.get("authors") or [])
    else:  # artist — metadata only
        e = api_get(token, f"/artists/{eid}")
        creator = e.get("name")
    for n, it in enumerate(items, 1):
        it["n"] = n
    return {
        "type": typ,
        "id": eid,
        "url": canonical_url(typ, eid),
        "name": e.get("name"),
        "creator": creator,
        "description": e.get("description") or e.get("html_description") or "",
        "images": [u for u in (http_url(i.get("url")) for i in (e.get("images") or []) if isinstance(i, dict)) if u][:1],
        "release_date": e.get("release_date"),
        "total": len(items) or e.get("total_episodes") or e.get("total_chapters"),
        "items": items,
        "keyless": False,
        "truncated": truncated,
    }


def _norm_item(t):
    typ = t.get("type") or ("episode" if t.get("show") else "track")
    show = (t.get("show") or {}).get("name")
    artist = ", ".join(a["name"] for a in t.get("artists") or []) or None
    known_id = isinstance(t.get("id"), str) and _SPOTIFY_ID.fullmatch(t["id"]) and typ in ENTITY_TYPES + ("chapter",)
    return {
        "type": typ,
        "id": t.get("id"),
        "url": canonical_url(typ, t["id"]) if known_id else None,
        "name": t.get("name"),
        "show": show,
        "artist": artist,
        "duration_ms": t.get("duration_ms"),
        "release_date": t.get("release_date"),
        "explicit": t.get("explicit"),
        "preview_url": http_url(t.get("preview_url") or (t.get("audio_preview_url"))),
    }


def fetch_entity_keyless(typ, eid):
    """Embed-endpoint fallback: name + possibly TRUNCATED item list."""
    embed = f"https://open.spotify.com/embed/{typ}/{eid}"
    r = requests.get(embed, headers={**UA, "Accept-Language": "en"}, timeout=30)
    if r.status_code in (404, 410):
        raise NotFound(embed, r.status_code)
    if r.status_code != 200:
        die(f"embed fetch failed ({r.status_code}) and no API credentials configured", 3)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        die("embed page carried no __NEXT_DATA__ and no API credentials configured", 3)
    ent = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    items = []
    for n, t in enumerate(ent.get("trackList") or [], 1):
        uri = t.get("uri") or ""
        um = re.match(r"spotify:([a-z]+):([A-Za-z0-9]+)", uri)
        ityp, iid = (um.group(1), um.group(2)) if um else (None, None)
        items.append(
            {
                "n": n,
                "type": ityp,
                "id": iid,
                "url": canonical_url(ityp, iid) if iid else None,
                "name": t.get("title"),
                "show": t.get("subtitle"),
                "artist": t.get("subtitle") if ityp == "track" else None,
                "duration_ms": t.get("duration"),
                "release_date": None,
                "explicit": t.get("isExplicit"),
                "preview_url": http_url((t.get("audioPreview") or {}).get("url")),
            }
        )
    return {
        "type": typ,
        "id": eid,
        "url": canonical_url(typ, eid),
        "name": ent.get("name"),
        "creator": ent.get("subtitle"),
        "description": "",
        "images": [u for u in (http_url(x.get("url")) for x in ((ent.get("coverArt") or {}).get("sources") or [])[:1] if isinstance(x, dict)) if u],
        "release_date": ent.get("releaseDate"),
        "total": len(items),
        "items": items,
        "keyless": True,
    }


# ------------------------------------------------------------------ RSS feeds


def itunes_feed_candidates(show_name, limit=5):
    r = requests.get(
        ITUNES_SEARCH, params={"term": show_name, "media": "podcast", "limit": limit}, headers=UA, timeout=30
    )
    if r.status_code != 200:
        # Not `[]`: "iTunes refused" and "no such show" are different answers,
        # and only the second makes every episode honestly "no open feed match".
        raise RuntimeError(f"iTunes lookup -> HTTP {r.status_code}")
    return [
        {"show": x.get("collectionName"), "feed": x.get("feedUrl"), "artist": x.get("artistName")}
        for x in r.json().get("results", [])
        if http_url(x.get("feedUrl"))  # http(s) only: this URL is fetched, recorded and shown
    ]


def _norm_title(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _parse_rss(feed_url):
    if not http_url(feed_url):
        raise ValueError("feed URL is not http(s)")
    r = requests.get(feed_url, headers=UA, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    out = []
    for it in root.iter("item"):
        enc = it.find("enclosure")
        dur = it.findtext("itunes:duration", default="", namespaces=ns)
        secs = None
        if dur:
            parts = [int(p) for p in dur.split(":") if p.strip().isdigit()]
            if len(parts) == 1:
                secs = parts[0]
            elif len(parts) == 2:
                secs = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                secs = parts[0] * 3600 + parts[1] * 60 + parts[2]
        out.append(
            {
                "title": it.findtext("title", default="").strip(),
                "pub_date": it.findtext("pubDate", default="").strip(),
                "duration_s": secs,
                # http(s) only. The plugin's downloader opens any scheme it is
                # handed — `file:///etc/passwd` would become a pending asset —
                # and the feed is whichever one iTunes matched to the show NAME.
                "enclosure": http_url(enc.get("url")) if enc is not None else None,
                "bytes": int(enc.get("length") or 0) if enc is not None else None,
            }
        )
    return out


def match_episode(feed_items, name, duration_ms, tol_s=150):
    """Best feed item for a Spotify episode: title tokens + duration window."""
    want, want_s = _norm_title(name), (duration_ms or 0) / 1000
    best, best_score = None, 0.0
    for fi in feed_items:
        if not fi["enclosure"]:
            continue
        have = _norm_title(fi["title"])
        a, b = set(want.split()), set(have.split())
        overlap = len(a & b) / max(1, len(a))
        dur_ok = fi["duration_s"] is None or not want_s or abs(fi["duration_s"] - want_s) <= tol_s
        score = overlap + (0.5 if want in have or have in want else 0) + (0.25 if dur_ok else -0.5)
        if score > best_score:
            best, best_score = fi, score
    return (best, round(best_score, 2)) if best_score >= 1.0 else (None, round(best_score, 2))


# -------------------------------------------------------------------- capture


# What the spawner leaves beside a worker, what the extractor reads, and what
# travels back out of the slice. The names are the host's; this unit only
# reads the first and writes the other two.
TICKET_NAME = "ticket.json"
CAPTURE_NAME = "capture.json"
REPORT_NAME = "report.json"

# Frontmatter keys another verb owns — never offered in `capture.json`'s
# `frontmatter` object, whatever the entity is called.
FRONTMATTER_RESERVED = ("status", "document_id", "document_revision", "harvested", "extracted", "title", "resource")

ASSET_POLICIES = ("reference", "download", "download-audio")

# The tail `assets.py download` takes for each `harvest.assets` policy. The
# flags are the plugin script's own (`--mode`, `--skip-types`); the reading of
# `download-audio` as "the enclosures, not the cover" is this unit's.
ASSET_ARGS = {"reference": ["--mode", "reference"], "download": [], "download-audio": ["--skip-types", "image"]}

OUTCOMES = ("ok", "partial", "skipped", "unchanged", "gone", "failed")
WHYS = ("denied", "timeout", "auth", "error")

# How the slice proxy words a refusal (measured by the plugin's own fetch
# worker; repeated here because a unit imports nothing from the plugin).
DENIED_MARKERS = ("tunnel connection failed", "not in the allowlist")


def read_ticket(cap):
    """`ticket.json` beside the capture, or `{}` — a hand run has none."""
    try:
        data = json.loads((Path(cap) / TICKET_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def host_of(url):
    return (urlsplit(url or "").hostname or "").lower()


def why_for(said):
    """One of the report's four `why` words, read off what a failure said."""
    low = str(said or "").lower()
    if any(m in low for m in DENIED_MARKERS):
        return "denied"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if re.search(r"\b(401|403|407)\b", low) or "auth/paywall" in low:
        return "auth"
    return "error"


def already_held(ticket, item):
    """Is this entity a page `known[]` says the job already has? Matched on
    `resource` exactly — the key the page carries."""
    known = ticket.get("known")
    if not isinstance(known, list):
        return False
    return any(isinstance(k, dict) and k.get("resource") == item for k in known)


def plan_capture(ent, *, market="US", min_date=None, no_audio=False):
    """An already-fetched entity -> `(meta, assets)`: items filtered by date,
    each item's audio route decided, the pending asset manifest built.

    The only network here is the open-feed lookup (`itunes_feed_candidates`,
    `_parse_rss`), skipped entirely under `no_audio`. A lookup that could not
    be reached is recorded in `meta["unreachable"]` rather than swallowed: it
    is what `report` turns into `missing[]`, and `missing[]` is what the
    foreman widens egress from.
    """
    ent = {**ent, "items": [dict(i) for i in ent.get("items") or [] if isinstance(i, dict)]}
    # An entity can arrive from a file (`--entity-json`) as well as from the
    # API, so the URL rule is applied HERE, where both roads meet: http(s) or
    # nothing, for the cover, each item's link and each preview.
    ent["images"] = [u for u in (http_url(x) for x in ent.get("images") or []) if u]
    for i in ent["items"]:
        i["url"] = http_url(i.get("url"))
        i["preview_url"] = http_url(i.get("preview_url"))
    if min_date:
        ent["items"] = [i for i in ent["items"] if not i.get("release_date") or i["release_date"] >= min_date]

    assets, drm_refs, feeds, unreachable = [], [], {}, []
    for img in [u for u in ent.get("images") or [] if u]:
        assets.append(
            {
                "id": f"asset-image-{len(assets) + 1:03d}",
                "type": "image",
                "src_url": img,
                "status": "pending",
                "alt": ent.get("name"),
            }
        )
    audio_n = 0
    for it in ent["items"]:
        if no_audio:
            break
        if it["type"] == "episode" and it.get("show"):
            fkey = it["show"]
            if fkey not in feeds:
                feeds[fkey] = {"candidates": [], "items": None, "feed": None}
                try:
                    feeds[fkey]["candidates"] = itunes_feed_candidates(fkey)
                except Exception as e:  # noqa: BLE001 — an unreachable lookup is a report line, never a traceback
                    unreachable.append({"host": host_of(ITUNES_SEARCH), "url": ITUNES_SEARCH, "why": why_for(e)})
                feeds[fkey]["candidates"] = [c for c in feeds[fkey]["candidates"] if http_url(c.get("feed"))]
                for c in feeds[fkey]["candidates"]:
                    try:
                        feeds[fkey]["items"] = _parse_rss(c["feed"])
                        feeds[fkey]["feed"] = c["feed"]
                        break
                    except Exception as e:  # noqa: BLE001 — try the next candidate, but say this one failed
                        unreachable.append({"host": host_of(c["feed"]), "url": c["feed"], "why": why_for(e)})
                        continue
            fi, score = (None, 0)
            if feeds[fkey]["items"]:
                fi, score = match_episode(feeds[fkey]["items"], it.get("name"), it.get("duration_ms"))
            if fi and not http_url(fi.get("enclosure")):
                fi = None  # `_parse_rss` already drops these; a replaced parser does not get to skip the rule
            if fi:
                audio_n += 1
                assets.append(
                    {
                        "id": f"asset-audio-{audio_n:03d}",
                        "type": "audio",
                        "src_url": fi["enclosure"],
                        "status": "pending",
                        "player_url": it["url"],
                        "note": f"open RSS enclosure ({feeds[fkey]['feed']}); matched '{_one_line(fi['title'])}' score={score}",
                    }
                )
                it["audio"] = {"route": "rss", "asset_id": f"asset-audio-{audio_n:03d}", "src_url": fi["enclosure"]}
            else:
                drm_refs.append(_drm_ref(it, "episode not matched in any public feed"))
                it["audio"] = {"route": "none"}
        elif it["type"] in ("track", "chapter"):
            drm_refs.append(_drm_ref(it, "Spotify catalog audio is DRM-protected"))
            it["audio"] = {"route": "drm"}
    meta = {
        **ent,
        "market": market,
        "feeds": {k: {"feed": v["feed"], "candidates": v["candidates"]} for k, v in feeds.items()},
        "drm_refs": drm_refs,
        "unreachable": unreachable,
        "truncated": ent.get("truncated") or None,
        "counts": {"items": len(ent["items"]), "audio_resolved": audio_n, "drm_or_unmatched": len(drm_refs)},
    }
    return meta, assets


def capture_frontmatter(meta):
    """The entity's exact facts, as the flat object `capture.json` carries
    under `frontmatter` — scalars only, nothing another verb owns, and no key
    for a fact the venue did not declare (an absent `published` is absent,
    never a guess)."""
    counts = meta.get("counts") or {}
    items = meta.get("items") or []
    name, show = _one_line(meta.get("name")), items[0].get("show") if meta.get("type") == "episode" and items else None
    facts = {
        "type": meta.get("type"),
        "venue": "spotify",
        "spotify_id": meta.get("id"),
        # The venue's own name, when the filename rule made `title` differ from it.
        "source_title": name if name and name != page_title(meta) else None,
        "author": _one_line(meta.get("creator")) or None,
        "show": _one_line(show) or None,
        "published": _published_day(meta.get("release_date")) or None,
        "items": counts.get("items", len(items)),
        "audio_resolved": counts.get("audio_resolved", 0),
        "drm_or_unmatched": counts.get("drm_or_unmatched", 0),
        "keyless": bool(meta.get("keyless")),
        "market": meta.get("market") or None,
    }
    return {k: v for k, v in facts.items() if v is not None and k not in FRONTMATTER_RESERVED}


def capture_record(meta, *, slug, item, fetched_at=None):
    """`capture.json`: the whole agreement with the generic extractor. The body
    is the markdown page this unit rendered; the extractor takes it verbatim."""
    return {
        "v": 1,
        "slug": slug,
        "item": item or meta["url"],
        # Names the page's FILE: the filename-safe form. The venue's own name is
        # the body's H1, and `frontmatter.source_title` when the two differ.
        "title": page_title(meta),
        "body": "page.md",
        "content_type": "text/markdown",
        "fetched_at": fetched_at or now_iso(),
        "frontmatter": capture_frontmatter(meta),
    }


def _dump(path, data):
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_capture_dir(cap, meta, assets, *, slug=None, item=None, fetched_at=None):
    """Every file a capture leaves, `capture.json` last — it names `page.md`,
    so it is written only once the page is on disk."""
    cap = Path(cap)
    cap.mkdir(parents=True, exist_ok=True)
    _dump(cap / "meta.json", meta)
    _dump(cap / "items.json", meta["items"])
    _dump(cap / "assets.json", assets)
    (cap / "page.md").write_text(render_page_md(meta), encoding="utf-8")
    record = capture_record(meta, slug=slug, item=item, fetched_at=fetched_at)
    _dump(cap / CAPTURE_NAME, record)
    return record


# What this script itself leaves in a capture dir. A single-entity capture dir
# is STABLE across pulls and respawns, so none of it may answer for a later run.
OWN_FILES = ("meta.json", "items.json", "assets.json", "page.md")


def cmd_capture(a):
    # `llm-wiki-ops run` starts this script in the WIKI ROOT, not in the capture
    # directory the worker stands in: `--capture-dir` is the ticket's
    # `capture_dir`, wiki-relative, verbatim — and a directory with no ticket in
    # it is refused rather than created, unless this is plainly a hand run.
    cap = Path(a.capture_dir)
    ticket = read_ticket(cap)
    if not ticket and not a.url:
        die(
            f"{a.capture_dir} holds no {TICKET_NAME}. --capture-dir is the ticket's `capture_dir`, WIKI-RELATIVE "
            f"(this script runs from the wiki root, {Path.cwd()}). A hand run names the entity URL as well."
        )
    # FIRST, before anything below can refuse: a respawn, and the next pull,
    # land in this same directory. What an earlier run left must not answer
    # for this one — `report` reads `capture.json` as "it landed", `apply`
    # never checks whose `report.json` it is reading, and a refusal that left
    # the last run's `ok` report in place would be read as this run's.
    for stale in (CAPTURE_NAME, REPORT_NAME):
        (cap / stale).unlink(missing_ok=True)
    url = a.url or ticket.get("item") or ticket.get("target")
    if not url:
        die(f"no entity URL: pass one, or run beside a {TICKET_NAME} that names an item")
    slug = a.slug or ticket.get("slug")
    min_date = a.min_date or ticket.get("min_date")
    if min_date and not _published_day(min_date):
        die(f"min date {min_date!r} is not a YYYY-MM-DD day")
    policy = a.assets or (ticket.get("harvest") or {}).get("assets") or "download"
    if policy not in ASSET_POLICIES:
        die(f"unknown assets policy {policy!r} (one of: {', '.join(ASSET_POLICIES)})")
    cap.mkdir(parents=True, exist_ok=True)

    def verdict(outcome, reason, code, **said):
        """A run that ends here, with nothing captured: the report is the whole
        of it, written from THIS run's facts and nothing on disk."""
        if ticket:
            _dump(cap / REPORT_NAME, build_report(cap, ticket, outcome=outcome, reason=reason))
        print(json.dumps({"url": url, **said, "outcome": outcome, "reason": reason, "capture_dir": str(cap)}, indent=1))
        sys.exit(code)

    if ticket and already_held(ticket, url) and not ticket.get("refresh"):
        # The pull whose target the job already holds: a designed non-event.
        verdict("skipped", f"known: {url}", 0, skipped=True)

    for stale in OWN_FILES:  # past the skip: this run re-plans them all, or dies and must not leave the last run's
        (cap / stale).unlink(missing_ok=True)

    typ, eid = parse_entity(url)
    unreadable = [] if ticket else None  # only a ticketed run may degrade on an unreadable store — see load_auth
    if a.entity_json:
        try:
            ent = json.loads(Path(a.entity_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            die(f"cannot read --entity-json {a.entity_json} (relative paths are wiki-relative): {e}")
        if not isinstance(ent, dict) or (ent.get("type"), ent.get("id")) != (typ, eid):
            die(f"--entity-json is not the {typ} {eid} that {url} names")
    else:
        try:
            named = ticket.get("credential")
            token = None if a.keyless else get_token(wiki_root(), named if isinstance(named, str) and named else CREDENTIAL, unreadable)
            ent = fetch_entity(token, typ, eid, a.market) if token else fetch_entity_keyless(typ, eid)
        except NotFound as gone:
            # 404/410 for the entity itself. On a refresh ticket that is the
            # `gone` verdict (agent-loop), and `apply` dates it on the page; on
            # a first pull there is nothing to be gone FROM, so it failed.
            # Spotify answers 404 for "not offered in this market" too.
            reason = (
                f"{typ} {eid}: the venue answered {gone.status} at {gone.url} — removed, never there, "
                f"or not offered in market {a.market}"
            )
            if ticket.get("refresh"):
                verdict("gone", reason, 0, gone=True)
            verdict("failed", reason, 3, gone=True)

    meta, assets = plan_capture(ent, market=a.market, min_date=min_date, no_audio=a.no_audio)
    if unreadable:
        # The payload is THERE and this process may not open it — a slice is
        # granted no payload for a unit declaring `requires.credential: false`.
        # Captured keyless, and said out loud: the report turns this into
        # `partial` + a `missing[]` entry `why: auth`, and the page says it too.
        meta["auth"] = {"url": f"{API}/{typ}s/{eid}", "why": "auth", "said": unreadable[0][:200]}
    record = write_capture_dir(cap, meta, assets, slug=slug, item=url)
    counts = meta["counts"]
    summary = {
        "url": meta["url"],
        "item": record["item"],
        "name": meta["name"],
        "type": typ,
        "items": counts["items"],
        "audio_resolved": counts["audio_resolved"],
        "drm_or_unmatched": counts["drm_or_unmatched"],
        "unreachable": len(meta["unreachable"]),
        "keyless": meta["keyless"],
        "truncated": bool(meta.get("truncated")),
        "credential_unreadable": bool(meta.get("auth")),
        # The venue's native creation timestamp, under the protocol's name
        # for it — the same value `capture.json`'s `frontmatter.published`
        # carries. Emitted only at day precision: Spotify's `release_date`
        # follows its `release_date_precision`, so an album can legitimately
        # return a bare "1979", which is not a publication DAY.
        "published": _published_day(meta.get("release_date")),
        # The job's `harvest.assets`, and the tail `assets.py download` takes for it.
        "assets": policy,
        "assets_args": ASSET_ARGS[policy],
        "capture_dir": str(cap),
        "wrote": ["meta.json", "items.json", "assets.json", "page.md", CAPTURE_NAME],
    }
    print(json.dumps(summary, indent=1))
    if meta["items"] and counts["audio_resolved"] == 0 and not a.no_audio:
        sys.exit(4)


# --------------------------------------------------------------------- report


def build_report(cap, ticket, *, outcome=None, reason=None, ticket_id=None, capture_dir=None, extra_missing=()):
    """`report.json` for one entity capture, read off what is on disk.

    `captured[]` names the capture dir exactly when `capture.json` is there.
    `missing[]` is every failed asset in `assets.json`, every feed lookup the
    capture could not reach (`meta.json` `unreachable`), and whatever the
    caller adds. The outcome, unless the caller names one: `failed` with no
    capture; `partial` when something is missing or the item list came from
    the keyless embed (possibly truncated); else `ok`.
    """
    cap = Path(cap)

    def load(name, default):
        try:
            return json.loads((cap / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    # `skipped` and `gone` fetched nothing THIS run: whatever the directory
    # holds is an earlier pull's, and its `missing[]` is not this run's to repeat.
    fresh = outcome not in ("skipped", "gone")
    record = load(CAPTURE_NAME, None) if fresh else None
    meta = load("meta.json", {}) if fresh else {}
    assets = load("assets.json", []) if fresh else []
    if not isinstance(meta, dict):
        meta = {}
    where = capture_dir or ticket.get("capture_dir") or str(cap)
    captured, missing, why_partial = [], [], []
    if isinstance(record, dict):
        captured.append({"item": record.get("item"), "dir": where, "title": record.get("title")})
    for m in (meta.get("unreachable") or []) if isinstance(meta, dict) else []:
        if isinstance(m, dict) and m.get("url"):
            missing.append({"host": m.get("host") or host_of(m["url"]), "url": m["url"], "why": m.get("why") if m.get("why") in WHYS else "error"})
    for entry in assets if isinstance(assets, list) else []:
        if isinstance(entry, dict) and entry.get("status") == "failed" and entry.get("src_url"):
            missing.append({"host": host_of(entry["src_url"]), "url": entry["src_url"], "why": why_for(entry.get("error"))})
    missing.extend(extra_missing)
    if missing:
        why_partial.append(f"{len(missing)} url(s) not reached")
    cut, auth = meta.get("truncated"), meta.get("auth")
    if isinstance(cut, dict) and cut.get("url"):
        missing.append({"host": host_of(cut["url"]), "url": cut["url"], "why": cut.get("why") if cut.get("why") in WHYS else "error"})
        why_partial.append(
            f"item list TRUNCATED at {cut.get('got')} of {cut.get('expected') or '?'}: a page of it could not be "
            f"fetched ({cut.get('said')}) — re-run the ticket to complete it"
        )
    if isinstance(auth, dict) and auth.get("url"):
        missing.append({"host": host_of(auth["url"]), "url": auth["url"], "why": "auth"})
        why_partial.append(
            f"API credentials exist on this machine and could not be read here ({auth.get('said')}), so the API was "
            f"not used. Fix: a confined slice is granted no credential payload for this unit — see the unit's "
            f"INSTALL.md, 'Credentials under a confined harvest'"
        )
    if meta.get("keyless"):
        why_partial.append("keyless capture: the item list may be truncated")
    if outcome is None:
        outcome = "failed" if not captured else ("partial" if why_partial else "ok")
    if reason is None:
        if outcome == "failed":
            reason = f"no {CAPTURE_NAME} in {where}"
        elif outcome == "partial":
            reason = "; ".join(why_partial) or None
    return {
        "v": 1,
        "ticket": ticket_id or ticket.get("ticket"),
        "outcome": outcome,
        "reason": reason,
        "captured": captured,
        "written": [],
        "missing": missing,
        "discovered": [],
    }


TERMINAL = ("skipped", "gone", "failed")  # the verdicts `capture` writes itself, with nothing captured


def cmd_report(a):
    cap = Path(a.capture_dir)  # wiki-relative: `llm-wiki-ops run` starts this in the wiki root
    ticket = read_ticket(cap)
    if not ticket and not (a.ticket and a.dir):
        die(
            f"{a.capture_dir} holds no {TICKET_NAME}. --capture-dir is the ticket's `capture_dir`, WIKI-RELATIVE "
            f"(this script runs from the wiki root). A hand run passes --ticket <id> and --dir <capture_dir>."
        )
    # Read, then REMOVED, before anything below can refuse: a refusal that left
    # an earlier `ok` report in place would hand `apply` a success this run did
    # not have. (Not before the refusal above — a directory with no ticket in it
    # is not known to be ours.)
    try:
        prior = json.loads((cap / REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = None
    (cap / REPORT_NAME).unlink(missing_ok=True)
    extra = []
    for spec in a.missing or []:
        url, _, why = spec.rpartition("=")
        if not url or why not in WHYS:
            die(f"--missing takes <url>=<{'|'.join(WHYS)}>, got {spec!r}")
        extra.append({"host": host_of(url), "url": url, "why": why})
    ticket_id = a.ticket or ticket.get("ticket")
    if not ticket_id:
        die(f"no ticket id: pass --ticket, or run beside a {TICKET_NAME}")
    # `capture` may have ended this run with a verdict of its own (known, gone,
    # not found) and its reason: running `report` after it, as the flow says
    # to, must not flatten that into "no capture.json". The report on disk is
    # this run's only when it carries THIS ticket and no capture landed since —
    # `capture` removes any earlier report first, and the extractor's own
    # carries another ticket's id.
    outcome, reason = a.outcome, a.reason
    if (
        outcome is None
        and isinstance(prior, dict)
        and prior.get("ticket") == ticket_id
        and prior.get("outcome") in TERMINAL
        and not prior.get("captured")
        and not (cap / CAPTURE_NAME).exists()
    ):
        outcome, reason = prior["outcome"], reason or prior.get("reason")
    report = build_report(
        cap, ticket, outcome=outcome, reason=reason, ticket_id=ticket_id, capture_dir=a.dir, extra_missing=extra
    )
    if report["outcome"] in ("ok", "partial", "unchanged") and not report["captured"]:
        # agent-loop: a report claiming a capture with nothing captured fails its
        # ticket anyway — refuse to write the claim rather than let `apply` find it.
        die(f"outcome {report['outcome']!r} with nothing captured: there is no {CAPTURE_NAME} in {a.capture_dir}")
    _dump(cap / REPORT_NAME, report)
    print(json.dumps(report, indent=1))
    if report["outcome"] == "failed":
        sys.exit(1)


def _published_day(value):
    """A `release_date` as the protocol's `published` shape, or `""`.

    Its own three lines rather than a shared helper: a skill unit runs as
    a hosted script (the front door's `run` verb) and cannot import the plugin's
    `scripts/`, so the alternative is not reuse — it is a dependency the unit
    cannot express.

    Differs from `scripts/published_date.py::day_precision` in ONE direction
    only, deliberately: that one truncates a timestamp
    (`2019-03-04T10:00:00Z` -> `2019-03-04`) because it reads arbitrary web
    markup, while this reads one API field whose contract is a date, never a
    timestamp — `release_date` follows `release_date_precision` and is
    `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. A timestamp here would mean the API
    changed shape, so rejecting it beats salvaging a day out of it.

    Everything else matches, and must: the same calendar validation (day-
    SHAPED is not a day) and the same non-string tolerance, since this reads
    parsed JSON where a venue can supply a number.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return ""
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return ""  # day-SHAPED is not a day: 0000-00-00, 2019-02-30
    return value


def _drm_ref(it, why):
    return {
        "id": it["id"],
        "type": it["type"],
        "name": it["name"],
        "url": it["url"],
        "status": "drm_protected",
        "player_url": it["url"],
        "preview_url": it.get("preview_url"),
        "note": why,
    }


def render_page_md(meta):
    """The page BODY the generic extractor takes verbatim. Never a `---`
    frontmatter block: the extractor prepends its own, and a second one
    corrupts the page. The entity's facts ride as a plain list instead — the
    same keys `capture.json`'s `frontmatter` object carries — so nothing is
    lost while the extractor does not merge that object.

    Every venue string in here is DATA: names and fact values are folded to
    one line, the description is a blockquote, table cells are folded and
    pipe-escaped, dates are shape-checked, and a URL is http(s) or absent.
    """
    url = _md_url(meta["url"])
    L = [f"# {_one_line(meta.get('name')) or 'Spotify ' + _one_line(meta['type']) + ' ' + _one_line(meta['id'])}", ""]
    if _one_line(meta.get("creator")):
        L.append(f"By **{_one_line(meta['creator'])}** — Spotify {_one_line(meta['type'])}: {url}")
    else:
        L.append(f"Spotify {_one_line(meta['type'])}: {url}")
    L.append("")
    for key, value in capture_frontmatter(meta).items():
        shown = str(value).lower() if isinstance(value, bool) else _one_line(value)
        L.append(f"- {key}: {shown}")
    if meta.get("keyless"):
        L.append("")
        L.append(
            "> [!warning] Keyless capture — item list may be truncated; "
            "configure API credentials and re-capture for the full set."
        )
    if isinstance(meta.get("auth"), dict):
        L.append("")
        L.append(
            "> [!warning] API credentials exist on this machine but could not be read from this run, "
            "so the API was not used — see the unit's INSTALL.md, 'Credentials under a confined harvest'."
        )
    cut = meta.get("truncated")
    if isinstance(cut, dict):
        L.append("")
        L.append(
            f"> [!warning] Item list TRUNCATED at {_one_line(cut.get('got'))} of {_one_line(cut.get('expected') or '?')} "
            "— a page of it could not be fetched. Re-capture for the full set."
        )
    if str(meta.get("description") or "").strip():
        L += ["", *_quoted(str(meta["description"]).strip())]
    if meta.get("items"):
        L += ["", "| # | Item | Duration | Released | Audio | Spotify |", "|---|---|---|---|---|---|"]
        for it in meta["items"]:
            route = (it.get("audio") or {}).get("route", "-")
            audio = {"rss": "open RSS feed", "drm": "DRM — listen at source", "none": "no open feed match"}.get(route, "-")
            src = http_url((it.get("audio") or {}).get("src_url"))
            if route == "rss" and src:
                audio = f"[open RSS feed]({_md_url(src)})"
            released = it.get("release_date")
            released = released if isinstance(released, str) and _DATE_SHAPED.fullmatch(released) else "?"
            duration = it.get("duration_ms") if isinstance(it.get("duration_ms"), (int, float)) else None
            link = _md_url(it["url"]) if http_url(it.get("url")) else "-"
            L.append(f"| {_cell(it.get('n'))} | {_cell(it.get('name')) or '?'} | {ms_to_hms(duration)} | {released} | {audio} | {link} |")
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------- CLI


def read_secret():
    """The client secret from a prompt that does not echo (a terminal), else
    the first line of stdin — never from argv, which every user on the box can
    read in `ps` and which the shell writes to its history."""
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass("Spotify client secret (not echoed): ").strip()
    return (sys.stdin.readline() if sys.stdin is not None else "").strip()


def cmd_auth(a):
    root = wiki_root()
    if root is None:
        die("no wiki found above the current directory — run from the wiki root")
    data = load_auth(root)
    if a.client_id:
        data["client_id"] = a.client_id
    given = getattr(a, "client_secret", None)
    if given:
        print(
            "warning: --client-secret puts the secret in argv (`ps`, shell history) — deprecated; "
            "omit it and the secret is prompted for, or read from stdin",
            file=sys.stderr,
        )
        data["client_secret"] = given
    elif not data.get("client_id"):
        die("need a client id: auth --client-id <id> (the secret is then prompted for, or read from stdin)")
    elif a.client_id or not data.get("client_secret"):
        data["client_secret"] = read_secret()
    if not data.get("client_id") or not data.get("client_secret"):
        die("need a client id (--client-id) and a client secret (prompted, or one line on stdin)")
    # A payload hand-carried from the old store may still have these — never
    # persist a bearer token to the store; it now lives only in-process.
    data.pop("token", None)
    data.pop("expires_at", None)
    try:
        rc, answer = _ops(root, "credential", "set", CREDENTIAL, input=json.dumps(data, indent=1).encode())
    except OSError as e:
        die(f"credential store unreachable ({e.__class__.__name__}: {e})")
    if rc != 0:
        die(f"credential store failed ({rc}): {answer.get('error', '')}")
    print(json.dumps({"stored": "spotify", "token_ok": bool(get_token(root))}))


def cmd_search(a):
    token = get_token(wiki_root())
    if not token:
        die("search needs API credentials — run: spotify.py auth --client-id <id> (the secret is prompted for)", 3)
    types = a.type or "episode,show,playlist,album,audiobook"
    d = api_get(token, "/search", {"q": a.query, "type": types, "limit": a.limit, "market": a.market})
    rows = []
    for key, bucket in (d or {}).items():
        for x in (bucket or {}).get("items") or []:
            if not x:
                continue
            i = _norm_item(x)
            rows.append(
                {
                    "type": i["type"] or key.rstrip("s"),
                    "name": i["name"] or x.get("name"),
                    "by": i["show"] or i["artist"] or (x.get("publisher") if x else None),
                    "release_date": i["release_date"],
                    "duration": ms_to_hms(i["duration_ms"]) if i["duration_ms"] else None,
                    "url": i["url"] or canonical_url(key.rstrip("s"), x["id"]),
                }
            )
    print(json.dumps({"query": a.query, "results": rows}, indent=1))


def cmd_meta(a):
    typ, eid = parse_entity(a.url)
    token = None if a.keyless else get_token(wiki_root())
    ent = fetch_entity(token, typ, eid, a.market) if token else fetch_entity_keyless(typ, eid)
    print(json.dumps(ent, indent=1))


def cmd_resolve_feed(a):
    name = a.show_name
    if not name and a.url:
        typ, eid = parse_entity(a.url)
        token = get_token(wiki_root())
        if token:
            e = api_get(token, f"/{typ}s/{eid}", {"market": a.market})
            name = (e.get("show") or {}).get("name") if typ == "episode" else e.get("name")
        else:
            name = fetch_entity_keyless(typ, eid).get("creator")
    if not name:
        die("pass --show-name or a show/episode URL")
    cands = itunes_feed_candidates(name)
    out = {"show_name": name, "candidates": cands, "feed": None, "episodes": []}
    for c in cands:
        try:
            out["episodes"] = _parse_rss(c["feed"])
            out["feed"] = c["feed"]
            break
        except Exception:
            continue
    if a.limit and out["episodes"]:
        out["episodes"] = out["episodes"][: a.limit]
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("auth", help="store + test client credentials")
    c.add_argument("--client-id", help="the app's client id; the SECRET is then prompted for (or read from stdin)")
    c.add_argument("--client-secret", help="DEPRECATED — argv is world-readable; omit it and the secret is prompted for")
    c.set_defaults(fn=cmd_auth)

    c = sub.add_parser("search", help="search the catalog, JSON rows out")
    c.add_argument("query")
    c.add_argument("--type", help="comma list: episode,show,playlist,album,track,audiobook")
    c.add_argument("--limit", type=int, default=10)
    c.add_argument("--market", default="US")
    c.set_defaults(fn=cmd_search)

    c = sub.add_parser("meta", help="entity metadata JSON (full item pagination)")
    c.add_argument("url")
    c.add_argument("--market", default="US")
    c.add_argument("--keyless", action="store_true", help="force embed fallback")
    c.set_defaults(fn=cmd_meta)

    c = sub.add_parser("resolve-feed", help="show/episode -> public RSS feed")
    c.add_argument("url", nargs="?")
    c.add_argument("--show-name")
    c.add_argument("--market", default="US")
    c.add_argument("--limit", type=int, default=0, help="cap episodes in output")
    c.set_defaults(fn=cmd_resolve_feed)

    c = sub.add_parser(
        "capture",
        help="capture an entity into --capture-dir: meta.json, items.json, assets.json, page.md, capture.json",
    )
    c.add_argument("url", nargs="?", help=f"entity URL/URI; default: `item` in <capture-dir>/{TICKET_NAME}")
    c.add_argument(
        "--capture-dir", required=True,
        help=f"the ticket's `capture_dir`, WIKI-RELATIVE and verbatim (holds {TICKET_NAME}); the script runs from the wiki root",
    )
    c.add_argument("--slug", help=f"job slug for {CAPTURE_NAME}; default: the ticket's `slug`")
    c.add_argument("--market", default="US")
    c.add_argument("--min-date", help="drop items released before YYYY-MM-DD; default: the ticket's `min_date`")
    c.add_argument(
        "--assets", choices=ASSET_POLICIES, help="asset policy echoed in the summary; default: the ticket's `harvest.assets`"
    )
    c.add_argument("--keyless", action="store_true", help="force embed fallback")
    c.add_argument("--no-audio", action="store_true", help="metadata + images only (no feed lookup)")
    c.add_argument("--entity-json", help="an already-fetched entity (what `meta` prints) instead of calling the API; wiki-relative")
    c.set_defaults(fn=cmd_capture)

    c = sub.add_parser("report", help=f"write {REPORT_NAME} from what the capture dir holds — run it LAST")
    c.add_argument("--capture-dir", required=True, help="the ticket's `capture_dir`, WIKI-RELATIVE and verbatim")
    c.add_argument("--ticket", help=f"ticket id; default: `ticket` in <capture-dir>/{TICKET_NAME}")
    c.add_argument("--dir", help="wiki-relative capture dir for captured[]; default: the ticket's `capture_dir`")
    c.add_argument("--outcome", choices=OUTCOMES, help="override the outcome read off the capture dir")
    c.add_argument("--reason")
    c.add_argument("--missing", action="append", metavar="URL=WHY", help=f"a url not reached; WHY is {'|'.join(WHYS)}")
    c.set_defaults(fn=cmd_report)

    a = ap.parse_args()
    try:
        a.fn(a)
    except NotFound as gone:  # `meta`, `search`, `resolve-feed`; `capture` turns it into a verdict itself
        die(f"not found: the venue answered {gone.status} at {gone.url}", 3)


if __name__ == "__main__":
    main()
