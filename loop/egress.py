"""
Where your data goes: every place loop sends anything off this Mac, and a log of each request.

Two parts:
  FLOWS    the full list, in plain words: where, what is sent, what comes back, when, how to turn it off.
           You → Where your data goes shows it. A test fails if any code that makes a network request
           isn't listed here and doesn't call record().
  record() writes one line per outbound request to ~/.loop/egress.log (only you can read it): when, which flow,
           which host, and a short description of what was sent. Never the secret values themselves.
"""
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

# key: the name used by record(); "on": which setting/connection makes it happen
FLOWS = [
    {"key": "ai", "name": "AI you connect (Claude, OpenAI-compatible, or a local model)", "to": "the provider you chose in ✦ Ask",
     "sends": "Your question, the last few messages, and a summary of your stats: sleep, steps, food totals, goals, scores, "
              "projects, calendar load, and recent 1–5 day ratings. Never: notes, journal, mood notes, private health, "
              "weight, or what you browsed.",
     "gets": "The answer.", "when": "Only when you ask, open the AI summary once a day, or ask it to rearrange your homepage "
     "(that sends only the card names and your sentence).",
     "off": "✦ Ask → setup → remove the key (a local model sends nothing off this Mac)."},
    {"key": "garmin", "name": "Garmin Connect", "to": "connect.garmin.com",
     "sends": "Your saved Garmin sign-in session (your password is never stored).",
     "gets": "Sleep, heart rate, HRV, steps, workouts.", "when": "Every 15–60 minutes while connected.",
     "off": "You → Connectors → Garmin switch."},
    {"key": "lastfm", "name": "Last.fm", "to": "ws.audioscrobbler.com",
     "sends": "Your Last.fm username and API key.", "gets": "What you played.", "when": "Every 20 minutes while connected.",
     "off": "You → Connectors → Last.fm switch."},
    {"key": "spotify", "name": "Spotify", "to": "api.spotify.com",
     "sends": "Your Spotify access token.", "gets": "Recently played tracks, artist genres.", "when": "Every 15 minutes while connected.",
     "off": "You → Connectors → Spotify switch."},
    {"key": "music_tags", "name": "Music tags (Last.fm / MusicBrainz)", "to": "ws.audioscrobbler.com, musicbrainz.org",
     "sends": "Artist and track names you played.", "gets": "Genres and moods.", "when": "Every 30 minutes, for new tracks.",
     "off": "Turn off Last.fm and Spotify."},
    {"key": "gcal", "name": "Google Calendar", "to": "calendar.google.com",
     "sends": "Nothing but a request for your private calendar link.", "gets": "Your events.", "when": "Every 15 minutes while connected.",
     "off": "You → Connectors → Google Calendar switch."},
    {"key": "zotero", "name": "Zotero", "to": "api.zotero.org",
     "sends": "Your Zotero key (from the Keychain), read-only.", "gets": "Items in your library.", "when": "Hourly while connected.",
     "off": "You → Connectors → Zotero switch."},
    {"key": "site", "name": "Your website (brain snapshot)", "to": "your site's GitHub repo (git push)",
     "sends": "src/data/brain.json: topic names, sizes, how many are new, a few related ideas, and up to 3 recent Curius "
              "link titles per topic. Never people, notes, AI chats, to-dos, paper titles or private data.",
     "gets": "Nothing.", "when": "Once a month, and only if the snapshot changed.",
     "off": "Set publish_brain to off (python3 -m loop publish-brain --off)."},
    {"key": "curius", "name": "Curius", "to": "curius.app",
     "sends": "Your Curius profile name only. Public, read-only requests with no login.",
     "gets": "Your saved links, highlights and public topics; and, for people you follow, their 30 latest public saves, "
             "reduced on arrival to an anonymous id, which of your links they also saved, and a few interest words "
             "(no names or profiles kept).",
     "when": "Every 10 minutes at most while connected (the full list every 6 hours; people and topics weekly).",
     "off": "Build → Brain graph → Curius → disconnect."},
    {"key": "food", "name": "Food search (USDA, Open Food Facts)", "to": "api.nal.usda.gov, world.openfoodfacts.org",
     "sends": "The food name you search for, or a barcode.", "gets": "Nutrition facts.",
     "when": "Only when a search isn't found in the offline database.", "off": "Build the offline database: python3 -m loop usda-build."},
    {"key": "hub", "name": "Together hub", "to": "the hub address you joined",
     "sends": "Your display name, friend and group codes you enter, and the weekly summary you choose to share (goal names, "
              "days hit, streaks). Your picture only if you press Share my picture.",
     "gets": "Friends, groups, their shares.", "when": "When you open Together or share.", "off": "Together → Disconnect from this hub."},
    {"key": "feedback", "name": "Feedback sheet", "to": "the Google Sheet web app you set",
     "sends": "The feedback message, its type, the page you were on, and the email if you typed one.", "gets": "A confirmation.",
     "when": "Only when you press Send.", "off": "You → Feedback inbox → clear the address."},
    {"key": "about", "name": "About you (only if you share)", "to": "the loop team's survey sheet (script.google.com)",
     "sends": "The About you answers you filled in (background, age range, sex, gender identity, country and state, race or "
              "ethnicity, how you heard about loop, what you hope for, goals) and a random id. Never your name, username, "
              "email, or anything loop tracks.",
     "gets": "A confirmation.",
     "when": "Only when you press Share with the loop team, and once more if you stop sharing or delete your answers "
             "(to delete your row).",
     "off": "You → Password & sign-out → About you → Stop sharing."},
]

