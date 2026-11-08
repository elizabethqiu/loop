"""
Music via Last.fm scrobbles. Connect Spotify / YouTube Music / Apple Music to Last.fm
once (in their settings), and it catches listening on every device, including the Pixel.

Connect once: `python3 -m loop connect lastfm` (username + a free API key from
https://www.last.fm/api/account/create). Stored in loop's local settings table.
Each scrobble becomes an item: source = artist, title = track. Duration isn't in the
recent-tracks feed, so each counts as ~3.5 minutes.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from ..db import upsert_item
from .spotify import is_duplicate

API = "https://ws.audioscrobbler.com/2.0/"
TRACK_MIN = 3.5


class LastfmError(RuntimeError):
    pass


def parse_tracks(payload: dict) -> list[dict]:
    tracks = (payload.get("recenttracks") or {}).get("track") or []
    if isinstance(tracks, dict):
        tracks = [tracks]
    out = []
    for t in tracks:
        if (t.get("@attr") or {}).get("nowplaying"):
            continue
        uts = (t.get("date") or {}).get("uts")
        if not uts:
            continue
        artist = (t.get("artist") or {}).get("#text") or (t.get("artist") or {}).get("name") or "unknown"
        when = datetime.fromtimestamp(int(uts))
        out.append({
            "origin": "lastfm", "source": artist, "title": t.get("name"),
            "mode": "listening", "depth": "skim",
            "started_at": when.isoformat(timespec="minutes"), "minutes": TRACK_MIN,
            "ext_key": f"lastfm:{uts}:{artist}:{t.get('name')}",
        })
    return out


def credentials(con) -> tuple[str | None, str | None]:
    s = {r[0]: r[1] for r in con.execute(
        "SELECT key, value FROM settings WHERE key IN ('lastfm_user','lastfm_api_key')")}
    return (os.environ.get("LASTFM_USER") or s.get("lastfm_user"),
            os.environ.get("LASTFM_API_KEY") or __import__("loop.keychain", fromlist=["x"]).resolve("lastfm_api_key", s.get("lastfm_api_key")))


def is_connected(con) -> bool:
    return all(credentials(con))


def connect_interactive(con) -> str:
    print("A Last.fm API key adds moods/genres to your Spotify listening.")
    print("Username is optional: leave it blank unless you also want Last.fm's play history.")
    key = input("Last.fm API key: ").strip()
    user = input("Last.fm username (optional): ").strip()
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('lastfm_user',?)", (user,))
    __import__("loop.keychain", fromlist=["x"]).save_secret(con, "lastfm_api_key", key)   # Keychain, not the database
    con.commit()
    return (f"Last.fm connected as {user}." if user else "Last.fm key saved: moods and genres will be added to your Spotify plays.")


def _call(params: dict) -> dict:
    q = urllib.parse.urlencode({**params, "format": "json"})
    from .. import egress
    egress.record("lastfm", API, f"{params.get('method', 'request')} for your username (with your API key)")
    with urllib.request.urlopen(f"{API}?{q}", timeout=30) as r:
        payload = json.load(r)
    if "error" in payload:
        raise LastfmError(payload.get("message", "last.fm error"))
    return payload


def verify(user: str, key: str) -> dict:
    """Check the username and key work before saving them."""
    info = _call({"method": "user.getinfo", "user": user, "api_key": key}).get("user", {})
    return {"name": info.get("name"), "playcount": info.get("playcount")}


def track_minutes(con, key: str, artist: str, track: str) -> float | None:
    """Real song length (Last.fm's recent-tracks feed doesn't include it), cached per track."""
    k = f"{artist}\t{track}"
    r = con.execute("SELECT minutes FROM track_lengths WHERE key=?", (k,)).fetchone()
    if r:
        return r[0]
    mins = None
    try:
        info = _call({"method": "track.getInfo", "artist": artist, "track": track, "api_key": key, "autocorrect": 1})
        ms = int((info.get("track") or {}).get("duration") or 0)
        mins = round(ms / 60000, 2) if ms else None
    except (LastfmError, OSError, ValueError):
        return None
    con.execute("INSERT OR REPLACE INTO track_lengths(key, minutes) VALUES(?,?)", (k, mins))
    return mins


def sync(con, days: int = 7) -> dict:
    """Pull scrobbles into the Listening card (music_plays) and the consumption timeline (items).
    Last.fm timestamps are when a song started; listening time = the song's length, capped by when the
    next song started (so skips count only for as long as they played)."""
    user, key = credentials(con)
    if not (user and key):
        raise LastfmError("Last.fm not connected. Connect it in You → Connectors.")
    since = int((datetime.now() - timedelta(days=days)).timestamp())
    page, scrobbles = 1, []
    while True:
        payload = _call({"method": "user.getrecenttracks", "user": user, "api_key": key,
                         "limit": 200, "page": page, "from": since})
        scrobbles += parse_tracks(payload)
        attr = payload["recenttracks"].get("@attr", {})
        if page >= int(attr.get("totalPages", 1)):
            break
        page += 1
    scrobbles.sort(key=lambda x: x["started_at"])
    added = 0
    for i, item in enumerate(scrobbles):
        length = track_minutes(con, key, item["source"], item["title"] or "") or TRACK_MIN
        nxt = scrobbles[i + 1]["started_at"] if i + 1 < len(scrobbles) else None
        listened = length
        if nxt:
            gap = (datetime.fromisoformat(nxt) - datetime.fromisoformat(item["started_at"])).total_seconds() / 60
            if 0 < gap < length:
                listened = gap
        item["minutes"] = round(listened, 2)
        played_at = item["ext_key"].split(":")[1]
        con.execute("""INSERT OR IGNORE INTO music_plays(played_at, start, track, artist, duration_min, listened_min)
                       VALUES(?,?,?,?,?,?)""", (f"lastfm:{played_at}", item["started_at"], item["title"], item["source"],
                                                  length, item["minutes"]))
        if is_duplicate(con, item):  # already have it from Spotify
            continue
        upsert_item(con, item)
        added += 1
    con.commit()
    return {"scrobbles": added}
