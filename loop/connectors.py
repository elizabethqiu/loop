"""
Connectors: what loop can connect to today, and what's planned (TBD, not built yet).
Shown in You → Connectors. Order of the planned list = priority (see work-in-progress/NOTES.md "Later").
"""
from . import ai, runner
from .sources import gcal, usda_local

PLANNED = [
    ("Android screen time", "TikTok, X, Instagram time on your Pixel: the biggest gap in Consume.",
     "Small companion app reading Android usage stats."),
    ("Google Health Connect", "Steps and heart rate on days the watch isn't worn; a backup for Garmin.",
     "Same companion app."),
    ("Apple Health / Apple Watch", "Sleep, heart rate, HRV, steps and workouts if you use an iPhone + Apple Watch.",
     "Needs an iPhone: an iOS app reading HealthKit, or an app like Health Auto Export sending JSON to loop. "
     "Apple Watch can't pair with an Android phone."),
    ("Muse", "Meditation and focus minutes; sleep staging on a Muse S.",
     "Import Mind Monitor's CSV export (Muse has no public cloud API)."),
    ("Oura", "Sleep stages, HRV, readiness, temperature.", "Oura API v2 (personal access token / OAuth)."),
    ("Whoop", "Strain, recovery, HRV, sleep.", "Whoop developer API (OAuth)."),
    ("Fitbit / Google Fit", "Steps, sleep, heart rate.", "Fitbit Web API (OAuth), moving to Health Connect."),
    ("Strava", "Runs, rides and workouts with detail.", "Strava API (OAuth)."),
    ("Withings scale", "Automatic weigh-ins for a better TDEE and forecast.", "Withings API (OAuth)."),
    ("Samsung Health", "Galaxy Watch data.", "Through Health Connect on Android."),
    ("Kindle / Readwise", "Highlights and reading time, beyond your Goodreads shelves.", "Readwise API token."),
    ("Goodreads (live)", "Update the moment you start or finish a book, without re-running the site script.",
     "Goodreads' public shelf RSS (the same feed your site's import script uses)."),
    ("YouTube history", "What you watched, by channel.", "Google Takeout import."),
    ("Weather & daylight", "Real sunrise for morning-light tips; weather vs. steps.", "Open-Meteo (free, no key)."),
]


def _books(settings):
    from .sources import books
    s = books.summary(settings)
    c = s["counts"]
    return {"name": "Reading (your website)", "what": "Goodreads shelves from your site's books.ts: what you're reading, read and rated.",
            "how": "set books_file in Settings", "connected": s["connected"], "ok": s["connected"] or None,
            "message": f"{c['reading']} reading · {c['read']} read · {c['to_read']} to read" if s["connected"] else f"no file at {s['file']}"}


def connectors(con, settings: dict) -> dict:
    status = {s["source"]: s for s in runner.sources_status(con)}
    def st(key, label, what, how):
        s = status.get(key, {})
        msg = s.get("message") or ""
        if msg.startswith("{"):  # a sync result, e.g. {"days_synced": 2}
            msg = "connected" + (f" · last sync {s['at'][5:16].replace('T', ' ')}" if s.get("at") else "")
        return {"key": key, "name": label, "what": what, "how": how, "connected": bool(s.get("connected")),
                "ok": s.get("ok"), "message": msg}
    a = ai.status(settings)
    have = [
        st("mac", "Mac activity", "Apps, sites, window titles, music on this Mac.", "Built in."),
        st("garmin", "Garmin", "Sleep, resting heart rate, steps, activity.", "python3 -m loop connect garmin"),
        st("spotify", "Spotify", "Listening time; genres and moods.", "python3 -m loop connect spotify"),
        st("lastfm", "Last.fm", "Spotify listening time, genres and moods.", "You → Connectors → Connect Last.fm"),
        {**st("gcal", "Google Calendar", "Today's events on your homepage, checked against your energy and sleep.",
              "You → Connectors → Connect Google Calendar"), "connected": gcal.is_connected(con)},
        {"name": "Claude (✦ Ask)", "what": "AI coach for your stats.", "how": "✦ Ask → setup",
         "connected": a["configured"], "ok": a["configured"] or None,
         "message": "ready" if a["configured"] else ("add your API key" if a["sdk_installed"] else "pip3 install anthropic, then add your key")},
        _books(settings),
        st("zotero", "Zotero", "Your research library: papers and books you save, tags, collections.", "You → Connectors → Connect Zotero"),
        {"name": "USDA food database", "what": "1.9M foods, offline.", "how": "python3 -m loop usda-build",
         "connected": usda_local.available(), "ok": usda_local.available() or None,
         "message": "on this Mac" if usda_local.available() else "not built (search uses the USDA website)"},
        {"name": "MyNetDiary import", "what": "Food, exercise, water, weight history.", "how": "python3 -m loop import-mynetdiary FILE.xls",
         "connected": None, "ok": None, "message": "one-time import"},
    ]
    have[0]["connected"] = True   # the Mac tracker is built in
    for x in have:
        if x.get("key"):
            x["on"] = settings.get(f"off_{x['key']}") != "on"
            if not x["on"]:
                x["ok"], x["message"] = None, "off · nothing new is collected"
    return {"connected": have, "planned": [{"name": n, "what": w, "how": h} for n, w, h in PLANNED]}