STORED = [
    ("~/.loop/loop.db", "Everything loop tracks and you log. On this Mac only, readable only by you."),
    ("~/.loop/garmin_tokens/", "Your Garmin sign-in session (not your password)."),
    ("macOS Keychain", "Your keys and private links (AI key, Zotero, Last.fm, calendar link), locked by your Mac password."),
    ("~/.loop/avatar.img", "Your profile picture."),
    ("~/.loop/profile.md", "The About me doc, rebuilt hourly from your data."),
    ("~/.loop/egress.log", "This log of what was sent where."),
]

_lock = threading.Lock()
_HIDE = re.compile(r"(?i)([?&](?:api_key|key|token|access_token|sk)=)[^&]+")


def log_path() -> Path:
    if os.environ.get("LOOP_EGRESS_LOG"):
        return Path(os.environ["LOOP_EGRESS_LOG"])
    base = Path(os.environ.get("LOOP_DB") or Path.home() / ".loop" / "loop.db").parent
    return base / "egress.log"


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/?#]+)", url or "")
    return m.group(1) if m else (url or "")


def record(key: str, url: str, what: str) -> None:
    """One line per outbound request. Never raises; never stores secret values (query keys are stripped)."""
    try:
        line = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "flow": key, "host": host_of(_HIDE.sub(r"\1…", url)),
                           "what": what[:300]})
        p = log_path()
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            new = not p.exists()
            with open(p, "a") as f:
                f.write(line + "\n")
            if new:
                os.chmod(p, 0o600)
            if p.stat().st_size > 2_000_000:   # keep it small: last ~half
                lines = p.read_text().splitlines()[-5000:]
                p.write_text("\n".join(lines) + "\n")
    except Exception:
        pass


def recent(n: int = 100) -> list[dict]:
    p = log_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[::-1]


def overview(con, settings: dict) -> dict:
    from . import connectors
    on = {x.get("key"): x for x in connectors.connectors(con, settings)["connected"] if x.get("key")}
    last = {}
    for r in recent(2000):
        last.setdefault(r["flow"], r["at"])
    flows = [{**f, "active": (on.get(f["key"], {}).get("connected") and on.get(f["key"], {}).get("on", True)) if f["key"] in on else None,
              "last": last.get(f["key"])} for f in FLOWS]
    return {"flows": flows, "stored": [{"where": w, "what": t} for w, t in STORED], "recent": recent(60)}
