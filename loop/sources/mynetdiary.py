"""
Import a MyNetDiary export (the FoodTracker_YYYY.xls files).

  python3 -m loop import-mynetdiary ~/Downloads/FoodTracker_2025.xls ~/Downloads/FoodTracker_2026.xls

.xls files are converted to CSV with Apple Numbers (locally) first; a folder of already-exported
CSVs works too. Re-importing is safe: entries already imported are skipped.

What comes in:
  Food            every entry with its logged nutrients. Blank cells stay blank (never counted
                  as zero). Amounts in g/oz become per-100 g foods; household amounts ("1 cup",
                  "1 medium") become per-serving foods so they can be re-logged.
  Exercise        workouts (name, minutes, calories)
  Water Glasses   daily water (fl oz)
  Measurements    body weight (lbs) and height
  Measurements    "Daily Steps Count" → steps (only on days Garmin has none)
  Notes           "930,1230,545"-style notes → breakfast/lunch/dinner times; any other text → journal.
                  Single numbers (e.g. "-700") are ignored.
Not imported: "Hours of Sleep" (sleep comes from Garmin), Food Grade scores.
"""
import csv
import re
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .. import food as F
from .. import nutrients as N

MEALS = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snacks": "snack", "snack": "snack"}
GRAMS_PER = {"g": 1, "gram": 1, "grams": 1, "oz": 28.3495, "kg": 1000, "lb": 453.592, "lbs": 453.592}
AMOUNT = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)\s*(.*)$")


def convert_with_numbers(xls: Path) -> Path:
    out = Path(tempfile.mkdtemp(prefix="loop-mnd-")) / xls.stem
    script = f'''
with timeout of 300 seconds
tell application "Numbers"
  set d to open POSIX file "{xls}"
  export d to POSIX file "{out}" as CSV
  close d saving no
end tell
end timeout'''
    # AppleScript's own `launch` can fail with -600 ("isn't running"); `open -a` reliably starts it.
    subprocess.run(["open", "-g", "-a", "Numbers"], capture_output=True)
    for _ in range(20):
        if subprocess.run(["osascript", "-e", 'tell application "Numbers" to get name'],
                          capture_output=True).returncode == 0:
            break
        time.sleep(1)
    for attempt in range(3):
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode == 0 and out.exists():
            break
        time.sleep(3)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"Numbers couldn't convert {xls.name}: {r.stderr.strip()}")
    return out


