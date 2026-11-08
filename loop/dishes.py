"""Name a meal from its ingredients, on this Mac, with simple rules (no AI: your food log never leaves the Mac).

  oats + egg (+ banana)              -> oat pancakes          (+ yogurt -> "Yogurt with oat pancakes")
  rice + chicken + bok choy/carrots  -> "Rice with chicken and veg"
  pasta + vodka sauce + beef         -> "Vodka pasta with beef and veg"
  bagel + cream cheese               -> "Bagel with cream cheese"
  one food                           -> that food, tidied ("Festival mooncake")

Oils, butter, honey, spices, sauces and nut butters count as toppings and are left out of the name.
You can rename any meal; loop remembers the name for that set of foods and uses it the next time they show up.
"""
import re
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS meal_names(date TEXT NOT NULL, meal TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(date, meal));
CREATE TABLE IF NOT EXISTS meal_name_memory(sig TEXT PRIMARY KEY, name TEXT NOT NULL, used_at TEXT);
"""

# (pattern, role, word used in the name)
RULES = [
    (r"\boats?\b|rolled oats|oatmeal|granola", "oats", "oats"),
    (r"basmati|\brice\b", "starch", "rice"),
    (r"penne|fusilli|spaghetti|pasta(?! sauce)|rigate|macaroni|linguine", "starch", "pasta"),
    (r"noodle|ramen|udon|soba", "starch", "noodles"),
    (r"bagel", "bread", "bagel"), (r"croissant", "bread", "croissant"), (r"toast|bread|sourdough", "bread", "toast"),
    (r"tortilla|wrap\b", "starch", "wrap"), (r"potato", "starch", "potatoes"), (r"quinoa", "starch", "quinoa"),
    (r"vodka.*sauce", "sauce", "vodka"), (r"marinara|tomato sauce|pesto", "sauce", "tomato"),
    (r"chicken", "protein", "chicken"), (r"steak|ground beef|\bbeef\b", "protein", "beef"), (r"pork|bacon", "protein", "pork"),
    (r"salmon", "protein", "salmon"), (r"tuna|cod|tilapia|\bfish\b", "protein", "fish"), (r"shrimp|prawn", "protein", "shrimp"),
    (r"tofu", "protein", "tofu"), (r"turkey", "protein", "turkey"), (r"\beggs?\b", "egg", "egg"),
    (r"yogurt|yoghurt|skyr", "yogurt", "yogurt"),
    (r"spinach|bok choy|cabbage|carrot|broccoli|eggplant|cucumber|kale|lettuce|salad|zucchini|pepper(?!oni)|corn\b|peas\b|green bean|asparagus|mushroom|cauliflower|tomato(?! sauce)",
     "veg", "veg"),
    (r"banana|kiwi|watermelon|berr|apple|mango|orange|grape|peach|pear\b|melon", "fruit", "fruit"),
    (r"cream cheese", "spread", "cream cheese"), (r"american cheese|cheddar|mozzarella|parmesan|cheese(?! dressing)", "cheese", "cheese"),
    (r"cookie|cupcake|cake|danish|brownie|donut|doughnut|ice cream|mooncake|pastry|muffin", "sweet", None),
    (r"olive oil|\boil\b|butter(?! *milk)|honey|cinnamon|cocoa|salt|sugar|soy sauce|sriracha|kewpie|mayo|dressing|ginger sauce|"
     r"vinegar|ketchup|mustard|sauce|scallion|garlic|chocolate|syrup|jam|seasoning|spice|green tea|\btea\b|coffee", "topping", None),
]
BRAND = re.compile(r"\b(by .*$|organic|365|whole ?foods( market)?|wholefoods|trader joe'?s|trader|heb|fage usa|"
                   r"organic valley|bee harmony|just|the no cookie|\d+% ?(milkfat)?)\b", re.I)


def tidy(name: str) -> str:
    s = BRAND.sub("", name or "")
    s = re.sub(r"\s+", " ", s).strip(" ,-")
    return (s[:1].upper() + s[1:].lower()) if s else (name or "")


def classify(name: str) -> tuple[str, str | None]:
    n = (name or "").lower()
    if re.search(r"peanut butter|almond butter|nut butter", n):
        return ("topping", None)
    for pat, role, word in RULES:
        if re.search(pat, n):
            return (role, word)
    return ("other", None)


def _and(words: list[str]) -> str:
    w = list(dict.fromkeys(x for x in words if x))
    return w[0] if len(w) == 1 else ", ".join(w[:-1]) + " and " + w[-1] if w else ""


def guess(names: list[str], kcals: list[float] | None = None) -> str:
    """A short, plausible dish name for one meal."""
    if not names:
        return ""
    if len(names) == 1:
        return tidy(names[0])
    kc = kcals or [0] * len(names)
    roles: dict[str, list[str]] = {}
    for n in names:
        role, word = classify(n)
        roles.setdefault(role, []).append(word or tidy(n).lower())
    has = lambda r: r in roles
    parts = []
    if has("oats"):
        if has("egg"):          # oats + egg (+ banana) is pancake batter
            parts.append("yogurt with oat pancakes" if has("yogurt") else "oat pancakes")
        else:
            withs = (["yogurt"] if has("yogurt") else []) + (["fruit"] if has("fruit") else [])
            parts.append(f"oatmeal with {_and(withs)}" if withs else "oatmeal")
    elif has("bread"):
        b = roles["bread"][0]
        withs = (["cream cheese"] if has("spread") else []) + (["egg"] if has("egg") else []) + (roles.get("protein") or [])[:1]
        parts.append(f"{b} with {_and(withs)}" if withs else b)
    elif has("starch"):
        st = roles["starch"][0]
        if has("sauce"):
            st = f"{roles['sauce'][0]} {st}"
        withs = (roles.get("protein") or [])[:2]
        if has("egg") and len(withs) < 2:
            withs.append("egg")
        if has("veg"):
            withs.append("veg")
        parts.append(f"{st} with {_and(withs)}" if withs else st)
    elif has("protein") or has("egg"):
        main = (roles.get("protein") or []) + (["eggs"] if has("egg") else [])
        sides = (["veg"] if has("veg") else []) + (["fruit"] if has("fruit") else [])
        parts.append(_and(main[:2]) + (f" with {_and(sides)}" if sides else ""))
    if has("sweet") or not parts:
        # sweets, or nothing matched: the two or three biggest items by calories
        others = sorted([(k or 0, n) for n, k in zip(names, kc) if classify(n)[0] in ("sweet", "other", "cheese", "fruit")], reverse=True)
        extra = [tidy(n).lower() for _, n in others[:3]]
        if not parts and not extra:
            extra = [tidy(n).lower() for _, n in sorted(zip(kc, names), reverse=True)[:2]]
        if extra:
            parts.append(_and(extra))
    s = "; ".join(p for p in parts if p)
    return s[:1].upper() + s[1:]


def _sig(food_ids: list[int]) -> str:
    return ",".join(str(i) for i in sorted(set(food_ids)))


def ensure(con):
    con.executescript(SCHEMA)


def _remembered(con, ids: list[int]) -> str | None:
    """Your own name for this set of foods, or for a set that's mostly the same (70% overlap)."""
    sig = _sig(ids)
    r = con.execute("SELECT name FROM meal_name_memory WHERE sig=?", (sig,)).fetchone()
    if r:
        return r[0]
    mine = set(ids)
    best, score = None, 0.0
    for s, name in con.execute("SELECT sig, name FROM meal_name_memory"):
        other = {int(x) for x in s.split(",") if x}
        j = len(mine & other) / len(mine | other) if mine | other else 0
        if j > score:
            best, score = name, j
    return best if score >= 0.7 else None


