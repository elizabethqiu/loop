"""
loop's own Mac activity tracker. No third-party apps, no screenshots.

Every few seconds it notes:
  - the frontmost app (via `lsappinfo`, no permissions needed)
  - for browsers, the active tab's domain + title (AppleScript; macOS asks once per browser
    for Automation permission). Incognito/private windows are recorded only as
    "private browsing", never the URL.
  - what's playing in Spotify / Apple Music, even in the background (a separate lane, since
    you can listen while you work)
  - keyboard/mouse idle time (via `ioreg`). Past 2 min idle, time stops counting, unless
    the thing in front is something you watch or listen to.

Samples merge into spans in the `activity` table; spans roll up into hourly consumption
items, so the rest of loop (plate, tags, cut list) treats them like any other source.

Run:  python3 -m loop run       (tracker + scheduled syncs + UI, foreground)
      python3 -m loop install   (same, started at login via launchd)
"""
import os
import plistlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .. import names
from ..consume import classify
from ..db import upsert_item

INTERVAL = 5          # seconds between samples
IDLE_LIMIT = 120      # seconds of no input before "away" (unless watching/listening)
PASSIVE = {"watching", "listening"}
AWAY_APPS = {"com.apple.loginwindow", "com.apple.ScreenSaver.Engine"}

CHROMIUM = {
    "com.google.Chrome": "Google Chrome",
    "com.google.Chrome.beta": "Google Chrome Beta",
    "com.brave.Browser": "Brave Browser",
    "company.thebrowser.Browser": "Arc",
    "com.microsoft.edgemac": "Microsoft Edge",
    "com.vivaldi.Vivaldi": "Vivaldi",
}
SAFARI = {"com.apple.Safari": "Safari", "com.apple.SafariTechnologyPreview": "Safari Technology Preview"}
PLAYERS = {"com.spotify.client": "Spotify", "com.apple.Music": "Music"}

LABEL = "com.loop.app"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


@dataclass
class Sample:
    source: str            # domain for browser tabs, bundle id for apps
    title: str | None      # tab title, or app name
    app: str               # bundle id of the frontmost app


# ------------------------------------------------------------------ probes (macOS)

def _sh(args, timeout=3) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _osa(script: str) -> str:
    return _sh(["osascript", "-e", script])


def frontmost() -> tuple[str | None, str | None, int | None]:
    """(bundle id, display name, pid) of the app in front."""
    asn = _sh(["lsappinfo", "front"])
    if not asn:
        return None, None, None
    info = _sh(["lsappinfo", "info", "-only", "bundleid", "-only", "name", "-only", "pid", asn])
    bundle = re.search(r'"CFBundleIdentifier"="([^"]*)"', info)
    name = re.search(r'"LSDisplayName"="([^"]*)"', info)
    pid = re.search(r'"pid"\s*=\s*(\d+)', info)
    return (bundle.group(1) if bundle else None), (name.group(1) if name else None), (int(pid.group(1)) if pid else None)


def idle_seconds() -> float:
    out = _sh(["ioreg", "-c", "IOHIDSystem", "-d", "4"])
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


_JXA_CHROMIUM = """(() => { const a = Application(%s); if (a.windows.length === 0) return "";
  const w = a.windows[0]; let mode = ""; try { mode = w.mode(); } catch (e) {}
  return JSON.stringify([w.activeTab.url(), w.activeTab.title(), mode]); })()"""
_JXA_SAFARI = """(() => { const a = Application(%s); if (a.documents.length === 0) return "";
  const d = a.documents[0]; return JSON.stringify([d.url(), d.name(), ""]); })()"""


def _jxa(script: str) -> str:
    return _sh(["osascript", "-l", "JavaScript", "-e", script])


