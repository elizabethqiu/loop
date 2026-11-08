"""
Garmin Connect -> daily rows (sleep window, resting HR, HRV, steps, active minutes) and workouts (activities).

Uses the unofficial `garminconnect` library (pip install garminconnect), which logs in
with your Garmin account. The official Garmin Health API is partner-gated, so this is
the practical path for a personal app. It can break when Garmin changes its login
flow; if it does, fall back to `loop import-health` with a CSV (see healthcsv.py).

Connect once in a terminal: `python3 -m loop connect garmin` (asks for email, password,
and an MFA code if you use one). The password is not stored; Garmin's session tokens are
cached in ~/.loop/garmin_tokens and the background runner reuses them.

Forerunner 235 notes: sleep window, resting HR, steps and intensity minutes sync fine.
It does not record overnight HRV, so `hrv` will stay empty from this watch.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..db import upsert_daily

TOKENS = Path(os.environ.get("LOOP_GARMIN_TOKENS", Path.home() / ".loop" / "garmin_tokens"))


class GarminError(RuntimeError):
    pass


def _garmin_cls():
    try:
        from garminconnect import Garmin
    except ImportError as e:
        raise GarminError("Install the Garmin client first:  pip install garminconnect") from e
    return Garmin


def is_connected(con=None) -> bool:
    return TOKENS.is_dir() and any(TOKENS.iterdir())


def client():
    """Log in with cached session tokens. Never asks for a password (safe for the background runner)."""
    Garmin = _garmin_cls()
    if not is_connected():
        raise GarminError("Garmin not connected. Run once in a terminal:  python3 -m loop connect garmin")
    g = Garmin()
    try:
        g.login(str(TOKENS))
    except Exception as e:
        raise GarminError(f"Garmin session expired or rejected ({e}). Run:  python3 -m loop connect garmin") from e
    return g


def connect_interactive() -> str:
    """One-time login in a terminal. Your password is used once and never stored;
    only Garmin's session tokens are kept in ~/.loop/garmin_tokens."""
    import getpass
    Garmin = _garmin_cls()
    email = input("Garmin Connect email: ").strip()
    password = getpass.getpass("Garmin Connect password (not stored): ")
    try:
        g = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    except TypeError:  # older garminconnect without MFA support
        g = Garmin(email, password)
    TOKENS.mkdir(parents=True, exist_ok=True)
    # garminconnect >= 0.3 loads/saves the session itself when given a tokenstore path;
    # older versions kept it on `g.garth`. Save explicitly through whichever exists.
    g.login(str(TOKENS))
    session = getattr(g, "client", None) or getattr(g, "garth", None)
    if session is not None and hasattr(session, "dump"):
        session.dump(str(TOKENS))
    if not is_connected():
        raise GarminError("Logged in, but no session was saved. Please report the garminconnect version.")
    return f"Garmin connected. Session saved to {TOKENS}"


def _local(ms) -> str | None:
    """Garmin '...TimestampLocal' values are epoch-ms already shifted to local wall time."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes")


def parse_day(d: date, sleep: dict, stats: dict, hrv: dict | None) -> dict:
    dto = (sleep or {}).get("dailySleepDTO") or {}
    secs = dto.get("sleepTimeSeconds")
    hrv_sum = (hrv or {}).get("hrvSummary") or {}
    active = None
    if stats:
        mod, vig = stats.get("moderateIntensityMinutes"), stats.get("vigorousIntensityMinutes")
        if mod is not None or vig is not None:
            active = (mod or 0) + 2 * (vig or 0)
    return {
        "date": d.isoformat(),
        "sleep_start": _local(dto.get("sleepStartTimestampLocal")),
        "sleep_end": _local(dto.get("sleepEndTimestampLocal")),
        "sleep_minutes": round(secs / 60) if secs else None,
        "resting_hr": (stats or {}).get("restingHeartRate") or (sleep or {}).get("restingHeartRate"),
        "hrv": hrv_sum.get("lastNightAvg"),
        "steps": (stats or {}).get("totalSteps"),
        "active_minutes": active,
        "active_kcal": (stats or {}).get("activeKilocalories"),
        "step_goal": (stats or {}).get("dailyStepGoal"),
        "intensity_week_goal": (stats or {}).get("intensityMinutesGoal"),
        "distance_m": (stats or {}).get("totalDistanceMeters"),
        "origin": "garmin",
    }


STAGES = {0: "deep", 1: "light", 2: "rem", 3: "awake"}  # Garmin sleepLevels activityLevel


def _gmt_local(v) -> str | None:
    """A GMT time (ISO string or epoch ms) -> local wall time 'YYYY-MM-DDTHH:MM'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        utc = datetime.fromtimestamp(v / 1000, timezone.utc)
    else:
        utc = datetime.fromisoformat(str(v).split(".")[0]).replace(tzinfo=timezone.utc)
    return utc.astimezone().replace(tzinfo=None).isoformat(timespec="minutes")


