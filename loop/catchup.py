"""
Catch up: a few short cards on Today for what you missed and what's coming that matters.

Built only from data already on this Mac, by simple rules (no AI, nothing sent anywhere). Each card is
"missed" (happened while you weren't looking) or "ahead" (likely to matter soon), ranked by priority.
You can dismiss a card for the day.
"""
import json
from datetime import date, datetime, timedelta

from . import history


def _ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS catchup_dismissed(date TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(date, key))")
    con.execute("CREATE TABLE IF NOT EXISTS catchup_snooze(key TEXT PRIMARY KEY, until TEXT NOT NULL)")   # until 9999 = done for good


def dismiss(con, d: date, key: str, until: str | None = None, now: datetime | None = None):
    """Swipe left: done with it (never shown again). Swipe right: bring it back later_today / tomorrow / next_week."""
    _ensure(con)
    now = now or datetime.now()
    morning = lambda days: datetime.combine(now.date() + timedelta(days=days), datetime.min.time()).replace(hour=8)
    when = {"later_today": now + timedelta(hours=3), "tomorrow": morning(1), "in_3_days": morning(3),
            "next_week": morning(7)}.get(until or "")
    if not when and (until or "").startswith("date:"):            # a day you picked: 8am that day
        try:
            when = datetime.combine(date.fromisoformat(until[5:15]), datetime.min.time()).replace(hour=8)
        except ValueError:
            when = None
    value = when.isoformat(timespec="minutes") if when else "9999-12-31T00:00"
    con.execute("INSERT OR REPLACE INTO catchup_snooze(key, until) VALUES(?,?)", (key[:80], value))
    con.commit()


def _q(con, sql, args=()):
    try:
        return con.execute(sql, args).fetchall()
    except Exception:   # a table that doesn't exist yet (feature never used)
        return []


