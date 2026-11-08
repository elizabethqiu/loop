"""Fills a database with 5 weeks of plausible fake data so the UI has something to show.

Test account (used by `python3 -m loop test`; the test database is wiped and re-seeded every start):
  Username: test
  Password: test
These only work on ~/.loop/test.db. Your real loop (~/.loop/loop.db) never has this account.
"""
import random
from datetime import date, datetime, timedelta

from .consume import classify
from .db import upsert_daily, upsert_item

SOURCES = [  # source, typical minutes per session, sessions per day, hours of day it shows up
    ("x.com", 20, 4, [0, 1, 9, 13, 22, 23]),
    ("tiktok.com", 25, 2, [0, 1, 23]),
    ("youtube.com", 30, 1, [20, 21, 22]),
    ("substack.com", 25, 1, [10, 19]),
    ("arxiv.org", 40, 1, [11, 15]),
    ("docs.google.com", 45, 1, [11, 14, 16]),
    ("com.microsoft.VSCode", 60, 2, [11, 12, 15, 16, 17]),
    ("github.com", 20, 1, [12, 16]),
    ("claude.ai", 25, 1, [11, 15]),
    ("reddit.com", 15, 1, [13, 23]),
    ("com.spotify.client", 45, 2, [10, 14, 16, 20]),
    ("mail.google.com", 15, 1, [10, 17]),
]
TAG_BIAS = {
    "x.com": ["noise", "noise", "anxious", "think"],
    "tiktok.com": ["noise", "noise", "noise", "anxious"],
    "reddit.com": ["noise", "think", "noise"],
    "substack.com": ["think", "think", "decision"],
    "arxiv.org": ["think", "decision"],
    "youtube.com": ["think", "noise"],
}


