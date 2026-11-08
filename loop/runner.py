"""
`loop run`: the one always-on process.

  thread 1  Mac tracker         samples every 5s, rolls up into consumption items
  thread 2  scheduler           pulls each connected feed on its own cadence (Garmin: setting
                                garmin_every_min, default 60, min 15), records health
  main      web UI / API        http://127.0.0.1:8765, auto-refreshes

`loop install` registers this with launchd so it starts at login and restarts if it dies.
"""
import json
import threading
import time
import traceback
from datetime import datetime

from . import db, music
from .sources import curius, garmin, gcal, lastfm, mac, spotify, zotero

# name, every (seconds), connected?(con), job(con)
JOBS = [
    # Watch -> Garmin Connect app on the phone -> Garmin cloud. Pulling 3 days back each time
    # catches late syncs (e.g. you open the app at noon and last night's sleep arrives).
    ("garmin", lambda con: garmin_every_s(con), garmin.is_connected, lambda con: garmin.sync(con, days=3)),
    # steps & activity change all day: a light pull of just today, every 15 min (the watch still has to
    # sync to Garmin Connect on your phone for new numbers to exist)
    ("garmin_steps", 15 * 60, garmin.is_connected, lambda con: garmin.sync_today(con)),
    ("spotify", 15 * 60, spotify.is_connected, spotify.sync),   # API only keeps the last 50 plays
    ("lastfm", 20 * 60, lastfm.is_connected, lambda con: lastfm.sync(con, days=2)),
    ("music_tags", 30 * 60, lambda con: spotify.is_connected(con) or lastfm.is_connected(con)
     or bool(con.execute("SELECT 1 FROM music_plays LIMIT 1").fetchone()),
     lambda con: music.enrich(con)),  # genres + moods
    ("gcal", 15 * 60, gcal.is_connected, gcal.sync),
    ("zotero", 60 * 60, zotero.is_connected, zotero.sync),   # incremental: only what changed since last time
    ("curius", lambda con: curius.every_s(con), curius.is_connected, curius.sync),
    ("site", 24 * 60 * 60, lambda con: __import__("loop.publish", fromlist=["x"]).is_on(con),   # brain snapshot → your website, monthly
     lambda con: __import__("loop.publish", fromlist=["x"]).publish(con, {r[0]: r[1] for r in con.execute("SELECT key, value FROM settings")}, force=False)),   # full list every 6 h; highlights a few pages per run
    ("mac_music", 10 * 60, lambda con: True, lambda con: music.absorb_mac(con)),
    ("focus_nudge", 2 * 60, lambda con: True, lambda con: __import__("loop.nudge", fromlist=["x"]).check(con)),
    ("profile_doc", 60 * 60, lambda con: True, lambda con: {"file": _write_profile(con)}),
    ("evals", 6 * 60 * 60, lambda con: True,   # one "before loop → now" snapshot a week, kept on this Mac
     lambda con: {"saved": __import__("loop.evals", fromlist=["x"]).snapshot(con, __import__("datetime").date.today(), __import__("loop.db", fromlist=["x"]).settings(con))}),
]


GARMIN_MIN_EVERY_MIN = 15  # floor: data only changes when the watch syncs via the phone


def garmin_every_s(con) -> int:
    r = con.execute("SELECT value FROM settings WHERE key='garmin_every_min'").fetchone()
    try:
        minutes = int(float(r[0])) if r else 60
    except ValueError:
        minutes = 60
    return max(GARMIN_MIN_EVERY_MIN, minutes) * 60


def _write_profile(con) -> str:
    from datetime import date as _date
    from . import profile_doc
    s = {r[0]: r[1] for r in con.execute("SELECT key, value FROM settings")}
    return profile_doc.write_file(con, profile_doc.build(con, s, _date.today()))


def record(con, source: str, ok: bool | None, message: str):
    con.execute(
        "INSERT OR REPLACE INTO sync_status(source, at, ok, message) VALUES(?,?,?,?)",
        (source, datetime.now().isoformat(timespec="seconds"),
         None if ok is None else int(ok), message[:500]),
    )
    con.commit()


JOB_SOURCE = {"garmin_steps": "garmin", "music_tags": "music", "mac_music": "mac"}