def _sheet(folder: Path, name: str) -> list[dict]:
    hits = sorted(folder.glob(f"{name}*.csv"))
    if not hits:
        return []
    with open(hits[0], newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _dt(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%m %d %Y %I:%M %p")


def _date(s: str) -> str:
    return datetime.strptime(s.strip(), "%m %d %Y").date().isoformat()


def parse_amount(text: str) -> tuple[float | None, str]:
    """'1 1/2 cups' -> (1.5, 'cups'); '20g' -> (20, 'g'); 'serving' -> (1, 'serving')."""
    m = AMOUNT.match(text or "")
    if not m:
        return (1.0, (text or "serving").strip()) if text else (None, "")
    num, unit = m.group(1), m.group(2).strip()
    if " " in num:
        whole, frac = num.split()
        a, b = frac.split("/")
        q = int(whole) + int(a) / int(b)
    elif "/" in num:
        a, b = num.split("/")
        q = int(a) / int(b)
    else:
        q = float(num)
    return q, unit or "serving"


def _num(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def tidy_name(name: str) -> str:
    """MyNetDiary's export sometimes cuts a food name at a comma, leaving half a bracket:
    '(kewpie', 'Rye (liquor', 'Beef) rice stir fry'. Close or drop the stray bracket and capitalize."""
    s = " ".join((name or "").split())
    out, depth = [], 0
    for ch in s:
        if ch == ")":
            if depth == 0:
                continue                       # a ')' with no '(' before it
            depth -= 1
        elif ch == "(":
            depth += 1
        out.append(ch)
    s = "".join(out) + ")" * depth
    if s.startswith("(") and s.endswith(")") and s.count("(") == 1:
        s = s[1:-1]                            # the whole name was the bracket: '(kewpie)' -> 'kewpie'
    s = re.sub(r"\(\s*\)", "", s).strip(" ,;")
    return s[:1].upper() + s[1:] if s else s


def repair_names(con) -> int:
    """Fix names already imported with a stray bracket. Returns how many changed."""
    n = 0
    for fid, name in con.execute("SELECT id, name FROM foods WHERE key LIKE 'mnd:%'").fetchall():
        t = tidy_name(name)
        if t and t != name:
            con.execute("UPDATE foods SET name=? WHERE id=?", (t, fid))
            n += 1
    return n


def import_food(con, rows: list[dict]) -> dict:
    seen = Counter()
    added = skipped = 0
    for r in rows:
        name = tidy_name(r.get("Name") or "")
        if not name or not r.get("Date & Time"):
            continue
        at = _dt(r["Date & Time"])
        totals = {key: _num(r.get(col)) for col, key in N.MND_COLUMN.items()}
        totals = {k: v for k, v in totals.items() if v is not None}
        qty, unit = parse_amount(r.get("Amount", ""))
        grams = qty * GRAMS_PER[unit.lower()] if qty and unit.lower() in GRAMS_PER else None
        if grams:
            key, basis, factor = f"mnd:{r.get('Food ID')}:g", "g100", grams / 100
            serving_label, serving_g = r.get("Amount"), grams
        else:
            u = unit.lower()
            key, basis, factor = f"mnd:{r.get('Food ID')}:{u}", "serving", (qty or 1)
            serving_label, serving_g = f"1 {u}" if u not in ("serving",) else "1 serving", None
        per_unit = {k: v / factor for k, v in totals.items()} if factor else totals
        fid = F.upsert_food(con, {
            "key": key, "name": name, "brand": None, "grp": F.guess_group(name),
            "kcal": per_unit.get("kcal"), "protein": per_unit.get("protein"), "carbs": per_unit.get("carbs"),
            "fat": per_unit.get("fat"), "fiber": per_unit.get("fiber"),
            "serving_g": serving_g, "serving_label": serving_label, "basis": basis, "nutrients": per_unit,
        })
        ident = (r.get("Food ID"), r["Date & Time"], r.get("Amount"))
        seen[ident] += 1
        new = F.log(con, fid, grams=grams, qty=None if grams else (qty or 1), at=at.isoformat(timespec="minutes"),
                    meal=MEALS.get((r.get("Meal") or "").strip().lower()), amount_text=r.get("Amount"),
                    totals=totals, ext_key=f"mnd:{ident[0]}:{ident[1]}:{ident[2]}:{seen[ident]}", origin="mynetdiary")
        if new:
            added += 1
        else:
            skipped += 1
    return {"food_entries": added, "already_imported": skipped}


def import_exercise(con, rows: list[dict]) -> int:
    n, seen = 0, Counter()
    for r in rows:
        if not r.get("Date & Time"):
            continue
        ident = (r.get("Activity ID"), r["Date & Time"], r.get("Amount"))
        seen[ident] += 1
        at = _dt(r["Date & Time"]).isoformat(timespec="minutes")
        mins, _ = parse_amount(r.get("Amount", ""))
        cur = con.execute(
            "INSERT OR IGNORE INTO workouts(at, name, minutes, kcal, origin, ext_key) VALUES(?,?,?,?,?,?)",
            (at, r.get("Name"), mins, _num(r.get("Calories")), "mynetdiary",
             f"mnd:{ident[0]}:{ident[1]}:{ident[2]}:{seen[ident]}"))
        n += cur.rowcount
    return n


def _measure(con, date, kind, value, unit):
    con.execute("INSERT OR REPLACE INTO measurements(date, kind, value, unit, origin) VALUES(?,?,?,?, 'mynetdiary')",
                (date, kind, value, unit))


def import_water(con, rows) -> int:
    n = 0
    for r in rows:
        v = _num(r.get("Water, fl oz"))
        if r.get("Date") and v is not None:
            _measure(con, _date(r["Date"]), "water", v, "fl oz")
            n += 1
    return n


def import_measurements(con, rows) -> int:
    kinds = {"Body Weight": ("weight", "lbs"), "Body Height": ("height", "in")}
    n = 0
    for r in rows:
        k = kinds.get((r.get("Measurement") or "").strip())
        v = _num(r.get("Value"))
        if k and v is not None and r.get("Date"):
            _measure(con, _date(r["Date"]), k[0], round(v, 1), k[1])
            n += 1
    return n


MEAL_SLOTS = ("breakfast", "lunch", "dinner")


def parse_meal_times(text: str) -> list[tuple[str, int, int]] | None:
    """'930,1230,545' -> [('breakfast', 9, 30), ('lunch', 12, 30), ('dinner', 17, 45)].
    Hours without am/pm are placed in the slot's half of the day: lunch 1 or 2 means 13 or 14,
    dinner 5–11 means 17–23."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        return None
    out = []
    for slot, part in zip(MEAL_SLOTS, parts):
        m = re.search(r"(\d{1,4})", part)
        if not m:
            return None
        digits = m.group(1)
        h, mi = (int(digits), 0) if len(digits) <= 2 else (int(digits[:-2]), int(digits[-2:]))
        if slot == "lunch" and h < 10:
            h += 12
        if slot == "dinner" and h < 12:
            h += 12
        if not (0 <= h < 24 and 0 <= mi < 60):
            return None
        out.append((slot, h, mi))
    return out


def import_notes(con, rows) -> dict:
    meals = journal = 0
    for r in rows:
        if not r.get("Date") or not r.get("Note"):
            continue
        d = _date(r["Date"])
        lines = [x.strip() for x in str(r["Note"]).splitlines() if x.strip()]
        text_lines = []
        for line in lines:
            times = parse_meal_times(line)
            if times:
                for slot, h, mi in times:
                    at = f"{d}T{h:02d}:{mi:02d}"
                    if not con.execute("SELECT 1 FROM events WHERE kind='meal' AND at=? AND note=?", (at, slot)).fetchone():
                        con.execute("INSERT INTO events(kind, at, note) VALUES('meal', ?, ?)", (at, slot))
                        meals += 1
            elif re.fullmatch(r"[-+]?[\d.,\s]+", line):
                continue  # bare numbers: meaning unknown, ignored
            else:
                text_lines.append(line)
        if text_lines:
            text = "\n".join(text_lines)
            cur = con.execute("SELECT text FROM journal WHERE date=?", (d,)).fetchone()
            if not cur:
                con.execute("INSERT INTO journal(date, text, updated_at) VALUES(?,?, 'mynetdiary')", (d, text))
                journal += 1
            elif text not in cur[0]:
                con.execute("UPDATE journal SET text=? WHERE date=?", (cur[0] + "\n" + text, d))
    return {"meal_times": meals, "journal_days": journal}


def import_steps(con, rows) -> int:
    n = 0
    for r in rows:
        if (r.get("Measurement") or "").strip() != "Daily Steps Count" or not r.get("Date"):
            continue
        v = _num(r.get("Value"))
        if v is None:
            continue
        d = _date(r["Date"])
        con.execute("INSERT OR IGNORE INTO daily(date, origin) VALUES(?, 'mynetdiary')", (d,))
        cur = con.execute("UPDATE daily SET steps=? WHERE date=? AND (steps IS NULL OR origin='mynetdiary')", (int(v), d))
        n += cur.rowcount
    return n


def import_paths(con, paths: list[str]) -> list[dict]:
    results = []
    for p in map(lambda x: Path(x).expanduser(), paths):
        folder = convert_with_numbers(p) if p.suffix.lower() in (".xls", ".xlsx", ".numbers") else p
        res = {"file": p.name}
        res.update(import_food(con, _sheet(folder, "Food")))
        res["workouts"] = import_exercise(con, _sheet(folder, "Exercise"))
        res["water_days"] = import_water(con, _sheet(folder, "Water"))
        res["measurements"] = import_measurements(con, _sheet(folder, "Measurements"))
        res["steps_days"] = import_steps(con, _sheet(folder, "Measurements"))
        res.update(import_notes(con, _sheet(folder, "Notes")))
        con.commit()
        results.append(res)
    return results
