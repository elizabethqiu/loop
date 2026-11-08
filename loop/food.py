"""
Food logging: food groups + macros, built for re-logging the same things fast.

Where foods come from:
  1. Your own foods table: favorites, recents, custom entries, and anything you've logged
     before (cached, so it works offline).
  2. A built-in starter list of everyday whole foods (USDA values, per 100 g).
  3. USDA FoodData Central search: generic foods plus ~400k branded labels, including
     Whole Foods Market / 365. Free key: https://api.data.gov/signup (DEMO_KEY works
     until it hits its hourly limit).
  4. Barcode: USDA by UPC, then Open Food Facts.

All nutrition is stored per 100 g; servings are just a default gram amount.
Searches send your query text to USDA / Open Food Facts. Your log never leaves the Mac.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from . import nutrients as N

GROUPS = ["fruit", "veg", "protein", "grain", "fat", "dairy", "other", "carb"]   # "carb" = any carb, unsorted
GROUP_LABELS = {"fruit": "Fruit", "veg": "Veg", "protein": "Protein", "grain": "Grain",
                "fat": "Fats", "dairy": "Dairy", "other": "Other", "carb": "Carb"}
UA = "loop-personal/0.1 (local food log)"


class FoodError(RuntimeError):
    pass


# name, group, kcal, protein, carbs, fat, fiber (per 100 g), default serving g, serving label
STARTER = [
    ("Apple", "fruit", 52, 0.3, 13.8, 0.2, 2.4, 182, "1 medium"),
    ("Banana", "fruit", 89, 1.1, 22.8, 0.3, 2.6, 118, "1 medium"),
    ("Blueberries", "fruit", 57, 0.7, 14.5, 0.3, 2.4, 148, "1 cup"),
    ("Strawberries", "fruit", 32, 0.7, 7.7, 0.3, 2.0, 152, "1 cup"),
    ("Raspberries", "fruit", 52, 1.2, 11.9, 0.7, 6.5, 123, "1 cup"),
    ("Orange", "fruit", 47, 0.9, 11.8, 0.1, 2.4, 131, "1 medium"),
    ("Grapes", "fruit", 69, 0.7, 18.1, 0.2, 0.9, 151, "1 cup"),
    ("Mango", "fruit", 60, 0.8, 15.0, 0.4, 1.6, 165, "1 cup"),
    ("Pear", "fruit", 57, 0.4, 15.2, 0.1, 3.1, 178, "1 medium"),
    ("Kiwi", "fruit", 61, 1.1, 14.7, 0.5, 3.0, 69, "1 fruit"),
    ("Pineapple", "fruit", 50, 0.5, 13.1, 0.1, 1.4, 165, "1 cup"),
    ("Medjool dates", "fruit", 277, 1.8, 75.0, 0.2, 6.7, 24, "1 date"),
    ("Broccoli", "veg", 34, 2.8, 6.6, 0.4, 2.6, 91, "1 cup"),
    ("Spinach", "veg", 23, 2.9, 3.6, 0.4, 2.2, 30, "1 cup raw"),
    ("Kale", "veg", 35, 2.9, 4.4, 1.5, 4.1, 21, "1 cup raw"),
    ("Mixed greens / romaine", "veg", 17, 1.2, 3.3, 0.3, 2.1, 47, "1 cup"),
    ("Carrot", "veg", 41, 0.9, 9.6, 0.2, 2.8, 61, "1 medium"),
    ("Red bell pepper", "veg", 31, 1.0, 6.0, 0.3, 2.1, 119, "1 medium"),
    ("Tomato", "veg", 18, 0.9, 3.9, 0.2, 1.2, 123, "1 medium"),
    ("Cucumber", "veg", 15, 0.7, 3.6, 0.1, 0.5, 104, "1 cup sliced"),
    ("Zucchini", "veg", 17, 1.2, 3.1, 0.3, 1.0, 124, "1 cup"),
    ("Cauliflower", "veg", 25, 1.9, 5.0, 0.3, 2.0, 107, "1 cup"),
    ("Brussels sprouts", "veg", 43, 3.4, 9.0, 0.3, 3.8, 88, "1 cup"),
    ("Green beans", "veg", 31, 1.8, 7.0, 0.2, 2.7, 100, "1 cup"),
    ("Mushrooms", "veg", 22, 3.1, 3.3, 0.3, 1.0, 70, "1 cup"),
    ("Onion", "veg", 40, 1.1, 9.3, 0.1, 1.7, 110, "1 medium"),
    ("Sweet potato, baked", "veg", 90, 2.0, 20.7, 0.2, 3.3, 114, "1 medium"),
    ("Potato, baked", "veg", 93, 2.5, 21.2, 0.1, 2.2, 173, "1 medium"),
    ("Chicken breast, cooked", "protein", 165, 31.0, 0.0, 3.6, 0.0, 120, "1 palm (~120 g)"),
    ("Salmon, cooked", "protein", 206, 22.1, 0.0, 12.4, 0.0, 120, "1 fillet (~120 g)"),
    ("Egg", "protein", 143, 12.6, 0.7, 9.5, 0.0, 50, "1 large"),
    ("Ground beef 90/10, cooked", "protein", 217, 26.1, 0.0, 11.8, 0.0, 113, "4 oz"),
    ("Turkey breast, cooked", "protein", 135, 30.0, 0.0, 1.0, 0.0, 85, "3 oz"),
    ("Shrimp, cooked", "protein", 99, 24.0, 0.2, 0.3, 0.0, 85, "3 oz"),
    ("Tuna, canned in water", "protein", 116, 25.5, 0.0, 0.8, 0.0, 85, "3 oz"),
    ("Tofu, firm", "protein", 144, 17.3, 2.8, 8.7, 2.3, 126, "1/2 cup"),
    ("Tempeh", "protein", 192, 20.3, 7.6, 10.8, 0.0, 84, "1/2 cup"),
    ("Black beans, cooked", "protein", 132, 8.9, 23.7, 0.5, 8.7, 86, "1/2 cup"),
    ("Lentils, cooked", "protein", 116, 9.0, 20.1, 0.4, 7.9, 99, "1/2 cup"),
    ("Chickpeas, cooked", "protein", 164, 8.9, 27.4, 2.6, 7.6, 82, "1/2 cup"),
    ("Greek yogurt, plain nonfat", "protein", 59, 10.2, 3.6, 0.4, 0.0, 170, "1 container"),
    ("Cottage cheese 2%", "protein", 81, 10.5, 4.8, 2.3, 0.0, 113, "1/2 cup"),
    ("Milk, whole", "dairy", 61, 3.2, 4.8, 3.3, 0.0, 244, "1 cup"),
    ("Cheddar cheese", "dairy", 403, 24.9, 1.3, 33.1, 0.0, 28, "1 oz"),
    ("Oats, dry", "grain", 379, 13.2, 67.7, 6.5, 10.1, 40, "1/2 cup dry"),
    ("Brown rice, cooked", "grain", 123, 2.7, 25.6, 1.0, 1.6, 195, "1 cup"),
    ("White rice, cooked", "grain", 130, 2.7, 28.2, 0.3, 0.4, 158, "1 cup"),
    ("Quinoa, cooked", "grain", 120, 4.4, 21.3, 1.9, 2.8, 185, "1 cup"),
    ("Whole wheat bread", "grain", 252, 12.4, 42.7, 3.5, 6.0, 32, "1 slice"),
    ("Sourdough bread", "grain", 272, 10.8, 51.9, 2.4, 2.2, 50, "1 slice"),
    ("Pasta, cooked", "grain", 158, 5.8, 30.9, 0.9, 1.8, 140, "1 cup"),
    ("Corn tortilla", "grain", 218, 5.7, 44.6, 2.9, 6.3, 26, "1 tortilla"),
    ("Olive oil", "fat", 884, 0.0, 0.0, 100.0, 0.0, 13.5, "1 tbsp"),
    ("Butter", "fat", 717, 0.9, 0.1, 81.1, 0.0, 14, "1 tbsp"),
    ("Avocado", "fat", 160, 2.0, 8.5, 14.7, 6.7, 68, "1/2 avocado"),
    ("Almonds", "fat", 579, 21.2, 21.6, 49.9, 12.5, 28, "1 oz (~23)"),
    ("Walnuts", "fat", 654, 15.2, 13.7, 65.2, 6.7, 28, "1 oz"),
    ("Peanut butter", "fat", 588, 25.0, 20.0, 50.0, 6.0, 32, "2 tbsp"),
    ("Chia seeds", "fat", 486, 16.5, 42.1, 30.7, 34.4, 12, "1 tbsp"),
    ("Dark chocolate 70-85%", "other", 598, 7.8, 45.9, 42.6, 10.9, 20, "2 squares"),
    ("Honey", "other", 304, 0.3, 82.4, 0.0, 0.2, 21, "1 tbsp"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS foods(
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE,                 -- local:<slug> | fdc:<id> | off:<barcode> | custom:<n>
  name TEXT NOT NULL,
  brand TEXT,
  grp TEXT NOT NULL,
  kcal REAL, protein REAL, carbs REAL, fat REAL, fiber REAL,   -- per 100 g
  serving_g REAL, serving_label TEXT,
  barcode TEXT,
  favorite INTEGER NOT NULL DEFAULT 0,
  uses INTEGER NOT NULL DEFAULT 0,
  last_grams REAL,
  last_used TEXT,
  note TEXT                        -- e.g. 'kcal estimated from macros'
);
CREATE INDEX IF NOT EXISTS foods_name ON foods(name);
CREATE TABLE IF NOT EXISTS food_log(
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  food_id INTEGER NOT NULL REFERENCES foods(id),
  grams REAL NOT NULL,
  meal TEXT                        -- breakfast | lunch | dinner | snack
);
CREATE INDEX IF NOT EXISTS food_log_at ON food_log(at);
"""

