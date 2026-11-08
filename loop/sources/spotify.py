"""
Spotify, connected directly (no Last.fm needed).

Setup (once):
  1. https://developer.spotify.com/dashboard -> Create app. Any name/description.
     Redirect URI:  http://127.0.0.1:8766/callback     APIs: Web API
  2. python3 -m loop connect spotify   (paste the app's Client ID; a browser opens to approve)

Uses OAuth with PKCE, so no client secret is stored. Only a refresh token, kept in loop's
local database. Scopes: read recently played + currently playing. Nothing else.

Spotify's API only returns your last 50 plays, so loop pulls every 15 minutes while the Mac is
awake. Plays from a long stretch with the Mac asleep (e.g. a whole day of phone listening)
can fall off the end. Connect Last.fm too if you want a gap-free history; duplicates are skipped.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..db import upsert_item

AUTH = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
PORT = 8766
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCOPES = "user-read-recently-played user-read-currently-playing"


class SpotifyError(RuntimeError):
    pass


def _setting(con, key):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] else None


def _save(con, **kv):
    for k, v in kv.items():
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (k, v))
    con.commit()


def is_connected(con) -> bool:
    return bool(_setting(con, "spotify_refresh_token") and _setting(con, "spotify_client_id"))


def _post_form(url, data: dict) -> dict:
    from .. import egress
    egress.record("spotify", url, "sign-in token request")
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode(),
                                 {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SpotifyError(f"Spotify said {e.code}: {e.read().decode()[:200]}") from e


# ------------------------------------------------------------------ one-time connect

def connect_interactive(con) -> str:
    client_id = input("Spotify app Client ID: ").strip()
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state,
    })
    result: dict = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [None])[0] == state:
                result["code"], result["error"] = q.get("code", [None])[0], q.get("error", [None])[0]
            ok = bool(result.get("code"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>{'loop is connected to Spotify. You can close this tab.' if ok else 'Spotify connection failed: ' + str(result.get('error'))}</h2>".encode())

    srv = HTTPServer(("127.0.0.1", PORT), Callback)
    srv.timeout = 5
    print(f"Opening Spotify to approve loop… if no browser opens, visit:\n{url}\n")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    deadline = time.monotonic() + 300
    while not result and time.monotonic() < deadline:
        srv.handle_request()  # returns after each request (e.g. favicon) or 5 s idle
    srv.server_close()
    if not result:
        raise SpotifyError("timed out waiting for Spotify approval (5 min)")
    if not result.get("code"):
        raise SpotifyError(f"Spotify approval failed: {result.get('error')}")
    tok = _post_form(TOKEN, {"grant_type": "authorization_code", "code": result["code"],
                             "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier})
    _save(con, spotify_client_id=client_id, spotify_refresh_token=tok["refresh_token"])
    return "Spotify connected. loop pulls your plays every 15 minutes. Test now:  python3 -m loop sync spotify"


def access_token(con) -> str:
    if not is_connected(con):
        raise SpotifyError("Spotify not connected. Run:  python3 -m loop connect spotify")
    tok = _post_form(TOKEN, {"grant_type": "refresh_token", "client_id": _setting(con, "spotify_client_id"),
                             "refresh_token": _setting(con, "spotify_refresh_token")})
    if tok.get("refresh_token"):  # Spotify may rotate it
        _save(con, spotify_refresh_token=tok["refresh_token"])
    return tok["access_token"]


# ------------------------------------------------------------------ sync

def _local(iso_utc: str) -> datetime:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def parse_recent(payload: dict, prev_played_at: str | None = None) -> list[dict]:
    """
    Plays, oldest first. `played_at` is logged around when a track ends, so each play's real
    listening time is capped by the gap since the previous play: skipped songs count only for
    as long as they played.
    """
    plays = []
    for it in payload.get("items", []):
        t = it.get("track") or {}
        if not t or not it.get("played_at"):
            continue
        artist = (t.get("artists") or [{}])[0]
        plays.append({
            "played_at": it["played_at"], "end": _local(it["played_at"]),
            "track_id": t.get("id"), "track": t.get("name"),
            "artist_id": artist.get("id"), "artist": artist.get("name") or "unknown",
            "album": (t.get("album") or {}).get("name"),
            "duration_min": (t.get("duration_ms") or 210000) / 60000,
        })
    plays.sort(key=lambda p: p["played_at"])
    prev = _local(prev_played_at) if prev_played_at else None
    for p in plays:
        listened = p["duration_min"]
        if prev is not None:
            gap = (p["end"] - prev).total_seconds() / 60
            if 0 < gap < listened:
                listened = gap
        p["listened_min"] = round(listened, 2)
        p["start"] = (p["end"] - timedelta(minutes=listened)).isoformat(timespec="minutes")
        prev = p["end"]
    return plays


def as_item(p: dict) -> dict:
    return {"origin": "spotify", "source": p["artist"], "title": p["track"], "mode": "listening", "depth": "skim",
            "started_at": p["start"], "minutes": p["listened_min"], "ext_key": f"spotify:{p['played_at']}"}


def is_duplicate(con, item: dict, window_min: int = 6) -> bool:
    """Same artist + track within a few minutes from another music source (e.g. Last.fm)."""
    t = datetime.fromisoformat(item["started_at"])
    return con.execute(
        """SELECT 1 FROM items WHERE mode='listening' AND origin != ? AND source = ? COLLATE NOCASE
           AND title = ? COLLATE NOCASE AND started_at BETWEEN ? AND ? LIMIT 1""",
        (item["origin"], item["source"], item["title"] or "",
         (t - timedelta(minutes=window_min)).isoformat(timespec="minutes"),
         (t + timedelta(minutes=window_min)).isoformat(timespec="minutes")),
    ).fetchone() is not None


def sync(con) -> dict:
    token = access_token(con)
    after = _setting(con, "spotify_after_ms")
    q = {"limit": 50, **({"after": after} if after else {})}
    req = urllib.request.Request(f"{API}/me/player/recently-played?{urllib.parse.urlencode(q)}",
                                 headers={"Authorization": f"Bearer {token}"})
    from .. import egress
    egress.record("spotify", API, "recently played (with your token)")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise SpotifyError(f"Spotify said {e.code}: {e.read().decode()[:200]}") from e
    last = con.execute("SELECT MAX(played_at) FROM music_plays").fetchone()[0]
    added = skipped = 0
    for p in parse_recent(payload, last):
        con.execute(
            """INSERT OR IGNORE INTO music_plays(played_at, start, track_id, track, artist_id, artist, album,
                                                 duration_min, listened_min) VALUES(?,?,?,?,?,?,?,?,?)""",
            (p["played_at"], p["start"], p["track_id"], p["track"], p["artist_id"], p["artist"], p["album"],
             round(p["duration_min"], 2), p["listened_min"]))
        item = as_item(p)
        if is_duplicate(con, item):
            skipped += 1
            continue
        upsert_item(con, item)
        added += 1
    cur = (payload.get("cursors") or {}).get("after")
    if cur:
        _save(con, spotify_after_ms=str(cur))
    con.commit()
    return {"plays": added, "duplicates_skipped": skipped}


def artist_genres(con, artist_ids: list[str]) -> dict[str, list[str]]:
    """Spotify's own genre labels per artist (may be empty for some artists or app tiers)."""
    if not artist_ids:
        return {}
    token = access_token(con)
    out = {}
    for i in range(0, len(artist_ids), 50):
        ids = ",".join(artist_ids[i:i + 50])
        req = urllib.request.Request(f"{API}/artists?ids={ids}", headers={"Authorization": f"Bearer {token}"})
        from .. import egress
        egress.record("spotify", API, f"genres for {len(artist_ids[i:i + 50])} artists you played")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                for a in json.load(r).get("artists", []):
                    if a:
                        out[a["id"]] = a.get("genres") or []
        except urllib.error.HTTPError:
            break
    return out