def names_for(con, d: date, entries: list[dict]) -> dict[str, dict]:
    """{meal: {"name", "source": "you" | "remembered" | "guess"}} for one day's entries."""
    ensure(con)
    meals: dict[str, list[dict]] = {}
    for e in entries:
        meals.setdefault(e["meal"] or "snack", []).append(e)
    saved = dict(con.execute("SELECT meal, name FROM meal_names WHERE date=?", (d.isoformat(),)).fetchall())
    out = {}
    for m, es in meals.items():
        if m in saved:
            out[m] = {"name": saved[m], "source": "you"}
            continue
        r = _remembered(con, [e["food_id"] for e in es])
        if r:
            out[m] = {"name": r, "source": "remembered"}
        else:
            out[m] = {"name": guess([e["name"] for e in es], [e["macros"]["kcal"] for e in es]), "source": "guess"}
    return out


def rename(con, d: date, meal: str, name: str, food_ids: list[int]) -> None:
    """Save your name for this meal, and remember it for this set of foods. An empty name goes back to the guess."""
    ensure(con)
    name = (name or "").strip()[:80]
    if not name:
        con.execute("DELETE FROM meal_names WHERE date=? AND meal=?", (d.isoformat(), meal))
        return
    con.execute("INSERT OR REPLACE INTO meal_names(date, meal, name) VALUES(?,?,?)", (d.isoformat(), meal, name))
    if food_ids:
        con.execute("INSERT OR REPLACE INTO meal_name_memory(sig, name, used_at) VALUES(?,?,?)", (_sig(food_ids), name, d.isoformat()))