FIELDS = ["key", "name", "brand", "grp", "kcal", "protein", "carbs", "fat", "fiber",
          "serving_g", "serving_label", "barcode", "note", "basis", "nutrients"]

# basis 'g100'   : nutrients are per 100 g, entries are in grams
# basis 'serving': nutrients are per 1 serving (e.g. "1 cup"), entries are a number of servings.
#                  Used when grams aren't known (most household amounts in your MyNetDiary history).
MIGRATIONS = {
    "foods": [("basis", "TEXT NOT NULL DEFAULT 'g100'"), ("nutrients", "TEXT"), ("last_qty", "REAL")],
    "food_log": [("qty", "REAL"), ("totals", "TEXT"), ("amount_text", "TEXT"), ("ext_key", "TEXT"),
                 ("origin", "TEXT")],
}


def ensure(con):
    con.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS food_log_ext ON food_log(ext_key) WHERE ext_key IS NOT NULL")
    for name, grp, kcal, p, c, f, fib, sg, sl in STARTER:
        con.execute(
            "INSERT OR IGNORE INTO foods(key, name, grp, kcal, protein, carbs, fat, fiber, serving_g, serving_label)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"local:{_slug(name)}", name, grp, kcal, p, c, f, fib, sg, sl))
    con.commit()


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ------------------------------------------------------------------ groups

CATEGORY_RULES = [  # substring of USDA/OFF category (lowercase) -> group, first match wins
    ("nut & seed butter", "fat"), ("nut and seed", "fat"), ("fats and oils", "fat"), ("oil", "fat"),
    ("butter", "fat"), ("nuts", "fat"), ("seeds", "fat"),
    ("dairy", "dairy"), ("yogurt", "dairy"), ("cheese", "dairy"), ("milk", "dairy"),  # before "egg"; eggs match by name
    ("fruit juice", "fruit"), ("fruit", "fruit"), ("berries", "fruit"),
    ("vegetable", "veg"), ("salad", "veg"), ("greens", "veg"),
    ("legume", "protein"), ("beans", "protein"), ("tofu", "protein"), ("beef", "protein"), ("pork", "protein"),
    ("poultry", "protein"), ("chicken", "protein"), ("turkey", "protein"), ("lamb", "protein"),
    ("fish", "protein"), ("seafood", "protein"), ("shellfish", "protein"), ("egg", "protein"),
    ("sausage", "protein"), ("meat", "protein"), ("jerky", "protein"), ("protein", "protein"),
    ("cereal", "grain"), ("grain", "grain"), ("pasta", "grain"), ("bread", "grain"), ("rice", "grain"),
    ("baked products", "grain"), ("cracker", "grain"), ("tortilla", "grain"), ("oat", "grain"),
    ("granola", "grain"), ("bagel", "grain"),
]
NAME_RULES = [  # fallback on the food's name
    (r"\b(olive oil|avocado oil|ghee|butter|almond|walnut|cashew|pecan|pistachio|peanut|tahini|avocado|chia|flax|hemp seed)", "fat"),
    (r"(blueberr|strawberr|raspberr|blackberr|cranberr|watermelon|cantaloupe|honeydew|grapefruit|clementine|mandarin|"
     r"pomegranate|nectarine|apricot|papaya|guava|lychee|persimmon|\bapple|\bbanana|\bberr|\bgrape|\borange|\bmango|"
     r"\bpear\b|\bpeach|\bplum|\bcherr|\bmelon|\bpineapple|\bkiwi|\bdates?\b|\bfigs?\b|\braisin|\blemon|\blime\b)", "fruit"),
    (r"\b(broccoli|spinach|kale|lettuce|carrot|pepper|tomato|cucumber|zucchini|squash|cauliflower|"
     r"cabbage|onion|scallion|shallot|garlic|leek|mushroom|celery|asparagus|beet|potato|greens|arugula|brussels|"
     r"bok choy|eggplant|okra|radish|corn|peas|snap pea|edamame|seaweed|nori|salad|romaine|bean sprout)", "veg"),
    (r"\b(chicken|beef|pork|turkey|salmon|tuna|shrimp|cod|egg|tofu|tempeh|seitan|lentil|bean|chickpea|"
     r"edamame|jerky|protein)", "protein"),
    (r"\b(yogurt|yoghurt|kefir|cheese|milk)", "dairy"),
    (r"\b(oat|rice|quinoa|bread|pasta|noodle|tortilla|cereal|granola|cracker|bagel|wrap|farro|barley)", "grain"),
]


STRONG_NAME_RULES = [  # names so clear they beat USDA's category ("Dairy and Egg Products" holds ghee and eggs)
    (r"\b(ghee|lard|butter)\b|(^|, )(\w+ )?oil(,|$)", "fat"),
    (r"^eggs?\b|\begg,? (whole|white|yolk)", "protein"),
]


def guess_group(name: str, category: str | None = None) -> str:
    n0 = (name or "").lower()
    for pattern, grp in STRONG_NAME_RULES:
        if re.search(pattern, n0):
            return grp
    cat = (category or "").lower()
    if cat:
        for sub, grp in CATEGORY_RULES:
            if re.search(r"\b" + re.escape(sub), cat):  # word start: "donuts" isn't "nuts"
                return grp
    n = (name or "").lower()
    for pattern, grp in NAME_RULES:
        if re.search(pattern, n):
            return grp
    return "other"


def check_kcal(kcal, p, c, f) -> tuple[float | None, str | None]:
    """Branded data is sometimes wrong (e.g. 0.8 kcal for milk). Trust macros when they disagree."""
    est = 4 * (p or 0) + 4 * (c or 0) + 9 * (f or 0)
    if kcal is None or kcal <= 0:
        return (round(est) if est else kcal), ("kcal estimated from macros" if est else None)
    if est > 20 and abs(kcal - est) / est > 0.5:
        return round(est), "kcal corrected from macros (label data looked wrong)"
    return kcal, None


# ------------------------------------------------------------------ USDA FoodData Central

FDC = "https://api.nal.usda.gov/fdc/v1/foods/search"
NUTRIENTS = {"203": "protein", "204": "fat", "205": "carbs", "208": "kcal", "291": "fiber",
             "957": "kcal_atwater", "958": "kcal_atwater2"}


def usda_key(con) -> str:
    r = con.execute("SELECT value FROM settings WHERE key='usda_api_key'").fetchone()
    return os.environ.get("USDA_API_KEY") or (r[0] if r and r[0] else "DEMO_KEY")


def _get_json(url: str, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    from . import egress
    egress.record("food", url, "a food search or barcode")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise FoodError("USDA hourly limit reached. Add your own free key: python3 -m loop connect usda") from e
        raise FoodError(f"food database error {e.code}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise FoodError(f"couldn't reach the food database ({e})") from e


def parse_fdc(f: dict) -> dict:
    n = N.from_usda(f.get("foodNutrients", []))
    kcal, note = check_kcal(n.get("kcal"), n.get("protein"), n.get("carbs"), n.get("fat"))
    if kcal is not None:
        n["kcal"] = kcal
    brand = f.get("brandName") or f.get("brandOwner")
    name = _tidy(f.get("description", ""), brand)
    sg = f.get("servingSize") if (f.get("servingSizeUnit") or "").lower() in ("g", "grm", "ml", "mlt") else None
    return {
        "key": f"fdc:{f['fdcId']}", "name": name, "brand": _title(brand) if brand else None,
        "grp": guess_group(name, f.get("foodCategory")),
        "kcal": kcal, "protein": n.get("protein"), "carbs": n.get("carbs"), "fat": n.get("fat"),
        "fiber": n.get("fiber"),
        "serving_g": round(sg, 1) if sg else 100,
        "serving_label": (f.get("householdServingFullText") or (f"{round(sg)} g" if sg else "100 g")),
        "barcode": f.get("gtinUpc"), "note": note, "source": "USDA",
        "basis": "g100", "nutrients": n,
    }


def _title(s: str) -> str:
    return s.title() if s and s.isupper() else s


def _tidy(desc: str, brand: str | None) -> str:
    d = _title(desc.strip())
    if brand:  # "WHOLE FOODS MARKET, GREEK YOGURT, ..." -> "Greek Yogurt, ..."
        b = brand.lower()
        if d.lower().startswith(b):
            d = d[len(b):].lstrip(" ,-")
    parts = [p.strip() for p in d.split(",")]
    deduped = [p for i, p in enumerate(parts) if p and p.lower() not in (q.lower() for q in parts[:i])]
    return ", ".join(deduped) or desc


def _fdc_search(con, q: str, data_type: str, size: int) -> list[dict]:
    params = urllib.parse.urlencode({"api_key": usda_key(con), "query": q, "pageSize": size, "dataType": data_type})
    return [parse_fdc(f) for f in _get_json(f"{FDC}?{params}").get("foods", [])]


def search_usda(con, q: str) -> list[dict]:
    """Local USDA database if it's been built (private, offline); otherwise the USDA website."""
    from .sources import usda_local
    if usda_local.available():
        return usda_local.search(q)
    return search_usda_online(con, q)


def search_usda_online(con, q: str) -> list[dict]:
    """Branded labels and generic foods are searched separately (mixed, generics drown out e.g. 365
    products). Branded results go first when your words match their brand ('365', 'whole foods')."""
    branded = _fdc_search(con, q, "Branded", 15)
    generic = _fdc_search(con, q, "Foundation,SR Legacy", 6)
    words = set(re.findall(r"[a-z0-9]+", q.lower()))
    brand_words = {w for f in branded for w in re.findall(r"[a-z0-9]+", (f["brand"] or "").lower())}
    brand_hint = bool(words & brand_words - {"the", "and", "of", "inc", "market"})
    out = branded + generic if brand_hint else generic + branded
    return [x for x in out if x["kcal"] is not None]


def lookup_barcode(con, code: str) -> dict | None:
    code = re.sub(r"\D", "", code)
    if not code:
        return None
    from .sources import usda_local
    if usda_local.available():
        hit = usda_local.by_barcode(code)
        if hit:
            return hit
    for variant in dict.fromkeys([code, code.lstrip("0"), code.zfill(12), code.zfill(13), code.zfill(14)]):
        params = urllib.parse.urlencode({"api_key": usda_key(con), "query": variant, "pageSize": 5,
                                         "dataType": "Branded"})
        try:
            data = _get_json(f"{FDC}?{params}")
        except FoodError:
            break
        for f in data.get("foods", []):
            if (f.get("gtinUpc") or "").lstrip("0") == code.lstrip("0"):
                return parse_fdc(f)
    data = _get_json(f"https://world.openfoodfacts.org/api/v2/product/{code}.json"
                     "?fields=product_name,brands,nutriments,categories_tags,serving_quantity,serving_size")
    if data.get("status") != 1:
        return None
    return parse_off(code, data["product"])


def parse_off(code: str, p: dict) -> dict:
    n = p.get("nutriments", {})
    kcal, note = check_kcal(n.get("energy-kcal_100g"), n.get("proteins_100g"),
                            n.get("carbohydrates_100g"), n.get("fat_100g"))
    sg = p.get("serving_quantity")
    name = p.get("product_name") or f"Product {code}"
    return {
        "key": f"off:{code}", "name": name, "brand": (p.get("brands") or "").split(",")[0] or None,
        "grp": guess_group(name, " ".join(p.get("categories_tags", []))),
        "kcal": kcal, "protein": n.get("proteins_100g"), "carbs": n.get("carbohydrates_100g"),
        "fat": n.get("fat_100g"), "fiber": n.get("fiber_100g"),
        "serving_g": float(sg) if sg else 100, "serving_label": p.get("serving_size") or "100 g",
        "barcode": code, "note": note, "source": "Open Food Facts",
        "basis": "g100",
        "nutrients": {k: v for k, v in {
            "kcal": kcal, "protein": n.get("proteins_100g"), "carbs": n.get("carbohydrates_100g"),
            "fat": n.get("fat_100g"), "fiber": n.get("fiber_100g"), "sugar": n.get("sugars_100g"),
            "sat_fat": n.get("saturated-fat_100g"),
            "sodium": (n["sodium_100g"] * 1000) if n.get("sodium_100g") is not None else None,
            "added_sugar": n.get("added-sugars_100g"), "caffeine": (n["caffeine_100g"] * 1000) if n.get("caffeine_100g") else None,
        }.items() if v is not None},
    }


# ------------------------------------------------------------------ local store

def search_local(con, q: str, limit=25) -> list[dict]:
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w]
    if not words:
        return []
    where = " AND ".join(["(lower(name) LIKE ? OR lower(COALESCE(brand,'')) LIKE ?)"] * len(words))
    params = [x for w in words for x in (f"%{w}%", f"%{w}%")]
    return [dict(r) for r in con.execute(
        f"SELECT * FROM foods WHERE {where} ORDER BY favorite DESC, uses DESC, length(name) LIMIT ?",
        params + [limit])]


def profile(food: dict) -> dict:
    """All known nutrients for one basis unit (100 g or 1 serving)."""
    n = json.loads(food["nutrients"]) if isinstance(food.get("nutrients"), str) else dict(food.get("nutrients") or {})
    for m in N.MACROS:
        if food.get(m) is not None and m not in n:
            n[m] = food[m]
    if "carbs" in n and "net_carbs" not in n:
        n["net_carbs"] = max(0.0, n["carbs"] - n.get("fiber", 0))
    return n


def upsert_food(con, food: dict) -> int:
    """Insert a food by key (or return the existing one). Keeps your favorite/group edits."""
    food = {k: food.get(k) for k in FIELDS}
    food["basis"] = food["basis"] or "g100"
    if isinstance(food["nutrients"], dict):
        food["nutrients"] = json.dumps({k: round(v, 4) for k, v in food["nutrients"].items() if v is not None})
    if not food["key"]:
        n = con.execute("SELECT COUNT(*) FROM foods WHERE key LIKE 'custom:%'").fetchone()[0]
        food["key"] = f"custom:{n + 1}:{_slug(food['name'])}"
    if food["grp"] not in GROUPS:
        food["grp"] = guess_group(food["name"])
    existing = con.execute("SELECT id FROM foods WHERE key=?", (food["key"],)).fetchone()
    if existing:
        return existing[0]
    cur = con.execute(f"INSERT INTO foods({', '.join(FIELDS)}) VALUES({', '.join('?' * len(FIELDS))})",
                      [food[k] for k in FIELDS])
    return cur.lastrowid


LABEL_EXTRAS = ["sugar", "added_sugar", "sat_fat", "sodium", "cholesterol", "potassium", "calcium", "iron",
                "vit_d", "caffeine"]


def custom_food(con, body: dict) -> int:
    """From a nutrition label: values per serving -> stored per 100 g."""
    sg = float(body.get("serving_g") or 100)
    scale = 100 / sg
    num = lambda k: (float(body[k]) * scale) if body.get(k) not in (None, "") else None
    p, c, f = num("protein"), num("carbs"), num("fat")
    kcal, note = check_kcal(num("kcal"), p, c, f)
    extra = {k: num(k) for k in LABEL_EXTRAS if num(k) is not None}
    return upsert_food(con, {
        "key": None, "name": body["name"].strip(), "brand": (body.get("brand") or "").strip() or None,
        "grp": body.get("grp") or guess_group(body["name"]),
        "kcal": kcal, "protein": p, "carbs": c, "fat": f, "fiber": num("fiber"),
        "serving_g": sg, "serving_label": body.get("serving_label") or f"{round(sg)} g",
        "barcode": body.get("barcode"), "note": note, "basis": "g100",
        "nutrients": {**extra, **{k: v for k, v in {"kcal": kcal, "protein": p, "carbs": c, "fat": f,
                                                    "fiber": num("fiber")}.items() if v is not None}},
    })


MEALS = ["breakfast", "lunch", "dinner", "snack", "dessert"]


def meal_for(at: datetime) -> str:
    """Default meal for a time; you can always pick another (e.g. dessert, or a snack after dinner)."""
    m = at.hour * 60 + at.minute
    if 300 <= m < 645:
        return "breakfast"
    if 645 <= m < 900:
        return "lunch"
    if 1020 <= m < 1230:
        return "dinner"
    return "snack"


def entry_totals(food: dict, grams: float | None, qty: float | None) -> dict:
    factor = (qty or 0) if food.get("basis") == "serving" else (grams or 0) / 100
    return {k: round(v * factor, 4) for k, v in profile(food).items()}


def log(con, food_id: int, grams: float | None = None, at: str | None = None, meal: str | None = None,
        qty: float | None = None, amount_text: str | None = None, totals: dict | None = None,
        ext_key: str | None = None, origin: str = "loop") -> int | None:
    """Log an amount: grams for per-100 g foods, qty (servings) for per-serving foods."""
    when = datetime.fromisoformat(at) if at else datetime.now()
    food = dict(con.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone())
    if food["basis"] == "serving" and qty is None:
        qty = 1.0
    totals = totals if totals is not None else entry_totals(food, grams, qty)
    cur = con.execute(
        "INSERT OR IGNORE INTO food_log(at, food_id, grams, meal, qty, totals, amount_text, ext_key, origin)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (when.isoformat(timespec="minutes"), food_id, grams or 0, meal or meal_for(when), qty,
         json.dumps(totals), amount_text, ext_key, origin))
    if not cur.rowcount:
        return None  # already imported
    con.execute("UPDATE foods SET uses = uses + 1, last_grams=COALESCE(?, last_grams), last_qty=COALESCE(?, last_qty),"
                " last_used=MAX(COALESCE(last_used,''), ?) WHERE id=?",
                (grams if food["basis"] == "g100" else None, qty, when.isoformat(timespec="minutes"), food_id))
    return cur.lastrowid


MEAL_SLOT = {"dessert": "snack"}


def _slot_meals(meal: str) -> tuple:
    slot = MEAL_SLOT.get(meal, meal)
    return tuple(m for m in ("breakfast", "lunch", "dinner", "snack", "dessert") if MEAL_SLOT.get(m, m) == slot)


def meal_time(con, day: str, meal: str) -> str | None:
    """A meal is eaten at one time: the time already used by that meal on that day, if any."""
    ms = _slot_meals(meal)
    r = con.execute(f"SELECT at FROM food_log WHERE substr(at,1,10)=? AND meal IN ({','.join('?' * len(ms))}) ORDER BY at LIMIT 1",
                    (day, *ms)).fetchone()
    return r["at"] if r else None


def set_meal_time(con, day: str, meal: str, hhmm: str) -> int:
    ms = _slot_meals(meal)
    cur = con.execute(f"UPDATE food_log SET at=? WHERE substr(at,1,10)=? AND meal IN ({','.join('?' * len(ms))})",
                      (f"{day}T{hhmm[:5]}", day, *ms))
    return cur.rowcount


def move_entry(con, entry_id: int, meal: str):
    """Move a logged food to another meal; it takes that meal's time (or keeps its own if the meal is empty)."""
    row = con.execute("SELECT at, meal FROM food_log WHERE id=?", (entry_id,)).fetchone()
    if not row or row["meal"] == meal:
        return
    at = meal_time(con, row["at"][:10], meal) or row["at"]
    con.execute("UPDATE food_log SET meal=?, at=? WHERE id=?", (meal, at, entry_id))


def update_entry(con, entry_id: int, amount: float):
    row = con.execute("SELECT l.*, f.basis FROM food_log l JOIN foods f ON f.id=l.food_id WHERE l.id=?",
                      (entry_id,)).fetchone()
    food = dict(con.execute("SELECT * FROM foods WHERE id=?", (row["food_id"],)).fetchone())
    if food["basis"] == "serving":
        old = row["qty"] or 1
        if row["origin"] == "mynetdiary" and row["totals"]:  # scale the logged totals
            t = {k: v * amount / old for k, v in json.loads(row["totals"]).items()}
        else:
            t = entry_totals(food, None, amount)
        con.execute("UPDATE food_log SET qty=?, totals=? WHERE id=?", (amount, json.dumps(t), entry_id))
    else:
        con.execute("UPDATE food_log SET grams=?, totals=? WHERE id=?",
                    (amount, json.dumps(entry_totals(food, amount, None)), entry_id))


def recent_meals(con, d: date, days: int = 7, limit: int = 8) -> list[dict]:
    """Meals from the days before `d`, newest first, for copying over. Desserts count as snacks,
    and a meal identical to a newer one (same foods, same amounts) is shown once."""
    entries = _entries(con, (d - timedelta(days=days)).isoformat(), d.isoformat())
    meals: dict[tuple, list] = {}
    for e in entries:
        e["t"] = json.loads(e.pop("totals") or "{}")
        e["macros"] = {k: round(e["t"].get(k, 0), 1) for k in N.MACROS}
        meals.setdefault((e["at"][:10], MEAL_SLOT.get(e["meal"], e["meal"])), []).append(e)
    out, seen = [], set()
    for (day_, meal), items in sorted(meals.items(), key=lambda kv: kv[1][0]["at"], reverse=True):
        sig = tuple(sorted((e["food_id"], round((e["qty"] if e["basis"] == "serving" else e["grams"]) or 0, 1)) for e in items))
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"date": day_, "meal": meal, "items": items,
                    "kcal": round(sum(e["macros"]["kcal"] for e in items))})
    return out[:limit]