def is_off(con, source: str) -> bool:
    """You turned this connection off (You → Connectors). Its data stays; it just stops syncing."""
    r = con.execute("SELECT value FROM settings WHERE key=?", (f"off_{source}",)).fetchone()
    return bool(r and r[0] == "on")


def run_job(con, name, connected, job):
    if is_off(con, JOB_SOURCE.get(name, name)):
        record(con, name, None, "turned off")
        return
    if not connected(con):
        record(con, name, None, "not connected")
        return
    try:
        record(con, name, True, json.dumps(job(con)))
    except Exception as e:
        record(con, name, False, f"{type(e).__name__}: {e}")


def scheduler(path: str, stop: threading.Event, jobs=JOBS, poll: float = 60):
    con = db.connect(path)
    last: dict[str, float] = {}
    while not stop.is_set():
        for name, every, connected, job in jobs:
            every = every(con) if callable(every) else every
            if time.time() - last.get(name, 0) >= every:
                last[name] = time.time()
                run_job(con, name, connected, job)
        stop.wait(poll)


def run(path: str, host: str = "127.0.0.1", port: int = 8765, track: bool = True):
    from .server import serve
    from . import keychain
    try:
        moved = keychain.migrate(db.connect(path))   # older installs kept the Zotero key in the database
        if moved:
            print(f"moved to your Keychain: {', '.join(moved)}", flush=True)
    except Exception as e:  # never block startup on this
        print(f"keychain migration skipped: {e}", flush=True)
    stop = threading.Event()
    threads = [threading.Thread(target=scheduler, args=(path, stop), daemon=True, name="scheduler")]
    if track:
        def tracker():
            while not stop.is_set():  # restart the tracker if it ever crashes
                try:
                    mac.Tracker(db.connect(path)).run()
                except Exception:
                    traceback.print_exc()
                    time.sleep(10)
        threads.append(threading.Thread(target=tracker, daemon=True, name="tracker"))
    for t in threads:
        t.start()
    try:
        serve(db.connect(path), host, port)
    finally:
        stop.set()


def sources_status(con) -> list[dict]:
    """Everything feeding loop, and whether it's healthy, for the Sources panel."""
    status = {r["source"]: dict(r) for r in con.execute("SELECT * FROM sync_status")}
    last = mac.last_seen(con)
    pr = con.execute("SELECT value FROM settings WHERE key='track_paused_until'").fetchone()
    paused = pr[0] if pr and pr[0] and pr[0] > datetime.now().isoformat() else None
    out = [{
        "source": "mac", "label": "Mac activity (built-in tracker)",
        "connected": True, "ok": bool(last) and not paused, "at": last, "paused_until": paused,
        "message": ("off (until you turn it back on)" if paused and paused.startswith("9999") else
                    f"paused until {paused[11:16]}{'' if paused[:10] == datetime.now().date().isoformat() else ' tomorrow'}"
                    if paused else
                    "recording: apps, sites, titles. No screenshots, no keystrokes, never leaves this Mac"
                    if last else "not running yet: `python3 -m loop run`"),
        "how": "Built in. Allow the one-time macOS prompt per browser.",
    }]
    labels = {
        "garmin": ("Garmin (sleep, resting HR, steps)", "python3 -m loop connect garmin"),
        "spotify": ("Spotify (direct)", "python3 -m loop connect spotify"),
        "lastfm": ("Last.fm (optional: gap-free music history)", "python3 -m loop connect lastfm"),
        "gcal": ("Google Calendar", "You → Connectors"),
        "zotero": ("Zotero (research library)", "You → Connectors"),
        "curius": ("Curius (saved links and highlights)", "Build → Brain graph → Curius"),
    }
    for name, _, connected, _ in JOBS:
        if name not in labels:
            continue
        s = status.get(name, {})
        out.append({
            "source": name, "label": labels[name][0], "connected": connected(con),
            "ok": None if s.get("ok") is None else bool(s["ok"]), "at": s.get("at"),
            "message": s.get("message") or ("waiting for first sync" if connected(con) else "not connected"),
            "how": labels[name][1],
        })
    out.append({
        "source": "phone", "label": "Pixel screen time (TikTok, X, …)", "connected": False,
        "ok": None, "at": None, "message": "not built yet", "how": "Android companion app (next step)",
    })
    return out
