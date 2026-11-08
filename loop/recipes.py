"""
Recipes: foods you eat together, saved once and logged in one tap.

loop notices a combination you keep having in the same meal (3+ times in the last 6 weeks) and offers to save it.
When you log a recipe you can change amounts or drop an item just for this time, or keep the new amounts from now on.
Everything stays in loop's database on this Mac.
"""
import json
from collections import Counter
from datetime import date, datetime, timedelta
from itertools import combinations
from statistics import median

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, meal TEXT, created_at TEXT, uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS recipe_items(recipe_id INTEGER NOT NULL, food_id INTEGER NOT NULL, amount REAL NOT NULL,
  PRIMARY KEY(recipe_id, food_id));
CREATE TABLE IF NOT EXISTS recipe_dismissed(sig TEXT PRIMARY KEY);
"""
SLOT = {"dessert": "snack"}


def ensure(con):
    con.executescript(SCHEMA)


def _amount(row) -> float:
    return row["qty"] if row["basis"] == "serving" else row["grams"]


def _sig(ids) -> str:
    return ",".join(str(i) for i in sorted(ids))


def suggestions(con, today: date, days: int = 42, min_times: int = 3) -> list[dict]:
    """Combinations of 2–6 foods you've had together in one meal at least `min_times` times."""
    ensure(con)
    rows = con.execute(
        """SELECT substr(l.at,1,10) AS d, l.meal, l.food_id, l.grams, l.qty, f.basis, f.name FROM food_log l JOIN foods f ON f.id=l.food_id
           WHERE l.at >= ? AND l.at < ?""", ((today - timedelta(days=days)).isoformat(), (today + timedelta(days=1)).isoformat())).fetchall()
    meals: dict[tuple, dict] = {}
    names, amounts = {}, {}
    for r in rows:
        meals.setdefault((r["d"], SLOT.get(r["meal"], r["meal"])), {})[r["food_id"]] = r
        names[r["food_id"]] = r["name"]
        amounts.setdefault(r["food_id"], []).append(_amount(r) or 0)
    counts, where = Counter(), {}
    for (d, meal), items in meals.items():
        ids = sorted(items)[:10]
        for k in range(2, min(6, len(ids)) + 1):
            for combo in combinations(ids, k):
                counts[combo] += 1
                where.setdefault(combo, Counter())[meal] += 1
    have = {_sig(r[0].split(",")) for r in con.execute(
        "SELECT group_concat(food_id) FROM recipe_items GROUP BY recipe_id") if r[0]}
    gone = {r[0] for r in con.execute("SELECT sig FROM recipe_dismissed")}
    frequent = {c: n for c, n in counts.items() if n >= min_times}
    # keep only the largest combinations (drop a pair if a bigger set containing it is just as frequent)
    keep = [c for c, n in frequent.items()
            if not any(set(c) < set(o) and frequent[o] >= n for o in frequent)]
    keep.sort(key=lambda c: (-frequent[c], -len(c)))
    out = []
    for c in keep:
        s = _sig(c)
        if s in have or s in gone:
            continue
        meal = where[c].most_common(1)[0][0]
        out.append({"sig": s, "times": frequent[c], "meal": meal,
                    "items": [{"food_id": i, "name": names[i], "amount": round(median(amounts[i]), 2)} for i in c]})
    return out[:3]


def _items(con, rid: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT ri.food_id, ri.amount, f.name, f.basis, f.serving_label, f.serving_g, f.kcal, f.protein, f.carbs, f.fat, f.fiber, f.grp
           FROM recipe_items ri JOIN foods f ON f.id=ri.food_id WHERE ri.recipe_id=? ORDER BY f.name""", (rid,))]


def all_recipes(con) -> list[dict]:
    ensure(con)
    return [{"id": r["id"], "name": r["name"], "meal": r["meal"], "uses": r["uses"], "items": _items(con, r["id"])}
            for r in con.execute("SELECT * FROM recipes ORDER BY uses DESC, name")]


def save(con, name: str, items: list[dict], meal: str | None = None, rid: int | None = None) -> int:
    ensure(con)
    items = [i for i in items if i.get("food_id") and float(i.get("amount") or 0) > 0]
    if len(items) < 1:
        raise ValueError("a recipe needs at least one food")
    if rid:
        con.execute("UPDATE recipes SET name=COALESCE(?, name), meal=COALESCE(?, meal) WHERE id=?", (name or None, meal, rid))
        con.execute("DELETE FROM recipe_items WHERE recipe_id=?", (rid,))
    else:
        rid = con.execute("INSERT INTO recipes(name, meal, created_at) VALUES(?,?,?)",
                          ((name or "My recipe").strip()[:60], meal, datetime.now().isoformat(timespec="seconds"))).lastrowid
    for i in items:
        con.execute("INSERT OR REPLACE INTO recipe_items(recipe_id, food_id, amount) VALUES(?,?,?)", (rid, int(i["food_id"]), float(i["amount"])))
    con.commit()
    return rid


def log(con, rid: int, at: str, meal: str, items: list[dict] | None = None, keep: bool = False) -> int:
    """Log every food in the recipe. `items` (food_id, amount) overrides amounts or leaves foods out for this time;
    with keep=True those amounts become the recipe's new amounts."""
    from . import food
    ensure(con)
    base = {i["food_id"]: i for i in _items(con, rid)}
    use = items if items is not None else [{"food_id": k, "amount": v["amount"]} for k, v in base.items()]
    t = food.meal_time(con, at[:10], meal) or at
    n = 0
    for i in use:
        fid, amt = int(i["food_id"]), float(i.get("amount") or 0)
        if amt <= 0 or fid not in base:
            continue
        serving = base[fid]["basis"] == "serving"
        food.log(con, fid, grams=None if serving else amt, qty=amt if serving else None, at=t, meal=meal)
        n += 1
    con.execute("UPDATE recipes SET uses = uses + 1 WHERE id=?", (rid,))
    if keep and items is not None:
        save(con, None, [i for i in items if float(i.get("amount") or 0) > 0], rid=rid)
    con.commit()
    return n


def dismiss(con, sig: str):
    ensure(con)
    con.execute("INSERT OR IGNORE INTO recipe_dismissed(sig) VALUES(?)", (sig,))
    con.commit()


def delete(con, rid: int):
    ensure(con)
    con.execute("DELETE FROM recipe_items WHERE recipe_id=?", (rid,))
    con.execute("DELETE FROM recipes WHERE id=?", (rid,))
    con.commit()