def copy_entries(con, entry_ids: list[int], day: str, meal: str | None, hhmm: str) -> int:
    """Log the same foods and amounts again, into `meal` on `day`. Nutrition is copied as logged,
    so imported entries keep their original totals. Joins that meal's time if it already has one."""
    n = 0
    for i in entry_ids:
        r = con.execute("SELECT * FROM food_log WHERE id=?", (int(i),)).fetchone()
        if not r:
            continue
        m = meal or r["meal"]
        at = meal_time(con, day, m) or f"{day}T{hhmm[:5]}"
        log(con, r["food_id"], grams=r["grams"] or None, qty=r["qty"], at=at, meal=m,
            totals=json.loads(r["totals"]) if r["totals"] else None, amount_text=r["amount_text"])
        n += 1
    return n


def move_entries(con, entry_ids: list[int], day: str, meal: str | None, hhmm: str) -> int:
    """Move logged foods to another day (and meal). They keep their amounts and nutrition; each joins the target
    meal's time if that meal already has one. meal=None keeps each food's own meal."""
    n = 0
    for i in entry_ids:
        r = con.execute("SELECT id, meal FROM food_log WHERE id=?", (int(i),)).fetchone()
        if not r:
            continue
        m = meal or r["meal"]
        at = meal_time(con, day, m) or f"{day}T{hhmm[:5]}"
        con.execute("UPDATE food_log SET at=?, meal=? WHERE id=?", (at, m, r["id"]))
        n += 1
    return n


