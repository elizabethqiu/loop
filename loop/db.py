"""SQLite storage. Everything lives in one local file (default ~/.loop/loop.db)."""
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  origin TEXT NOT NULL,          -- mac | manual | lastfm | demo
  source TEXT NOT NULL,          -- the thing you could cut: domain, app, artist, book, show
  title TEXT,
  mode TEXT NOT NULL,            -- reading|writing|watching|listening|working|social|other
  depth TEXT NOT NULL,           -- deep|skim|doom
  started_at TEXT NOT NULL,      -- local ISO 'YYYY-MM-DDTHH:MM'
  minutes REAL NOT NULL,
  ext_key TEXT UNIQUE            -- idempotency key for synced items
);
CREATE INDEX IF NOT EXISTS items_started ON items(started_at);

-- After-the-fact influence tags, applied per source per day.
CREATE TABLE IF NOT EXISTS tags(
  source TEXT NOT NULL,
  day TEXT NOT NULL,
  tag TEXT NOT NULL,             -- think | anxious | decision | noise
  PRIMARY KEY(source, day, tag)
);

-- User overrides for how a source is classified, and whether it's been cut.
CREATE TABLE IF NOT EXISTS source_rules(
  source TEXT PRIMARY KEY,
  mode TEXT,
  depth TEXT,
  status TEXT NOT NULL DEFAULT 'active',  -- active | cut | keep
  changed_at TEXT
);

-- One row per wake date: the sleep that ended that morning + that day's body stats.
CREATE TABLE IF NOT EXISTS daily(
  date TEXT PRIMARY KEY,
  sleep_start TEXT,
  sleep_end TEXT,
  sleep_minutes REAL,
  resting_hr REAL,
  hrv REAL,
  steps INTEGER,
  active_minutes REAL,
  origin TEXT
);

-- Last night in detail (Garmin): stages, score, heart rate. One row per wake date.
CREATE TABLE IF NOT EXISTS sleep_detail(
  date TEXT PRIMARY KEY,
  score INTEGER,
  quality TEXT,                  -- Garmin's qualifier: EXCELLENT | GOOD | FAIR | POOR
  deep_min REAL, light_min REAL, rem_min REAL, awake_min REAL,
  awake_count INTEGER,
  avg_stress REAL,
  respiration REAL,
  resting_hr REAL,
  ranges TEXT,                   -- JSON {deep|light|rem: [lo%, hi%]} Garmin's optimal ranges
  levels TEXT,                   -- JSON [[start, end, stage]] local time; stage deep|light|rem|awake
  hr TEXT,                       -- JSON [[local time, bpm]]
  fetched_at TEXT
);

-- Point events you log by hand: meal, caffeine (value = mg), wake.
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  at TEXT NOT NULL,
  value REAL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);

CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

-- Raw spans from loop's own Mac tracker (sources/mac.py). Rolled up hourly into items.
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY,
  lane TEXT NOT NULL,            -- focus (frontmost) | background (music playing)
  start TEXT NOT NULL,
  end TEXT NOT NULL,
  source TEXT NOT NULL,          -- domain or bundle id
  title TEXT,                    -- tab title / track / app name
  app TEXT
);
CREATE INDEX IF NOT EXISTS activity_end ON activity(end);

-- Garmin 15-minute step buckets (local time), for hourly movement.
CREATE TABLE IF NOT EXISTS steps_intraday(
  start TEXT PRIMARY KEY,
  steps INTEGER NOT NULL,
  level TEXT
);

-- Spotify plays with ids, for listening time + genre/mood analysis.
CREATE TABLE IF NOT EXISTS music_plays(
  played_at TEXT PRIMARY KEY,    -- Spotify's timestamp (UTC ISO)
  start TEXT NOT NULL,           -- local
  track_id TEXT, track TEXT,
  artist_id TEXT, artist TEXT,
  album TEXT,
  duration_min REAL,
  listened_min REAL              -- capped by the gap to the next play (skips)
);
CREATE INDEX IF NOT EXISTS music_plays_start ON music_plays(start);

-- AI-written text cached per day (e.g. your daily summary).
CREATE TABLE IF NOT EXISTS ai_cache(date TEXT, kind TEXT, text TEXT, model TEXT, created_at TEXT, PRIMARY KEY(date, kind));

-- Song lengths from Last.fm (its play feed has none), cached.
CREATE TABLE IF NOT EXISTS track_lengths(key TEXT PRIMARY KEY, minutes REAL);

-- Genre / mood tags per artist or track, cached.
CREATE TABLE IF NOT EXISTS music_tags(
  kind TEXT NOT NULL,            -- artist | track
  key TEXT NOT NULL,             -- artist name | 'artist\ttrack'
  tags TEXT NOT NULL,            -- JSON list of [tag, weight 0-100]
  source TEXT,
  fetched_at TEXT,
  PRIMARY KEY(kind, key)
);

-- Workouts (imported from MyNetDiary; Garmin activities later).
CREATE TABLE IF NOT EXISTS workouts(
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  name TEXT,
  minutes REAL,
  kcal REAL,
  origin TEXT,
  ext_key TEXT UNIQUE
);

-- Daily body/intake measurements: water (fl oz), weight (lbs), height (in).
CREATE TABLE IF NOT EXISTS measurements(
  date TEXT NOT NULL,
  kind TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT,
  origin TEXT,
  PRIMARY KEY(date, kind)
);