def seed(con, today: date | None = None, days: int = 35):
    rnd = random.Random(7)
    today = today or date.today()
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('target_wake','08:30')")
    onset = 60  # minutes after midnight; drifts later over the last week
    for k in range(days, -1, -1):
        d = today - timedelta(days=k)
        drift = max(0, 7 - k) * 18 if k < 7 else 0
        o = onset + rnd.randint(-40, 40) + drift
        start = datetime.combine(d, datetime.min.time()) + timedelta(minutes=o)
        dur = rnd.randint(390, 500) - (drift // 3)
        end = start + timedelta(minutes=dur)
        upsert_daily(con, {
            "date": end.date().isoformat(),
            "sleep_start": start.isoformat(timespec="minutes"),
            "sleep_end": end.isoformat(timespec="minutes"),
            "sleep_minutes": dur,
            "resting_hr": 56 + rnd.randint(-2, 2) + (4 if k < 5 else 0),
            "hrv": None,
            "steps": rnd.randint(4000, 14000),
            "active_minutes": rnd.randint(10, 70),
            "active_kcal": rnd.randint(180, 620),
            "step_goal": 7010,
            "intensity_week_goal": 150,
            "distance_m": rnd.randint(3000, 10000),
            "origin": "demo",
        })
        if k <= 7:  # 15-min step buckets for the last week
            for q in range(7 * 4, 23 * 4):
                busy = q in range(8 * 4, 9 * 4) or q in range(12 * 4, 13 * 4) or q in range(17 * 4, 19 * 4)
                st = rnd.randint(150, 900) if busy else (rnd.randint(0, 200) if rnd.random() < .6 else 0)
                con.execute("INSERT OR REPLACE INTO steps_intraday(start, steps, level) VALUES(?,?,?)",
                            (f"{end.date().isoformat()}T{q // 4:02d}:{(q % 4) * 15:02d}", st, None))
        if k <= 34:
            _music(con, rnd, end.date(), sad_bias=0.55 if k < 7 else 0.3)
        if k == 0:
            continue  # leave today's consumption to real syncs
        for src, mins, per_day, hours in SOURCES:
            for _ in range(per_day):
                if rnd.random() < 0.25:
                    continue
                h = rnd.choice(hours)
                mode, depth = classify(src)
                upsert_item(con, {
                    "origin": "demo", "source": src, "title": None, "mode": mode, "depth": depth,
                    "started_at": f"{d.isoformat()}T{h:02d}:{rnd.randint(0, 5) * 10:02d}",
                    "minutes": max(5, round(rnd.gauss(mins, mins / 3))),
                    "ext_key": f"demo:{src}:{d}:{h}:{rnd.random()}",
                })
            if src in TAG_BIAS and k > 2 and rnd.random() < 0.5:
                con.execute("INSERT OR IGNORE INTO tags(source,day,tag) VALUES(?,?,?)",
                            (src, d.isoformat(), rnd.choice(TAG_BIAS[src])))
        if rnd.random() < 0.3:
            upsert_item(con, {"origin": "manual", "source": "book: The Dawn of Everything",
                              "title": None, "mode": "reading", "depth": "deep",
                              "started_at": f"{d.isoformat()}T21:30", "minutes": 35, "ext_key": None})
        for target, jitter, skip in ((9 * 60, 60, 0.3), (12 * 60 + 30, 50, 0.1), (18 * 60, 70, 0.1)):
            if rnd.random() > skip:
                t = datetime.combine(d, datetime.min.time()) + timedelta(minutes=target + rnd.randint(-jitter, jitter))
                con.execute("INSERT INTO events(kind, at, note) VALUES('meal', ?, NULL)",
                            (t.isoformat(timespec="minutes"),))
        for hh in rnd.sample([9, 11, 14, 16], rnd.randint(1, 3)):
            con.execute("INSERT INTO events(kind, at, value) VALUES('caffeine', ?, 95)",
                        (f"{d.isoformat()}T{hh:02d}:15",))
    con.execute("INSERT INTO events(kind, at, value) VALUES('caffeine', ?, 95)",
                (f"{today.isoformat()}T15:30",))
    con.commit()


ARTISTS = {  # artist: (genre tags, track moods to choose from)
    "Phoebe Bridgers": (["indie", "indie folk", "singer-songwriter"], ["sad", "melancholic"]),
    "Japanese Breakfast": (["indie pop", "dream pop"], ["upbeat", "dreamy"]),
    "Frank Ocean": (["r&b", "alternative r&b"], ["chill", "romantic", "sad"]),
    "Fred again..": (["electronic", "house"], ["energetic", "emotional"]),
    "Mitski": (["indie rock", "art pop"], ["sad", "heartbreak"]),
    "SZA": (["r&b", "pop"], ["romantic", "chill"]),
}


def _music(con, rnd, d, sad_bias=0.3):
    import json
    for name, (genres, _) in ARTISTS.items():
        con.execute("INSERT OR IGNORE INTO music_tags VALUES('artist', ?, ?, 'demo', '')",
                    (name, json.dumps([[g, 100 - 20 * i] for i, g in enumerate(genres)])))
    for i in range(rnd.randint(8, 30)):
        names = list(ARTISTS)
        artist = rnd.choice(["Phoebe Bridgers", "Mitski"]) if rnd.random() < sad_bias else rnd.choice(names)
        moods = ARTISTS[artist][1]
        track = f"{artist.split()[0]} song {rnd.randint(1, 12)}"
        h = rnd.choice([9, 10, 13, 14, 16, 20, 22, 23])
        at = f"{d.isoformat()}T{h:02d}:{rnd.randint(0, 59):02d}"
        mins = round(rnd.uniform(1, 4.5), 2)
        con.execute("INSERT OR IGNORE INTO music_plays(played_at, start, track, artist, listened_min, duration_min) VALUES(?,?,?,?,?,?)",
                    (f"{at}:{i:02d}Z", at, track, artist, mins, mins))
        con.execute("INSERT OR IGNORE INTO music_tags VALUES('track', ?, ?, 'demo', '')",
                    (f"{artist}\t{track}", json.dumps([[rnd.choice(moods), 100]])))


TEST_USERNAME = "test"
TEST_PASSWORD = "test"


def make_test_account(con):
    """The throwaway test login. Skips the 8-character rule on purpose: it only ever exists in test.db."""
    from . import auth
    import secrets as _s
    from datetime import datetime as _dt
    auth.ensure(con)
    salt = _s.token_hex(16)
    con.execute("INSERT INTO users(name, username, pw_hash, salt, created_at) VALUES(?,?,?,?,?)",
                ("Test", TEST_USERNAME, auth._hash(TEST_PASSWORD, salt), salt, _dt.now().isoformat(timespec="seconds")))
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('test_mode', 'on')")
    con.commit()