def browser_tab(bundle: str, pid: int | None = None) -> tuple[str | None, str | None, bool] | None:
    """(url, title, private) for the active tab of the browser in front, or None if not a scriptable browser.
    Addressed by process ID, so a second copy of the same browser (e.g. a hidden automation Chrome)
    can't answer instead of yours. Falls back to the app name if the pid isn't known."""
    import json as _json
    if pid and (bundle in CHROMIUM or bundle in SAFARI):
        out = _jxa((_JXA_CHROMIUM if bundle in CHROMIUM else _JXA_SAFARI) % pid)
        if out.startswith("["):
            try:
                url, title, mode = _json.loads(out)
                return url, title, mode == "incognito"
            except ValueError:
                pass
        elif out == "":
            return None, None, False
    sep = "␞"
    if bundle in CHROMIUM:
        app = CHROMIUM[bundle]
        mode = "" if bundle == "company.thebrowser.Browser" else f' & "{sep}" & (mode of front window)'
        out = _osa(f'tell application "{app}" to if (count of windows) > 0 then return '
                   f'(URL of active tab of front window) & "{sep}" & (title of active tab of front window){mode}')
    elif bundle in SAFARI:
        out = _osa(f'tell application "{SAFARI[bundle]}" to if (count of documents) > 0 then return '
                   f'(URL of front document) & "{sep}" & (name of front document)')
    else:
        return None
    if not out:
        return None, None, False
    parts = out.split(sep)
    url, title = parts[0], (parts[1] if len(parts) > 1 else None)
    private = len(parts) > 2 and parts[2].strip() == "incognito"
    return url, title, private


def now_playing() -> Sample | None:
    """Background music from Spotify / Apple Music, if one is running and playing."""
    for bundle, app in PLAYERS.items():
        if not _sh(["pgrep", "-x", app]):
            continue
        out = _osa(f'tell application "{app}" to if player state is playing then '
                   f'return (artist of current track) & " – " & (name of current track)')
        if out:
            return Sample(source=bundle, title=out, app=bundle)
    return None


def domain_of(url: str) -> str | None:
    try:
        u = urlparse(url)
        host = u.hostname if u.scheme in ("http", "https") else None
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


_JXA_TITLE = """(() => { const se = Application("System Events");
  const ps = se.processes.whose({unixId: %d}); if (ps.length === 0) return "";
  const p = ps[0]; let t = "";
  try { t = p.attributes.byName("AXFocusedWindow").value().name() || ""; } catch (e) {}
  if (!t) { try { t = p.windows[0].name() || ""; } catch (e) {} }
  if (!t) { try { t = p.windows[0].attributes.byName("AXTitle").value() || ""; } catch (e) {} }
  return t; })()"""


def window_title(pid: int | None = None) -> str | None:
    """Title of the focused window of the app in front (e.g. 'predict.py — loop' in VS Code).
    Asked of that exact process (by pid), so switching windows mid-sample can't mix apps up.
    Opt-in: needs Accessibility permission for python3. Never reads window contents."""
    if pid:
        out = _sh(["osascript", "-l", "JavaScript", "-e", _JXA_TITLE % pid])
        return out or None
    out = _osa('tell application "System Events" to tell (first process whose frontmost is true) '
               'to if (count of windows) > 0 then return name of front window')
    return out or None


WINDOW_TITLES = False  # set from the `track_window_titles` setting by the Tracker


def probe() -> tuple[Sample | None, Sample | None, float]:
    """(focus sample or None if away, background music sample, idle seconds)."""
    bundle, name, pid = frontmost()
    idle = idle_seconds()
    focus = None
    if bundle and bundle not in AWAY_APPS:
        tab = browser_tab(bundle, pid)
        if tab and tab[2]:
            focus = Sample("private browsing", None, bundle)
        elif tab and tab[0] and domain_of(tab[0]):
            focus = Sample(domain_of(tab[0]), tab[1] or None, bundle)
        else:
            title = window_title(pid) if WINDOW_TITLES else None
            if title and is_private_title(title):
                focus = Sample("private browsing", None, bundle)  # never keep incognito/private titles
            else:
                focus = Sample(bundle, f"{name}: {title}" if title and title != name else name, bundle)
    return focus, now_playing(), idle


# ------------------------------------------------------------------ privacy

_PRIVATE_TITLE = re.compile(r"\((Incognito|InPrivate|Private)\)|Private Browsing|— Private$| - Private$", re.I)


def is_private_title(title: str) -> bool:
    return bool(_PRIVATE_TITLE.search(title or ""))