-- One journal entry per day.
CREATE TABLE IF NOT EXISTS journal(
  date TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  updated_at TEXT
);

-- The name macOS shows for each app (learned by the tracker): io.fant.colleague → Energy.
CREATE TABLE IF NOT EXISTS app_names(bundle TEXT PRIMARY KEY, name TEXT NOT NULL);

-- Changes to your profile, goals and settings (for the About me doc).
CREATE TABLE IF NOT EXISTS profile_log(
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  field TEXT NOT NULL,
  old TEXT,
  new TEXT
);

-- Health of each continuous feed, written by the runner.
CREATE TABLE IF NOT EXISTS sync_status(
  source TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  ok INTEGER,                    -- 1 ok, 0 error, NULL not connected
  message TEXT
);
"""

DEFAULT_SETTINGS = {
    "target_wake": "08:30",
    "sleep_need_h": "8",
    "meal_times": "09:00,12:30,18:00",
    "meal_window_min": "45",
    "meal_windows": "08:00-09:00,12:00-13:00,18:00-19:00",
    "meal_grace_min": "30",
    "caffeine_half_life_h": "5",
    "garmin_every_min": "60",
    "move_goal_kcal": "400",
    "move_hours_goal": "10",
    "caffeine_cutoff_h": "8",
    "sleep_goal": "consistent",
    "goal_bedtime": "",
    "card_steps": "auto",
    "units": "imperial",              # imperial (lb, ft/in) or metric (kg, cm); stored values stay in lb/in
    "deep_work_goal_min": "180",
    "theme": "sun",
    "focus_style": "deliberate",
    "focus_calendar": "ask",
    "reflect_cadence": "daily",
    "goal_wake": "",
    "target_fruit_veg_g": "400",
    "target_protein_g": "100",
    "target_fiber_g": "30",
    "target_kcal": "",
    "track_titles": "on",
    "track_window_titles": "off",
    "track_never": "",
    "track_no_titles": "mail.google.com,outlook.live.com,outlook.office.com,com.apple.mail,messages.google.com,web.whatsapp.com,com.apple.MobileSMS,net.whatsapp.WhatsApp,chase.com,bankofamerica.com,wellsfargo.com,capitalone.com,venmo.com,paypal.com,mychart.org,1password.com,com.1password.1password",
}


# Columns added after first release; connect() adds any that are missing.
MIGRATIONS = {
    "daily": [("active_kcal", "REAL"), ("step_goal", "INTEGER"), ("intensity_week_goal", "REAL"),
              ("distance_m", "REAL"), ("steps_manual", "REAL")],  # steps you add/subtract by hand
}


def default_path() -> str:
    return os.environ.get("LOOP_DB") or str(Path.home() / ".loop" / "loop.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or default_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")  # tracker and server write concurrently
    if path != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.execute("DELETE FROM items WHERE origin='mac' AND source IN ('com.apple.SecurityAgent','com.apple.UserNotificationCenter',"
                "'com.apple.accessibility.universalAccessAuthWarn','com.apple.loginwindow','com.apple.controlcenter')")
    if path != ":memory:":
        os.chmod(path, 0o600)  # only your user account can read your data
        os.chmod(Path(path).parent, 0o700)
        for extra in ("-wal", "-shm"):
            if Path(path + extra).exists():
                os.chmod(path + extra, 0o600)
        log = Path(path).parent / "loop.log"
        if log.exists():
            os.chmod(log, 0o600)
    from . import auth, build, food
    from .sources import gcal
    food.ensure(con)
    auth.ensure(con)
    build.ensure(con)
    gcal.ensure(con)
    from . import cycle, review
    from . import goals
    goals.ensure(con)
    from .sources import zotero
    zotero.ensure(con)
    from . import feedback, focus, nudge
    focus.ensure(con)
    feedback.ensure(con)
    nudge.ensure(con)
    cycle.ensure(con)
    con.execute("UPDATE settings SET value=substr(value,1,7) WHERE key='profile_birthdate' AND length(value) > 7")
    review.ensure(con)
    from . import brain
    brain.ensure(con)
    from .sources import curius
    curius.ensure(con)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    con.commit()
    return con


def settings(con) -> dict:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}


def rows(con, sql, params=()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params)]


def upsert_daily(con, row: dict):
    """Merge a daily row: non-null incoming fields overwrite, nulls keep what's there."""
    cols = ["sleep_start", "sleep_end", "sleep_minutes", "resting_hr", "hrv", "steps", "active_minutes", "origin",
            "active_kcal", "step_goal", "intensity_week_goal", "distance_m"]
    con.execute("INSERT OR IGNORE INTO daily(date) VALUES(?)", (row["date"],))
    sets = [(c, row[c]) for c in cols if row.get(c) is not None]
    if sets:
        con.execute(
            f"UPDATE daily SET {', '.join(c + '=?' for c, _ in sets)} WHERE date=?",
            [v for _, v in sets] + [row["date"]],
        )


def upsert_item(con, item: dict):
    con.execute(
        """INSERT INTO items(origin, source, title, mode, depth, started_at, minutes, ext_key)
           VALUES(:origin, :source, :title, :mode, :depth, :started_at, :minutes, :ext_key)
           ON CONFLICT(ext_key) DO UPDATE SET minutes=excluded.minutes, title=excluded.title""",
        {"title": None, "ext_key": None, **item},
    )