def cards(con, d: date, settings: dict, now: datetime | None = None) -> list[dict]:
    _ensure(con)
    now = now or datetime.now()
    ds, y = d.isoformat(), (d - timedelta(days=1)).isoformat()
    out = []

    def add(key, kind, prio, title, body, tab=None):
        out.append({"key": key, "kind": kind, "prio": prio, "title": title, "body": body, "tab": tab})

    # sleep debt: the same number as the Sleep tab (energy.sleep_debt, 14 nights, recent ones weighted more).
    # A card when it's moderate or high, or when the last two nights were both short.
    need = float(settings.get("sleep_need_h") or 8) * 60
    try:
        from . import energy
        days = [dict(r) for r in con.execute("SELECT * FROM daily WHERE date <= ? ORDER BY date", (ds,))]
        sd = energy.sleep_debt(days, d, settings)
    except Exception:
        sd = {"ok": False}
    nights = [r[0] for r in _q(con, "SELECT sleep_minutes FROM daily WHERE date <= ? AND date > ? AND sleep_minutes > 0 ORDER BY date DESC",
                                (ds, (d - timedelta(days=3)).isoformat()))]
    two_short = len(nights) >= 2 and all(n < need - 45 for n in nights[:2])
    if sd.get("ok") and (sd["level"] != "low" or two_short):
        add("sleep_debt", "ahead", 90, f"Sleep debt: {sd['hours']:.1f} h ({sd['level']})",
            ("The last two nights were short. " if two_short else "") + "An earlier bedtime tonight helps most; see your sleep plan.", "sleep")

    # Lou on your workload: overdue work piling up, or simply too much
    try:
        from . import workload
        lw = workload.check(con, d, settings)
        if lw["level"] != "ok":
            add("lou:load", "ahead", 92 if lw["level"] == "too_much" else 75,
                "Lou: " + ("too much on your plate" if lw["level"] == "too_much" else "let's sort your to-dos"), lw["say"], "today")
    except Exception:
        pass

    # your goals: a mid-term check or a deadline that has arrived
    from . import lifegoals
    for g in lifegoals.due_checks(con, d):
        add(f"goal:{g['id']}:{g['kind']}", "ahead", 95, f"{'Mid-term check' if g['kind'] == 'mid' else 'Final check'}: {g['what']}",
            f"How's it going? {g['check']}", "today")

    # new workouts from the watch since yesterday
    for name, at, mins, kcal in _q(con, "SELECT name, at, minutes, kcal FROM workouts WHERE origin='garmin' AND substr(at,1,10) IN (?,?) ORDER BY at DESC LIMIT 2", (y, ds)):
        add(f"workout:{at}", "missed", 40, f"Your watch logged {name}",
            f"{at[5:10].replace('-', '/')} at {at[11:16]} · {round(mins or 0)} min{f' · {round(kcal)} kcal' if kcal else ''}", "log")

    # calendar: what's next today, or tomorrow's start after 6pm
    target = d + timedelta(days=1) if now.hour >= 18 and d == now.date() else d
    evs = _q(con, "SELECT start, title FROM calendar_events e WHERE all_day=0 AND substr(start,1,10)=? AND start >= ? "
                  "AND NOT EXISTS (SELECT 1 FROM calendar_hidden h WHERE h.uid=e.uid AND h.start=e.start) ORDER BY start",
             (target.isoformat(), now.isoformat(timespec="minutes") if target == now.date() else target.isoformat()))
    if evs:
        when = "Tomorrow" if target != d else "Still today"
        add(f"cal:{target}:{len(evs)}", "ahead", 70 if len(evs) >= 3 else 50, f"{when}: {len(evs)} event{'s' if len(evs) != 1 else ''}, first at {evs[0][0][11:16]}",
            evs[0][1] or "untitled", "today")

    # a connection that stopped working
    for src, at, msg in _q(con, "SELECT source, at, message FROM sync_status WHERE ok=0"):
        add(f"sync:{src}", "missed", 80, f"{src.replace('_', ' ').title()} stopped syncing", f"Since {at[5:16].replace('T', ' ')}. Check You → Connectors.", "you")
    # always-on: data that quietly stopped arriving (the Mac asleep, the watch not synced to the phone, the tracker off)
    if d == now.date():
        last_steps = _q(con, "SELECT MAX(date) FROM daily WHERE steps IS NOT NULL")
        if last_steps and last_steps[0][0] and last_steps[0][0] < (d - timedelta(days=1)).isoformat():
            add("stale:garmin", "missed", 85, "No watch data since " + last_steps[0][0][5:].replace("-", "/"),
                "Open Garmin Connect on your phone so the watch syncs, and keep this Mac awake so loop can pull it.", "you")
        last_act = _q(con, "SELECT MAX(end) FROM activity")
        if last_act and last_act[0][0] and now.hour >= 10 and last_act[0][0][:10] < d.isoformat():
            add("stale:mac", "missed", 70, "No screen time tracked today yet",
                "The tracker may be paused or the Mac was asleep. You → Connectors shows whether Mac activity is on.", "you")

    # mood trend (your own 1-5 ratings)
    r = _q(con, "SELECT date, rating FROM day_ratings WHERE date > ? AND date <= ?", ((d - timedelta(days=14)).isoformat(), ds))
    this = [x[1] for x in r if x[0] > (d - timedelta(days=7)).isoformat()]
    prev = [x[1] for x in r if x[0] <= (d - timedelta(days=7)).isoformat()]
    if len(this) >= 3 and len(prev) >= 3:
        a, b = sum(this) / len(this), sum(prev) / len(prev)
        if a <= b - 0.6:
            add("mood_down", "ahead", 75, "Your days have felt harder this week", f"Average {a:.1f}/5, down from {b:.1f}. Worth a look at sleep and what changed.", "consume")
        elif a >= b + 0.6:
            add("mood_up", "missed", 30, "This week has felt better", f"Average {a:.1f}/5, up from {b:.1f}.", "consume")

    # goals: a streak at risk this evening, and the one most behind this week
    try:
        gs = history.goals_overview(con, d, settings)
    except Exception:
        gs = []
    if d == now.date() and now.hour >= 17:
        for g in gs:
            if (g["streak"] or 0) >= 3 and g["target"] and g["today"] is not None and (g["pct"] or 0) < 100 and g["kind"] != "max":
                add(f"streak:{g['metric']}", "ahead", 85, f"{g['streak']}-day {g['label'].lower()} streak at risk", f"{g['pct']}% of your goal so far today.", g["tab"])
                break
    behind = [g for g in gs if g["week_logged"] >= 4 and g["week_met"] <= g["week_logged"] // 3]
    if behind:
        g = behind[0]
        add(f"behind:{g['metric']}", "missed", 45, f"{g['label']}: {g['week_met']} of {g['week_logged']} days this week", "The goal you've hit least lately.", g["tab"])

    # research saved lately
    z = _q(con, "SELECT title FROM zotero_items WHERE date_added >= ? ORDER BY date_added DESC", ((d - timedelta(days=2)).isoformat(),))
    if z:
        add(f"zotero:{ds}", "missed", 25, f"{len(z)} new item{'s' if len(z) != 1 else ''} in Zotero", z[0][0] or "", "consume")

    gone = {k for (k,) in _q(con, "SELECT key FROM catchup_dismissed WHERE date=?", (ds,))}
    gone |= {k for (k,) in _q(con, "SELECT key FROM catchup_snooze WHERE until > ?", (now.isoformat(timespec="minutes"),))}
    from . import tracking
    areas = tracking.on(settings)
    need = {"sleep_debt": "sleep", "workout": "movement", "mood_down": "mood", "mood_up": "mood", "zotero": "consume", "streak": None}
    out = [c for c in out if c["key"] not in gone and (need.get(c["key"].split(":")[0]) in (None,) or need.get(c["key"].split(":")[0]) in areas)]
    out.sort(key=lambda c: -c["prio"])
    return out[:5]