def scrub_private(con) -> int:
    """Turn any incognito/private window titles already recorded into 'private browsing', and rebuild
    the hourly summaries that contained them. Safe to run repeatedly."""
    rows = [r for r in con.execute("SELECT id, start, end, title FROM activity WHERE title IS NOT NULL")
            if is_private_title(r[3])]
    hours = set()
    for rid, start, end, _ in rows:
        con.execute("UPDATE activity SET source='private browsing', title=NULL WHERE id=?", (rid,))
        t = datetime.fromisoformat(start).replace(minute=0, second=0)
        while t <= datetime.fromisoformat(end):
            hours.add(t)
            t += timedelta(hours=1)
    for h in hours:
        con.execute("DELETE FROM items WHERE origin='mac' AND started_at=?", (h.strftime("%Y-%m-%dT%H:%M"),))
        rollup_hour(con, h)
    con.commit()
    return len(rows)

DEFAULT_NO_TITLES = ("mail.google.com,outlook.live.com,outlook.office.com,com.apple.mail,"
                     "messages.google.com,web.whatsapp.com,com.apple.MobileSMS,net.whatsapp.WhatsApp,"
                     "chase.com,bankofamerica.com,wellsfargo.com,capitalone.com,venmo.com,paypal.com,"
                     "mychart.org,1password.com,com.1password.1password")


def _matches(source: str, patterns: list[str]) -> bool:
    s = source.lower()
    return any(s == p or s.endswith("." + p) or s.startswith(p + ".") for p in patterns)


def apply_privacy(sample: Sample | None, settings: dict) -> Sample | None:
    """never_track -> recorded only as 'private'. no_titles (or titles off) -> time kept, title dropped."""
    if sample is None:
        return None
    split = lambda k, d="": [x.strip().lower() for x in (settings.get(k) or d).split(",") if x.strip()]
    if _matches(sample.source, split("track_never")):
        return Sample("private", None, "private")
    if settings.get("track_titles", "on") == "off" or \
            _matches(sample.source, split("track_no_titles", DEFAULT_NO_TITLES)):
        return Sample(sample.source, None, sample.app)
    return sample


# ------------------------------------------------------------------ recording

