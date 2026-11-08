"""
Google Calendar via its private iCal address: read-only, no Google Cloud project needed.

  Google Calendar (web) → Settings → your calendar → "Integrate calendar" →
  "Secret address in iCal format" → copy → paste in loop (You → Connectors).

The secret address works like a password for reading that calendar, so loop keeps it only in
your local database. Events from 7 days back to 30 days ahead are cached and refreshed every 15
minutes. Recurring events are expanded (daily / weekly incl. BYDAY / monthly / yearly, with
INTERVAL, COUNT, UNTIL, EXDATE and single-instance edits).
"""
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events(
  uid TEXT NOT NULL,
  start TEXT NOT NULL,           -- local ISO, or YYYY-MM-DD for all-day
  end TEXT,
  all_day INTEGER NOT NULL DEFAULT 0,
  title TEXT,
  location TEXT,
  description TEXT,
  PRIMARY KEY(uid, start)
);
CREATE TABLE IF NOT EXISTS calendar_hidden(uid TEXT NOT NULL, start TEXT NOT NULL, PRIMARY KEY(uid, start));
"""
DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


class CalendarError(RuntimeError):
    pass


def ensure(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(calendar_events)")}
    if "description" not in cols:
        con.execute("ALTER TABLE calendar_events ADD COLUMN description TEXT")


def url(con) -> str | None:
    r = con.execute("SELECT value FROM settings WHERE key='gcal_ics_url'").fetchone()
    from .. import keychain
    return keychain.resolve("gcal_ics_url", r[0] if r else None)


def is_connected(con) -> bool:
    return bool(url(con))


def fetch(ics_url: str) -> str:
    if not ics_url.startswith("https://"):
        raise CalendarError("Use the https:// secret iCal address from Google Calendar settings.")
    req = urllib.request.Request(ics_url, headers={"User-Agent": "loop-personal/0.1"})
    from .. import egress
    egress.record("gcal", ics_url, "fetch your calendar (nothing else sent)")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        raise CalendarError(f"Couldn't fetch the calendar ({e}). Check the address.") from e


# ------------------------------------------------------------------ parsing

def _unfold(text: str) -> list[str]:
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _prop(line: str):
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _unescape_text(v: str) -> str:
    """Like _unescape, but keeps line breaks (for descriptions) and drops simple HTML Google adds."""
    v = v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    v = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", v)
    v = re.sub(r'(?i)<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', lambda m: m.group(1) if m.group(2) in m.group(1) else f"{m.group(2)} ({m.group(1)})", v)
    v = re.sub(r"<[^>]+>", "", v)
    v = v.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", v).strip()


def _dt(value: str, params: dict):
    """→ (naive local datetime or date, is_all_day)."""
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").date(), True
    v = value.rstrip("Z")
    d = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        d = d.replace(tzinfo=timezone.utc)
    elif params.get("TZID"):
        try:
            d = d.replace(tzinfo=ZoneInfo(params["TZID"]))
        except Exception:
            pass
    if d.tzinfo:
        d = d.astimezone().replace(tzinfo=None)
    return d, False


def _duration(v: str) -> timedelta:
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v)
    if not m:
        return timedelta(hours=1)
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse(text: str) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None:
            name, params, value = _prop(line)
            if name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
                cur[name], cur["all_day" if name == "DTSTART" else "_"] = _dt(value, params)
            elif name == "EXDATE":
                for v in value.split(","):
                    cur["exdates"].add(_dt(v, params)[0])
            elif name == "DESCRIPTION":
                cur[name] = _unescape_text(value)
            elif name in ("SUMMARY", "LOCATION", "UID", "RRULE", "STATUS", "DURATION"):
                cur[name] = _unescape(value) if name in ("SUMMARY", "LOCATION") else value
    return events


def _occurrences(ev: dict, lo: datetime, hi: datetime):
    start = ev["DTSTART"]
    all_day = ev.get("all_day", False)
    as_dt = (lambda d: datetime.combine(d, datetime.min.time())) if all_day else (lambda d: d)
    length = (as_dt(ev["DTEND"]) - as_dt(start)) if ev.get("DTEND") else (_duration(ev["DURATION"]) if ev.get("DURATION")
                                                                         else timedelta(days=1 if all_day else 0, hours=0 if all_day else 1))
    rule = dict(p.split("=", 1) for p in ev.get("RRULE", "").split(";") if "=" in p)
    if not rule:
        s = as_dt(start)
        if s < hi and s + length > lo:
            yield start, length
        return
    freq, interval = rule.get("FREQ"), int(rule.get("INTERVAL", 1))
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = _dt(rule["UNTIL"], {})[0] if "UNTIL" in rule else None
    if isinstance(until, date) and not isinstance(until, datetime):
        # date-only UNTIL (common on timed events too): inclusive through the end of that day
        until = datetime.combine(until, datetime.max.time())
    bydays = [DAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    n, cur, emitted = 0, as_dt(start), 0
    while n < 2000:
        n += 1
        candidates = [cur]
        if freq == "WEEKLY" and bydays:
            week_start = cur - timedelta(days=cur.weekday())
            candidates = [week_start + timedelta(days=d) for d in sorted(bydays)]
            candidates = [c.replace(hour=cur.hour, minute=cur.minute, second=cur.second) for c in candidates]
        for c in candidates:
            if c < as_dt(start):
                continue
            if (until and c > until) or (count is not None and emitted >= count) or c >= hi:
                return
            emitted += 1
            key = c.date() if all_day else c
            if key in ev["exdates"] or c in ev["exdates"]:
                continue
            if c + length > lo:
                yield (c.date() if all_day else c), length
        if freq == "DAILY":
            cur += timedelta(days=interval)
        elif freq == "WEEKLY":
            cur += timedelta(weeks=interval)
        elif freq == "MONTHLY":
            m = cur.month - 1 + interval
            try:
                cur = cur.replace(year=cur.year + m // 12, month=m % 12 + 1)
            except ValueError:
                cur = cur.replace(year=cur.year + m // 12, month=m % 12 + 1, day=28)
        elif freq == "YEARLY":
            cur = cur.replace(year=cur.year + interval)
        else:
            return


def expand(events: list[dict], lo: datetime, hi: datetime) -> list[dict]:
    overrides = {(e.get("UID"), e["RECURRENCE-ID"]) for e in events if e.get("RECURRENCE-ID")}
    out = []
    for ev in events:
        if "DTSTART" not in ev or ev.get("STATUS") == "CANCELLED":
            continue
        is_override = bool(ev.get("RECURRENCE-ID"))
        for start, length in _occurrences(ev, lo, hi):
            if not is_override and (ev.get("UID"), start) in overrides:
                continue  # this instance was edited; the override event carries it
            all_day = ev.get("all_day", False)
            end = start + length
            out.append({"uid": ev.get("UID") or ev.get("SUMMARY", "?"),
                        "start": start.isoformat() if all_day else start.isoformat(timespec="minutes"),
                        "end": end.isoformat() if all_day else end.isoformat(timespec="minutes"),
                        "all_day": int(all_day), "title": ev.get("SUMMARY") or "(busy)", "location": ev.get("LOCATION"),
                        "description": ev.get("DESCRIPTION")})
    return out


def sync(con) -> dict:
    u = url(con)
    if not u:
        raise CalendarError("Google Calendar not connected. Paste its secret iCal address in You → Connectors.")
    now = datetime.now()
    lo, hi = now - timedelta(days=7), now + timedelta(days=30)
    evs = expand(parse(fetch(u)), lo, hi)
    con.execute("DELETE FROM calendar_events")
    con.executemany("INSERT OR REPLACE INTO calendar_events(uid, start, end, all_day, title, location, description) VALUES(?,?,?,?,?,?,?)",
                    [(e["uid"], e["start"], e["end"], e["all_day"], e["title"], e["location"], e.get("description")) for e in evs])
    con.commit()
    return {"events": len(evs)}


def day(con, d: date) -> list[dict]:
    ds, nxt = d.isoformat(), (d + timedelta(days=1)).isoformat()
    return [dict(r) for r in con.execute(
        "SELECT * FROM calendar_events e WHERE ((all_day=1 AND start <= ? AND end > ?) OR (all_day=0 AND start < ? AND end > ?)) "
        "AND NOT EXISTS (SELECT 1 FROM calendar_hidden h WHERE h.uid = e.uid AND h.start = e.start) "
        "ORDER BY all_day DESC, start", (ds, ds, nxt, ds))]


def hide(con, uid: str, start: str, hidden: bool = True):
    """Hide one occurrence from loop (your Google Calendar is untouched)."""
    if hidden:
        con.execute("INSERT OR IGNORE INTO calendar_hidden VALUES(?,?)", (uid, start))
    else:
        con.execute("DELETE FROM calendar_hidden WHERE uid=? AND start=?", (uid, start))


def flags(events: list[dict], en: dict) -> list[dict]:
    """Mark events that land in the afternoon dip, after the caffeine cutoff, or too close to your sleep."""
    zones = {z["key"]: z for z in en.get("zones", [])}
    out = []
    for e in events:
        e = dict(e)
        notes = []
        if not e["all_day"]:
            s = e["start"]
            dip = zones.get("dip")
            if dip and dip["start_iso"] <= s < dip["end_iso"]:
                notes.append("in your afternoon dip")
            peak = zones.get("peak")
            if peak and peak["start_iso"] <= s < peak["end_iso"]:
                notes.append("in your focus peak")
            wind = zones.get("winddown")
            if wind and s >= wind["start_iso"]:
                notes.append("during wind-down")
            if en.get("wake_anchor_iso") and s < (datetime.fromisoformat(en["wake_anchor_iso"]) + timedelta(minutes=60)).isoformat() \
                    and s[:10] == en["wake_anchor_iso"][:10]:
                notes.append("within an hour of your wake time")
        e["notes"] = notes
        out.append(e)
    return out
