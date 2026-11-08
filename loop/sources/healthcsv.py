"""
Generic daily health CSV import, the escape hatch for any pipe (Health Connect export,
Garmin data export, a spreadsheet you keep by hand).

Columns (header row required, all but `date` optional):
  date, sleep_start, sleep_end, sleep_minutes, resting_hr, hrv, steps, active_minutes

`date` is the morning you woke up. sleep_start / sleep_end accept full ISO datetimes
('2026-11-03T01:10') or bare 'HH:MM' (sleep_start after noon is taken as the previous evening).
"""
import csv
from datetime import date, datetime, timedelta

from ..db import upsert_daily


def _dt(value: str, wake_date: date, is_start: bool) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    if "T" in v or " " in v:
        return datetime.fromisoformat(v.replace(" ", "T")).isoformat(timespec="minutes")
    h, m = map(int, v.split(":")[:2])
    d = wake_date - timedelta(days=1) if (is_start and h >= 12) else wake_date
    return datetime(d.year, d.month, d.day, h, m).isoformat(timespec="minutes")


def _num(v):
    v = (v or "").strip()
    return float(v) if v else None


def import_csv(con, path: str) -> dict:
    n = 0
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["date"].strip())
            start = _dt(r.get("sleep_start"), d, True)
            end = _dt(r.get("sleep_end"), d, False)
            mins = _num(r.get("sleep_minutes"))
            if mins is None and start and end:
                mins = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
            steps = _num(r.get("steps"))
            upsert_daily(con, {
                "date": d.isoformat(), "sleep_start": start, "sleep_end": end, "sleep_minutes": mins,
                "resting_hr": _num(r.get("resting_hr")), "hrv": _num(r.get("hrv")),
                "steps": int(steps) if steps is not None else None,
                "active_minutes": _num(r.get("active_minutes")), "origin": "csv",
            })
            n += 1
    con.commit()
    return {"rows": n}
