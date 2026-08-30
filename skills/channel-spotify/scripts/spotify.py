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
                page.md, assets.json (pending cover art + audio enclosures)

Inputs:  entity URL (https://open.spotify.com/<type>/<id>) or spotify:<type>:<id>
Outputs: JSON on stdout (all subcommands); capture writes files, stdout JSON
         is the run summary. Exit 4 from capture = entity captured but no
         audio was resolvable (all-DRM or no feed match) — metadata-only.

Auth: the `spotify` credential, {"client_id": ..., "client_secret": ...}
      (env SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET override). The token is
      held in memory for the process lifetime, never written to the store.
      No user OAuth: private playlists and the user library are out of
      scope.

Keyless degradation: with no credentials, `meta` and `capture` fall back to
the public embed endpoint (open.spotify.com/embed/...), which exposes the
entity name and a POSSIBLY TRUNCATED item list. The output carries
"keyless": true so nobody mistakes it for a complete enumeration.

History:
  2026-08-01  Written for this wiki (first target: the $100M Money Models
              audiobook playlist, which turned out to be episodes of the
              openly-distributed show "The Game with Alex Hormozi").
  2026-08-04  Auth resolves through the wiki's credential store (`credential
              get|set spotify` via the shim); the token is held in memory
              for the process lifetime, never cached to disk.
"""

import argparse
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
from urllib.parse import urlsplit

import requests

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ITUNES_SEARCH = "https://itunes.apple.com/search"
ENTITY_TYPES = ("playlist", "show", "episode", "album", "track", "audiobook", "artist")
UA = {"User-Agent": "llm-wiki channel-spotify (+local knowledgebase pipeline)"}


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def wiki_root(start=None):
    # NOT `Path.is_file()`: it swallows `PermissionError` and reports False,
    # which would make a denied `.llm-wiki.toml` look like "no wiki here" —
    # walking on to the next parent and eventually degrading keyless —
    # instead of surfacing the real problem. Same distinction
    # `credentials.py`'s `profile_dir` draws by name (`os.stat`, not
    # `Path.is_dir()`): FileNotFoundError means genuinely absent, any other
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


def _shim(root):
    """The wiki's front door. `wiki_root()` may return None — outside a wiki
    there is no store to reach, and the caller degrades keyless. The tree's
    name comes from `LLM_WIKI_OPS_DIRNAME` (exported by the front door that
    ran this script); absent, the store is likewise unreachable and the
    caller degrades the same way rather than this script guessing a tree."""
    dirname = os.environ.get("LLM_WIKI_OPS_DIRNAME") or None
    if root is None or dirname is None:
        return None
    return Path(root) / dirname / "bin" / "llm-wiki-ops"


def load_auth(root):
    """Env override, else the wiki's credential store.

    Returns a bare dict (the old tuple's second element was the file path,
    which no longer exists). `{}` means no credentials — the keyless path is
    a documented degradation, not an error.
    """
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if cid and sec:
        return {"client_id": cid, "client_secret": sec}
    shim = _shim(root)
    if shim is None:
        return {}
    try:
        proc = subprocess.run([str(shim), "credential", "get", "spotify"], capture_output=True)
    except OSError as e:
        die(f"credential store unreachable ({e.__class__.__name__}: {e})")
    if proc.returncode == 1:  # absent — degrade keyless
        return {}
    if proc.returncode != 0:  # 3 = unreadable, 2 = usage: real errors
        die(f"credential lookup failed ({proc.returncode}): {proc.stderr.decode()[:200]}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as e:
        die(f"credential store returned invalid JSON: {e}")
    if not isinstance(data, dict):
        die("credential store returned a non-object payload")
    return data


# Client-credentials bearer token, held for this process only — never
# written back to the credential store. {"token": ..., "expires_at": ...}.
_token_cache = None


def get_token(root):
    """Client-credentials token, cached in memory for this process. None = no creds."""
    global _token_cache
    data = load_auth(root)
    if not data.get("client_id") or not data.get("client_secret"):
        return None
    if _token_cache and _token_cache["expires_at"] > time.time() + 30:
        return _token_cache["token"]
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(data["client_id"], data["client_secret"]),
        timeout=30,
    )
    if r.status_code == 429:
        time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(data["client_id"], data["client_secret"]),
            timeout=30,
        )
    if r.status_code != 200:
        die(f"token request failed ({r.status_code}): {r.text[:200]}")
    tok = r.json()
    _token_cache = {"token": tok["access_token"], "expires_at": time.time() + tok.get("expires_in", 3600)}
    return _token_cache["token"]


def api_get(token, path, params=None):
    for attempt in range(4):
        r = requests.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}", **UA}, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")) + 1)
            continue
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            die(f"API {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()
    die(f"API {path}: rate-limited after retries")


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
    return typ, eid


def canonical_url(typ, eid):
    return f"https://open.spotify.com/{typ}/{eid}"


def paged(token, first_page, key="items"):
    out, page = [], first_page
    while page:
        out.extend(page.get(key) or [])
        nxt = page.get("next")
        if not nxt:
            break
        r = requests.get(nxt, headers={"Authorization": f"Bearer {token}", **UA}, timeout=30)
        if r.status_code != 200:
            break
        page = r.json()
    return out


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
    items = []
    if typ == "playlist":
        e = api_get(token, f"/playlists/{eid}", p)
        raw = paged(token, e.get("tracks") or {})
        for r in raw:
            t = r.get("track") or r.get("item") or {}
            if not t:
                continue
            items.append(_norm_item(t))
        creator = (e.get("owner") or {}).get("display_name")
    elif typ == "show":
        e = api_get(token, f"/shows/{eid}", p)
        raw = paged(token, api_get(token, f"/shows/{eid}/episodes", {**p, "limit": 50}) or {})
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
        raw = paged(token, e.get("tracks") or {})
        items = [_norm_item({**x, "type": "track", "album": {"name": e.get("name")}}) for x in raw if x]
        creator = ", ".join(a["name"] for a in e.get("artists") or [])
    elif typ == "track":
        e = api_get(token, f"/tracks/{eid}", p)
        items = [_norm_item({**e, "type": "track"})]
        creator = ", ".join(a["name"] for a in e.get("artists") or [])
    elif typ == "audiobook":
        e = api_get(token, f"/audiobooks/{eid}", p)
        raw = paged(token, api_get(token, f"/audiobooks/{eid}/chapters", {**p, "limit": 50}) or {})
        items = [_norm_item({**x, "type": "chapter"}) for x in raw if x]
        creator = ", ".join(a.get("name", "") for a in e.get("authors") or [])
    else:  # artist — metadata only
        e = api_get(token, f"/artists/{eid}")
        creator = e.get("name")
    if e is None:
        die(f"{typ} {eid}: not found (or not available in market)", 3)
    for n, it in enumerate(items, 1):
        it["n"] = n
    return {
        "type": typ,
        "id": eid,
        "url": canonical_url(typ, eid),
        "name": e.get("name"),
        "creator": creator,
        "description": e.get("description") or e.get("html_description") or "",
        "images": [i.get("url") for i in (e.get("images") or [])][:1],
        "release_date": e.get("release_date"),
        "total": len(items) or e.get("total_episodes") or e.get("total_chapters"),
        "items": items,
        "keyless": False,
    }


def _norm_item(t):
    typ = t.get("type") or ("episode" if t.get("show") else "track")
    show = (t.get("show") or {}).get("name")
    artist = ", ".join(a["name"] for a in t.get("artists") or []) or None
    return {
        "type": typ,
        "id": t.get("id"),
        "url": canonical_url(typ, t["id"]) if t.get("id") else None,
        "name": t.get("name"),
        "show": show,
        "artist": artist,
        "duration_ms": t.get("duration_ms"),
        "release_date": t.get("release_date"),
        "explicit": t.get("explicit"),
        "preview_url": t.get("preview_url") or (t.get("audio_preview_url")),
    }


def fetch_entity_keyless(typ, eid):
    """Embed-endpoint fallback: name + possibly TRUNCATED item list."""
    r = requests.get(f"https://open.spotify.com/embed/{typ}/{eid}", headers={**UA, "Accept-Language": "en"}, timeout=30)
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
                "preview_url": (t.get("audioPreview") or {}).get("url"),
            }
        )
    return {
        "type": typ,
        "id": eid,
        "url": canonical_url(typ, eid),
        "name": ent.get("name"),
        "creator": ent.get("subtitle"),
        "description": "",
        "images": [ent.get("coverArt", {}).get("sources", [{}])[0].get("url")],
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
        return []
    return [
        {"show": x.get("collectionName"), "feed": x.get("feedUrl"), "artist": x.get("artistName")}
        for x in r.json().get("results", [])
        if x.get("feedUrl")
    ]


def _norm_title(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _parse_rss(feed_url):
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
                "enclosure": enc.get("url") if enc is not None else None,
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


def cmd_capture(a):
    root = wiki_root()
    cap = Path(a.capture_dir)
    cap.mkdir(parents=True, exist_ok=True)
    typ, eid = parse_entity(a.url)
    token = None if a.keyless else get_token(root)
    ent = fetch_entity(token, typ, eid, a.market) if token else fetch_entity_keyless(typ, eid)

    if a.min_date:
        ent["items"] = [i for i in ent["items"] if not i.get("release_date") or i["release_date"] >= a.min_date]

    assets, drm_refs, feeds = [], [], {}
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
        if a.no_audio:
            break
        if it["type"] == "episode" and it.get("show"):
            fkey = it["show"]
            if fkey not in feeds:
                cands = itunes_feed_candidates(fkey)
                feeds[fkey] = {"candidates": cands, "items": None, "feed": None}
                for c in cands:
                    try:
                        feeds[fkey]["items"] = _parse_rss(c["feed"])
                        feeds[fkey]["feed"] = c["feed"]
                        break
                    except Exception:
                        continue
            fi, score = (None, 0)
            if feeds[fkey]["items"]:
                fi, score = match_episode(feeds[fkey]["items"], it["name"], it["duration_ms"])
            if fi:
                audio_n += 1
                assets.append(
                    {
                        "id": f"asset-audio-{audio_n:03d}",
                        "type": "audio",
                        "src_url": fi["enclosure"],
                        "status": "pending",
                        "player_url": it["url"],
                        "note": f"open RSS enclosure ({feeds[fkey]['feed']}); matched '{fi['title']}' score={score}",
                    }
                )
                it["audio"] = {"route": "rss", "asset_id": f"asset-audio-{audio_n:03d}"}
            else:
                drm_refs.append(_drm_ref(it, "episode not matched in any public feed"))
                it["audio"] = {"route": "none"}
        elif it["type"] in ("track", "chapter"):
            drm_refs.append(_drm_ref(it, "Spotify catalog audio is DRM-protected"))
            it["audio"] = {"route": "drm"}
    meta = {
        **ent,
        "market": a.market,
        "feeds": {k: {"feed": v["feed"], "candidates": v["candidates"]} for k, v in feeds.items()},
        "drm_refs": drm_refs,
        "counts": {"items": len(ent["items"]), "audio_resolved": audio_n, "drm_or_unmatched": len(drm_refs)},
    }
    (cap / "meta.json").write_text(json.dumps(meta, indent=1))
    (cap / "items.json").write_text(json.dumps(ent["items"], indent=1))
    (cap / "assets.json").write_text(json.dumps(assets, indent=1))
    (cap / "page.md").write_text(render_page_md(meta))
    summary = {
        "url": ent["url"],
        "name": ent["name"],
        "type": typ,
        "items": len(ent["items"]),
        "audio_resolved": audio_n,
        "drm_or_unmatched": len(drm_refs),
        "keyless": ent["keyless"],
        # The venue's native creation timestamp, under the protocol's
        # name for it, so the worker copies it into capture.json rather
        # than translating `release_date` itself. Emitted only at day
        # precision: Spotify's `release_date` follows its
        # `release_date_precision`, so an album can legitimately return
        # a bare "1979", which is not a publication DAY.
        "published": _published_day(ent.get("release_date")),
        "capture_dir": str(cap),
    }
    print(json.dumps(summary, indent=1))
    if ent["items"] and audio_n == 0 and not a.no_audio:
        sys.exit(4)


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
    L = [f"## {meta.get('name') or 'Spotify: ' + meta['id']}", ""]
    if meta.get("creator"):
        L.append(f"By **{meta['creator']}** — Spotify {meta['type']}: {meta['url']}")
    if meta.get("keyless"):
        L.append("")
        L.append(
            "> [!warning] Keyless capture — item list may be truncated; "
            "configure API credentials and re-capture for the full set."
        )
    if meta.get("description"):
        L += ["", meta["description"].strip()]
    if meta.get("items"):
        L += ["", "| # | Item | Duration | Released | Audio | Spotify |", "|---|---|---|---|---|---|"]
        for it in meta["items"]:
            route = (it.get("audio") or {}).get("route", "-")
            audio = {"rss": "open RSS feed", "drm": "DRM — listen at source", "none": "no open feed match", "-": "-"}[
                route
            ]
            name = (it.get("name") or "?").replace("|", "\\|")
            L.append(
                f"| {it['n']} | {name} | {ms_to_hms(it.get('duration_ms'))} "
                f"| {it.get('release_date') or '?'} | {audio} | {it['url']} |"
            )
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------- CLI


def cmd_auth(a):
    root = wiki_root()
    data = load_auth(root)
    if a.client_id:
        data["client_id"] = a.client_id
    if a.client_secret:
        data["client_secret"] = a.client_secret
    # A payload hand-carried from the old store may still have these — never
    # persist a bearer token to the store; it now lives only in-process.
    data.pop("token", None)
    data.pop("expires_at", None)
    shim = _shim(root)
    if shim is None:
        die("no wiki found above the current directory — run from the wiki root")
    try:
        proc = subprocess.run(
            [str(shim), "credential", "set", "spotify"], input=json.dumps(data, indent=1).encode(), capture_output=True
        )
    except OSError as e:
        die(f"credential store unreachable ({e.__class__.__name__}: {e})")
    if proc.returncode != 0:
        die(f"credential store failed ({proc.returncode}): {proc.stderr.decode()[:200]}")
    print(json.dumps({"stored": "spotify", "token_ok": bool(get_token(root))}))


def cmd_search(a):
    token = get_token(wiki_root())
    if not token:
        die("search needs API credentials — run: spotify.py auth --client-id ... --client-secret ...", 3)
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
    c.add_argument("--client-id")
    c.add_argument("--client-secret")
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

    c = sub.add_parser("capture", help="capture an entity into --capture-dir")
    c.add_argument("url")
    c.add_argument("--capture-dir", required=True)
    c.add_argument("--market", default="US")
    c.add_argument("--min-date", help="drop items released before YYYY-MM-DD")
    c.add_argument("--keyless", action="store_true", help="force embed fallback")
    c.add_argument("--no-audio", action="store_true", help="metadata + images only")
    c.set_defaults(fn=cmd_capture)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