def _entries(con, lo: str, hi: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT l.id, l.at, l.grams, l.qty, l.meal, l.totals, l.amount_text, l.origin,
                  f.id AS food_id, f.name, f.brand, f.grp, f.basis, f.serving_g, f.serving_label, f.favorite,
                  f.kcal, f.protein, f.carbs, f.fat, f.fiber
           FROM food_log l JOIN foods f ON f.id = l.food_id
           WHERE l.at >= ? AND l.at < ? ORDER BY l.at""", (lo, hi))]


def nutrient_report(entries: list[dict], settings: dict, days: int = 1) -> list[dict]:
    """Daily (or per-day average) totals for every nutrient that any entry has data for."""
    tg = N.targets(settings)
    sums, known = {k: 0.0 for k in N.KEYS}, {k: 0 for k in N.KEYS}
    for e in entries:
        for k, v in e["t"].items():
            if k in sums:
                sums[k] += v
                known[k] += 1
    out = []
    for k in N.KEYS:
        if not known[k]:
            continue  # never logged: don't pretend it's zero
        m = N.META[k]
        val = sums[k] / days
        out.append({"key": k, "label": m["label"], "unit": m["unit"], "group": m["group"], "kind": m["kind"],
                    "value": round(val, 1 if val < 10 else 0), "target": tg[k],
                    "pct": round(100 * val / tg[k]) if tg[k] else None,
                    "coverage": round(100 * known[k] / len(entries)) if entries else 0})
    return out


def day(con, d: date, settings: dict) -> dict:
    entries = _entries(con, d.isoformat(), (d + timedelta(days=1)).isoformat())
    groups = {g: {"grams": 0.0, "kcal": 0.0} for g in GROUPS}
    for e in entries:
        e["t"] = json.loads(e.pop("totals") or "{}")
        e["macros"] = {k: round(e["t"].get(k, 0), 1) for k in N.MACROS}
        groups[e["grp"]]["grams"] += e["grams"] or 0
        groups[e["grp"]]["kcal"] += e["t"].get("kcal", 0)
    report = nutrient_report(entries, settings)
    tot = {r["key"]: r["value"] for r in report}
    num = lambda k, dflt: float(settings.get(k) or dflt)
    opt = lambda k: float(settings[k]) if settings.get(k) not in (None, "", "off") else None
    targets = {"carbs": opt("target_carbs"), "fat": opt("target_fat"),
               "fruit_veg_g": num("target_fruit_veg_g", 400), "protein_g": num("target_protein_g", 100),
               "fiber_g": num("target_fiber_g", 30), "kcal": float(settings["target_kcal"]) if settings.get("target_kcal") else None}
    return {
        "date": d.isoformat(), "entries": entries,
        "totals": {k: round(tot.get(k, 0)) for k in N.MACROS},
        "nutrients": report,
        "groups": {g: {k: round(v) for k, v in x.items()} for g, x in groups.items()},
        "fruit_veg_g": round(groups["fruit"]["grams"] + groups["veg"]["grams"]),
        "targets": targets,
        "favorites": quick_list(con, "favorite"),
        "recents": quick_list(con, "recent"),
        "group_labels": GROUP_LABELS,
        "group_detail": settings.get("food_groups_detail") == "on",   # split carbs into fruit / veg / grain
    }


def trends(con, d: date, settings: dict, days: int = 30) -> dict:
    """Per-day averages over the last `days` days that have any food logged, plus a daily series."""
    lo = d - timedelta(days=days - 1)
    entries = _entries(con, lo.isoformat(), (d + timedelta(days=1)).isoformat())
    by_day: dict[str, dict] = {}
    for e in entries:
        e["t"] = json.loads(e.pop("totals") or "{}")
        dd = by_day.setdefault(e["at"][:10], {k: 0.0 for k in ("kcal", "protein", "fiber", "sugar", "sodium")})
        for k in dd:
            dd[k] += e["t"].get(k, 0)
    logged = len(by_day)
    return {
        "days": days, "logged_days": logged,
        "averages": nutrient_report(entries, settings, days=max(logged, 1)),
        "series": [{"date": k, **{m: round(v) for m, v in by_day[k].items()}} for k in sorted(by_day)],
    }


def quick_list(con, kind: str, limit=16) -> list[dict]:
    if kind == "favorite":
        sql = "SELECT * FROM foods WHERE favorite=1 ORDER BY uses DESC, name LIMIT ?"
    else:
        sql = "SELECT * FROM foods WHERE last_used IS NOT NULL AND favorite=0 ORDER BY last_used DESC LIMIT ?"
    return [dict(r) for r in con.execute(sql, (limit,))]


def meal_times(con, since: date) -> list[dict]:
    """Main-meal times from the food log (snacks/desserts excluded), for the 3-meals check.
    Imported MyNetDiary entries are skipped: their timestamps are when you *logged*, not when you ate."""
    return [{"at": r[0], "meal": r[1]} for r in con.execute(
        "SELECT DISTINCT at, meal FROM food_log WHERE at >= ? AND meal IN ('breakfast','lunch','dinner') "
        "AND COALESCE(origin, '') != 'mynetdiary' ORDER BY at", (since.isoformat(),))]


def days_with_three_meals_logged(con, end: date, days: int = 7) -> int:
    """Days where breakfast, lunch and dinner were all logged (any time, any source)."""
    lo = end - timedelta(days=days - 1)
    return con.execute(
        """SELECT COUNT(*) FROM (SELECT substr(at,1,10) d FROM food_log WHERE at >= ? AND at < ?
           AND meal IN ('breakfast','lunch','dinner') GROUP BY d HAVING COUNT(DISTINCT meal) = 3)""",
        (lo.isoformat(), (end + timedelta(days=1)).isoformat())).fetchone()[0]


def caffeine_events(con, d: date) -> list[dict]:
    """Caffeine from logged food/drinks (e.g. matcha, coffee) for the caffeine model."""
    out = []
    for at, totals in con.execute("SELECT at, totals FROM food_log WHERE substr(at,1,10)=?", (d.isoformat(),)):
        mg = json.loads(totals or "{}").get("caffeine", 0)
        if mg >= 5:
            out.append({"at": at, "value": mg})
    return out


SCORE_PARTS = [  # key, label, weight
    ("protein", "Protein", 20), ("fiber", "Fiber", 20), ("calories", "Calories in range", 15), ("meals", "Meals logged", 15),
    ("water", "Water", 10), ("added_sugar", "Added sugar", 10), ("sodium", "Sodium", 10),
]


def food_score(out: dict, settings: dict) -> dict:
    """0-100 for the day so far, from parts you can act on, with the math and the next best move for each.
    Missing data never counts against you. Parts you switched off (setting food_score_off) don't count."""
    t, tg = out.get("totals") or {}, out.get("targets") or {}
    rep = {r["key"]: r.get("value") for r in out.get("nutrients") or []}
    eaten = t.get("kcal") or 0
    off = {x for x in (settings.get("food_score_off") or "").split(",") if x}
    up = lambda v, target: None if not target else max(0.0, min(1.0, (v or 0) / target))
    def under(v, limit):
        if v is None or not limit:
            return None
        return 1.0 if v <= limit else max(0.0, 1 - (v - limit) / limit)
    meals = {slot_of(e.get("meal")) for e in out.get("entries") or []} & {"breakfast", "lunch", "dinner"}
    budget = tg.get("kcal")
    sugar_lim = float(settings.get("target_added_sugar_g") or 25)
    sodium_lim = float(settings.get("target_sodium_mg") or 2300)
    oz = out.get("water") or 0
    glass = 8.0
    v = {  # key: (fraction, your value text, target text, tip if not full)
        "protein": (up(t.get("protein"), tg.get("protein_g")), f"{round(t.get('protein') or 0)} g", f"{round(tg.get('protein_g') or 0)} g",
                    lambda: f"Add about {round(tg['protein_g'] - (t.get('protein') or 0))} g of protein: a chicken breast is about 30 g, Greek yogurt about 17 g, two eggs about 12 g."),
        "fiber": (up(t.get("fiber"), tg.get("fiber_g")), f"{round(t.get('fiber') or 0)} g", f"{round(tg.get('fiber_g') or 0)} g",
                  lambda: f"Add about {round(tg['fiber_g'] - (t.get('fiber') or 0))} g of fiber: a cup of raspberries is about 8 g, half a cup of beans about 7 g, a pear about 6 g."),
        "calories": (None if not budget or not eaten else (1.0 if eaten <= budget * 1.05 else max(0.0, 1 - (eaten - budget * 1.05) / (budget * 0.3))),
                     f"{round(eaten)} kcal", (f"up to {round(budget * 1.05)} kcal" if budget else "set a budget"),
                     lambda: f"You're {round(eaten - budget)} kcal over: keep the next meal light (vegetables and protein), or take a 30-minute walk (about 150 kcal)."),
        "meals": (len(meals) / 3 if out.get("entries") else None, f"{len(meals)} of 3", "breakfast, lunch, dinner",
                  lambda: "Log " + " and ".join(m for m in ("breakfast", "lunch", "dinner") if m not in meals) + " (or mark meals done for today on Today)."),
        "water": (up(oz, out.get("water_target")), f"{round(oz)} oz", f"{round(out.get('water_target') or 0)} oz",
                  lambda: f"Drink {max(1, round(((out.get('water_target') or 0) - oz) / glass))} more glass{'es' if round(((out.get('water_target') or 0) - oz) / glass) != 1 else ''} of water."),
        "added_sugar": (under(rep.get("added_sugar"), sugar_lim) if eaten else None, f"{round(rep.get('added_sugar') or 0)} g", f"under {round(sugar_lim)} g",
                        lambda: f"{round((rep.get('added_sugar') or 0) - sugar_lim)} g over the added-sugar limit: swap the next sweet drink or dessert for fruit or water."),
        "sodium": (under(rep.get("sodium"), sodium_lim) if eaten else None, f"{round(rep.get('sodium') or 0)} mg", f"under {round(sodium_lim)} mg",
                   lambda: f"{round((rep.get('sodium') or 0) - sodium_lim)} mg over on sodium: go easy on sauces, chips and deli meat for the rest of the day."),
    }
    rows, have = [], []
    for k, label, w in SCORE_PARTS:
        frac, mine, target, tip = v[k]
        counted = frac is not None and k not in off
        if counted:
            have.append((k, w, frac))
        rows.append({"key": k, "label": label, "weight": w, "on": k not in off, "pct": None if frac is None else round(100 * frac),
                     "value": mine, "target": target, "tip": tip() if (counted and frac < 0.999) else None})
    wsum = sum(w for _, w, _ in have)
    score = round(100 * sum(w * f for _, w, f in have) / wsum) if wsum else None
    for r in rows:   # points this part earned and could still earn, out of 100
        f = next((f for k, _, f in have if k == r["key"]), None)
        r["points"] = None if f is None or not wsum else round(100 * r["weight"] * f / wsum)
        r["possible"] = None if f is None or not wsum else round(100 * r["weight"] / wsum)
    label = None if score is None else "Great" if score >= 80 else "Good" if score >= 60 else "Okay" if score >= 40 else "Getting started"
    better = sorted([r for r in rows if r["tip"]], key=lambda r: -((r["possible"] or 0) - (r["points"] or 0)))
    return {"score": score, "label": label, "parts": rows,
            "next": [{"key": r["key"], "tip": r["tip"], "gain": (r["possible"] or 0) - (r["points"] or 0)} for r in better[:3]]}


def slot_of(meal):
    return "snack" if meal == "dessert" else meal