def parse_sleep_detail(d: date, sleep: dict) -> dict | None:
    """Garmin's sleep payload -> one sleep_detail row (stages, score, overnight heart rate)."""
    dto = (sleep or {}).get("dailySleepDTO") or {}
    if not dto.get("sleepTimeSeconds"):
        return None
    sc = dto.get("sleepScores") or {}
    m = lambda k: round(dto[k] / 60) if dto.get(k) is not None else None
    ranges = {}
    for k, key in (("deep", "deepPercentage"), ("light", "lightPercentage"), ("rem", "remPercentage")):
        r = sc.get(key) or {}
        if r.get("optimalStart") is not None:
            ranges[k] = [r["optimalStart"], r["optimalEnd"]]
    levels = []
    for lv in sleep.get("sleepLevels") or []:
        a, b = _gmt_local(lv.get("startGMT")), _gmt_local(lv.get("endGMT"))
        stage = STAGES.get(int(round(lv.get("activityLevel", -1))))
        if a and b and stage and b > a:
            levels.append([a, b, stage])
    hr = [[_gmt_local(p["startGMT"]), p["value"]] for p in sleep.get("sleepHeartRate") or []
          if p.get("value") and p.get("startGMT")]
    return {
        "date": d.isoformat(),
        "score": (sc.get("overall") or {}).get("value"),
        "quality": (sc.get("overall") or {}).get("qualifierKey"),
        "deep_min": m("deepSleepSeconds"), "light_min": m("lightSleepSeconds"),
        "rem_min": m("remSleepSeconds"), "awake_min": m("awakeSleepSeconds"),
        "awake_count": dto.get("awakeCount"),
        "avg_stress": dto.get("avgSleepStress"),
        "respiration": dto.get("averageRespirationValue"),
        "resting_hr": sleep.get("restingHeartRate"),
        "ranges": json.dumps(ranges), "levels": json.dumps(sorted(levels)), "hr": json.dumps(hr),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_sleep_detail(con, row: dict | None):
    if not row:
        return
    cols = list(row)
    con.execute(f"INSERT OR REPLACE INTO sleep_detail({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                [row[c] for c in cols])


def parse_steps(buckets: list[dict]) -> list[tuple[str, int, str | None]]:
    """15-min step buckets (GMT) -> [(local start ISO, steps, activity level)]."""
    out = []
    for b in buckets or []:
        start = b.get("startGMT")
        if not start or b.get("steps") is None:
            continue
        utc = datetime.fromisoformat(start.split(".")[0]).replace(tzinfo=timezone.utc)
        out.append((utc.astimezone().replace(tzinfo=None).isoformat(timespec="minutes"),
                    int(b["steps"]), b.get("primaryActivityLevel")))
    return out


def parse_activity(a: dict) -> dict | None:
    """One Garmin activity (run, Pilates, strength...) -> a workouts row. Local start time, minutes, kcal."""
    aid, start = a.get("activityId"), a.get("startTimeLocal")
    if not aid or not start:
        return None
    kind = ((a.get("activityType") or {}).get("typeKey") or "").replace("_", " ").strip()
    secs = a.get("duration") or a.get("movingDuration") or 0
    return {"at": str(start).replace(" ", "T")[:16], "name": (a.get("activityName") or kind or "Workout").strip(),
            "minutes": round(float(secs) / 60, 1), "kcal": a.get("calories"), "ext_key": f"garmin:{aid}"}


def save_activities(con, acts) -> int:
    n = 0
    for a in acts or []:
        row = parse_activity(a)
        if not row:
            continue
        # keep your edits to the name; refresh time, length and calories from the watch
        con.execute("""INSERT INTO workouts(at, name, minutes, kcal, origin, ext_key) VALUES(?,?,?,?, 'garmin', ?)
                       ON CONFLICT(ext_key) DO UPDATE SET at=excluded.at, minutes=excluded.minutes, kcal=excluded.kcal""",
                    (row["at"], row["name"], row["minutes"], row["kcal"], row["ext_key"]))
        n += 1
    return n


def sync(con, days: int = 14, today: date | None = None) -> dict:
    from .. import egress
    egress.record("garmin", "https://connect.garmin.com", f"download the last {days} days with your saved session")
    g = client()
    today = today or date.today()
    n = 0
    for k in range(days):
        d = today - timedelta(days=k)
        ds = d.isoformat()
        sleep = _safe(g.get_sleep_data, ds)
        stats = _safe(g.get_stats, ds)
        hrv = _safe(getattr(g, "get_hrv_data", None), ds)
        row = parse_day(d, sleep, stats, hrv)
        if any(row[k] is not None for k in ("sleep_start", "resting_hr", "steps")):
            upsert_daily(con, row)
            n += 1
        save_sleep_detail(con, parse_sleep_detail(d, sleep))
        for start, steps, level in parse_steps(_safe(g.get_steps_data, ds)):
            con.execute("INSERT OR REPLACE INTO steps_intraday(start, steps, level) VALUES(?,?,?)",
                        (start, steps, level))
    acts = save_activities(con, _safe(getattr(g, "get_activities_by_date", None), (today - timedelta(days=days - 1)).isoformat(), today.isoformat()))
    con.commit()
    return {"days_synced": n, "workouts": acts}


def sync_today(con, today: date | None = None) -> dict:
    """Light pull for the frequent job: today's steps, activity and 15-minute step buckets (2 calls),
    plus last night's sleep until it has synced from the watch (1 more call), so it shows up soon after you wake."""
    from .. import egress
    egress.record("garmin", "https://connect.garmin.com", "download today's steps and activity with your saved session")
    g = client()
    d = today or date.today()
    ds = d.isoformat()
    have = con.execute("SELECT 1 FROM sleep_detail WHERE date=?", (ds,)).fetchone()
    slept = None
    if not have:
        sleep = _safe(getattr(g, "get_sleep_data", None), ds)
        detail = parse_sleep_detail(d, sleep)
        if detail:
            upsert_daily(con, parse_day(d, sleep, None, None))
            save_sleep_detail(con, detail)
            slept = detail["date"]
    stats = _safe(g.get_stats, ds)
    if stats:
        row = parse_day(d, None, stats, None)
        upsert_daily(con, {k: v for k, v in row.items() if k not in ("sleep_start", "sleep_end", "sleep_minutes", "hrv")})
    buckets = parse_steps(_safe(g.get_steps_data, ds))
    for start, steps, level in buckets:
        con.execute("INSERT OR REPLACE INTO steps_intraday(start, steps, level) VALUES(?,?,?)", (start, steps, level))
    save_activities(con, _safe(getattr(g, "get_activities_by_date", None), ds, ds))
    con.commit()
    return {"steps": (stats or {}).get("totalSteps"), "buckets": len(buckets), **({"sleep": slept} if slept else {})}


def _safe(fn, *a):
    if not fn:
        return None
    try:
        return fn(*a)
    except Exception:
        return None