class Tracker:
    def __init__(self, con, probe_fn=probe, interval=INTERVAL):
        self.con, self.probe, self.interval = con, probe_fn, interval
        self.open = {"focus": None, "background": None}   # lane -> activity row id
        self.settings, self._settings_at = {}, 0.0
        self.last_focus, self.last_focus_at = None, None
        names.load(con)
        scrub_private(con)

    def _load_settings(self):
        if time.monotonic() - self._settings_at > 30 or not self._settings_at:
            self.settings = {r[0]: r[1] for r in self.con.execute("SELECT key, value FROM settings")}
            self._settings_at = time.monotonic()
            global WINDOW_TITLES
            WINDOW_TITLES = self.settings.get("track_window_titles") == "on"

    def tick(self, now: datetime | None = None):
        now = (now or datetime.now()).replace(microsecond=0)
        self._load_settings()
        paused = self.settings.get("track_paused_until")
        if self.settings.get("off_mac") == "on" or (paused and now < datetime.fromisoformat(paused)):
            self.open = {"focus": None, "background": None}
            return
        focus, background, idle = self.probe()
        if focus and focus.app in names.TRANSIENT:
            # a password prompt / notification / permission dialog: keep crediting what you were in
            recent = self.last_focus_at and (now - self.last_focus_at).total_seconds() <= 120
            focus = self.last_focus if recent else None
        elif focus:
            if focus.source == focus.app:  # app window: title is "Name" or "Name: window title"
                names.learn(self.con, focus.app, (focus.title or "").split(": ", 1)[0])
            self.last_focus, self.last_focus_at = focus, now
        focus = apply_privacy(focus, self.settings)
        background = apply_privacy(background, self.settings)
        if background and background.source == "com.spotify.client" and self.settings.get("spotify_refresh_token"):
            background = None  # the Spotify API connection records these plays (with real durations)
        if focus and idle > IDLE_LIMIT and classify(focus.source)[0] not in PASSIVE:
            focus = None  # stepped away
        if background and focus and background.source == focus.source:
            background = None  # music app is in front; already counted as focus
        touched = set()
        for lane, s in (("focus", focus), ("background", background)):
            touched |= self._record(lane, s, now)
        for hour in touched:
            rollup_hour(self.con, hour)
        self.con.commit()

    def _record(self, lane, s: Sample | None, now: datetime) -> set[datetime]:
        row_id = self.open[lane]
        row = self.con.execute("SELECT * FROM activity WHERE id=?", (row_id,)).fetchone() if row_id else None
        if s is None:
            self.open[lane] = None
            return set()
        start = now - timedelta(seconds=self.interval)
        if row and row["source"] == s.source and row["title"] == s.title and \
                datetime.fromisoformat(row["end"]) >= start - timedelta(seconds=self.interval):
            self.con.execute("UPDATE activity SET end=? WHERE id=?", (now.isoformat(), row_id))
            first = datetime.fromisoformat(row["end"])
        else:
            cur = self.con.execute(
                "INSERT INTO activity(lane, start, end, source, title, app) VALUES(?,?,?,?,?,?)",
                (lane, start.isoformat(), now.isoformat(), s.source, s.title, s.app))
            self.open[lane] = cur.lastrowid
            first = start
        return {first.replace(minute=0, second=0), now.replace(minute=0, second=0)}

    def run(self):
        print(f"loop tracker: sampling every {self.interval}s. Ctrl-C to stop.", flush=True)
        while True:
            t0 = time.monotonic()
            try:
                self.tick()
            except Exception as e:  # never die on one bad sample
                print(f"tick failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            time.sleep(max(0.5, self.interval - (time.monotonic() - t0)))


def rollup_hour(con, hour: datetime):
    """Aggregate spans overlapping [hour, hour+1h) into one item per (lane, source)."""
    h0, h1 = hour.isoformat(), (hour + timedelta(hours=1)).isoformat()
    spans = con.execute(
        "SELECT lane, source, title, start, end FROM activity WHERE end > ? AND start < ?", (h0, h1)
    ).fetchall()
    agg: dict[tuple[str, str], dict] = {}
    for lane, source, title, start, end in spans:
        if source in names.TRANSIENT:
            continue
        secs = (min(datetime.fromisoformat(end), hour + timedelta(hours=1)) -
                max(datetime.fromisoformat(start), hour)).total_seconds()
        if secs <= 0:
            continue
        a = agg.setdefault((lane, source), {"secs": 0.0, "titles": {}})
        a["secs"] += secs
        if title:
            a["titles"][title] = a["titles"].get(title, 0) + secs
    for (lane, source), a in agg.items():
        mode, depth = classify(source)
        top = sorted(a["titles"], key=lambda t: -a["titles"][t])[:3]
        upsert_item(con, {
            "origin": "mac", "source": source, "title": " · ".join(top) or None,
            "mode": mode, "depth": depth, "started_at": hour.strftime("%Y-%m-%dT%H:%M"),
            "minutes": round(a["secs"] / 60, 2),
            "ext_key": f"mac:{lane}:{source}:{hour:%Y-%m-%dT%H}",
        })


def current(con, within_s: int = 60) -> dict | None:
    """The focus span still being recorded, if any."""
    r = con.execute("SELECT * FROM activity WHERE lane='focus' ORDER BY end DESC LIMIT 1").fetchone()
    if not r or (datetime.now() - datetime.fromisoformat(r["end"])).total_seconds() > within_s:
        return None
    mins = (datetime.fromisoformat(r["end"]) - datetime.fromisoformat(r["start"])).total_seconds() / 60
    mode, depth = classify(r["source"])
    names.load(con)
    if r["source"] == r["app"]:
        group, detail = names.context(r["app"], r["title"], app_level=True)
        what = " · ".join(x for x in (group, detail) if x)
    else:
        what = r["title"]
    return {"source": r["source"], "name": names.pretty(r["source"]),
            "app_name": names.pretty(r["app"]) if r["app"] != r["source"] else None,
            "detail": what or None, "minutes": round(mins), "mode": mode, "depth": depth}


def last_seen(con) -> str | None:
    r = con.execute("SELECT MAX(end) FROM activity").fetchone()
    return r[0] if r else None


# ------------------------------------------------------------------ launchd

def install(db_path: str, command: tuple[str, ...] = ("run",)) -> str:
    """Register `loop run` (tracker + scheduler + UI) with launchd: starts at login, restarts on crash."""
    project = Path(__file__).resolve().parents[2]
    log = Path.home() / ".loop" / "loop.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [sys.executable, "-m", "loop", "--db", db_path, *command],
            "WorkingDirectory": str(project),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }, f)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], check=True)
    return f"loop is running and will start at login. Open http://127.0.0.1:8765  (logs: {log})"


def uninstall() -> str:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    PLIST.unlink(missing_ok=True)
    return "loop stopped and removed from login items"
