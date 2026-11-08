"""
USDA FoodData Central, on your Mac: private, offline, no rate limit.

  python3 -m loop usda-build     # reads data/usda/raw/*.zip, writes data/usda/usda.db (once, a few minutes)

Uses the Branded (all US packaged labels, incl. Whole Foods / 365), SR Legacy (~7,800 generic
foods) and Survey/FNDDS (mixed dishes) releases. Only the nutrients loop tracks are kept, per
100 g, plus serving sizes and barcodes. Discontinued branded products are skipped.
The zips are read in place (not extracted).
"""
import csv
import io
import json
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

from .. import nutrients as N

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "usda" / "raw"
DB = ROOT / "data" / "usda" / "usda.db"
csv.field_size_limit(sys.maxsize)

SCHEMA = """
CREATE TABLE foods(
  fdc_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  brand TEXT,
  category TEXT,
  data_type TEXT,           -- branded | sr_legacy | survey
  gtin TEXT,                -- barcode without leading zeros
  serving_g REAL,
  serving_label TEXT,
  nutrients TEXT            -- JSON, per 100 g
);
CREATE INDEX foods_gtin ON foods(gtin);
CREATE VIRTUAL TABLE foods_fts USING fts5(name, brand, content='foods', content_rowid='fdc_id',
                                          tokenize='unicode61 remove_diacritics 2');
"""
OMEGA3 = {"851", "629", "631", "621"}


def available() -> bool:
    return DB.exists()


def _rows(z: zipfile.ZipFile, name: str):
    hits = [i.filename for i in z.infolist() if i.filename.endswith("/" + name)]
    if not hits:
        return
    with z.open(hits[0]) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", newline=""))


def _title(s: str) -> str:
    return s.title() if s and s.isupper() else s


def _tidy(desc: str, brand: str | None) -> str:
    d = _title((desc or "").strip())
    if brand and d.lower().startswith(brand.lower()):
        d = d[len(brand):].lstrip(" ,-")
    parts = [p.strip() for p in d.split(",")]
    return ", ".join(p for i, p in enumerate(parts) if p and p.lower() not in (q.lower() for q in parts[:i])) or desc


def build(log=print) -> dict:
    zips = sorted(RAW.glob("*.zip"))
    if not zips:
        raise SystemExit(f"No USDA zips in {RAW}")
    tmp = DB.with_suffix(".building.db")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA + "CREATE TABLE nut(fdc_id INTEGER, k TEXT, v REAL);")
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    stats = {}
    t0 = time.time()
    for zpath in zips:
        kind = ("branded" if "branded" in zpath.name else "sr_legacy" if "sr_legacy" in zpath.name
                else "survey" if "survey" in zpath.name else "foundation")
        z = zipfile.ZipFile(zpath)
        if not any(i.filename.endswith("/food.csv") for i in z.infolist()):
            log(f"skip {zpath.name}: no food.csv in this release")
            continue
        # nutrient id -> loop key
        idkey, omega_ids = {}, set()
        for r in _rows(z, "nutrient.csv"):
            nbr = (r.get("nutrient_nbr") or "").split(".")[0]
            # Survey (FNDDS) rows reference the public nutrient number; the others use the internal id.
            ref = nbr if kind == "survey" else r["id"]
            if nbr in N.USDA_NUMBER:
                idkey[ref] = N.USDA_NUMBER[nbr]
            if nbr in OMEGA3:
                omega_ids.add(ref)
        cats = {r["id"]: r.get("description") for r in _rows(z, "food_category.csv")}
        cats.update({r.get("wweia_food_category"): r.get("wweia_food_category_description")
                     for r in _rows(z, "wweia_food_category.csv")})
        branded = {}
        if kind == "branded":
            for r in _rows(z, "branded_food.csv"):
                if r.get("discontinued_date"):
                    continue
                branded[r["fdc_id"]] = r
        portions = {}
        for r in _rows(z, "food_portion.csv"):
            if r["fdc_id"] in portions or not r.get("gram_weight"):
                continue
            desc = r.get("portion_description") or " ".join(x for x in (r.get("amount"), r.get("modifier")) if x)
            try:
                portions[r["fdc_id"]] = (float(r["gram_weight"]), desc.strip() or None)
            except ValueError:
                pass
        n_food = 0
        batch = []
        for r in _rows(z, "food.csv"):
            fid = r["fdc_id"]
            b = branded.get(fid) if kind == "branded" else None
            if kind == "branded" and b is None:
                continue
            brand = (b.get("brand_name") or b.get("brand_owner")) if b else None
            serving_g, label = None, None
            if b:
                unit = (b.get("serving_size_unit") or "").lower()
                try:
                    serving_g = float(b["serving_size"]) if unit in ("g", "grm", "ml", "mlt") and b.get("serving_size") else None
                except ValueError:
                    serving_g = None
                label = b.get("household_serving_fulltext") or (f"{round(serving_g)} g" if serving_g else None)
            elif fid in portions:
                serving_g, label = portions[fid]
            gtin = (b.get("gtin_upc") or "").lstrip("0") if b else None
            category = (b.get("branded_food_category") if b else None) or cats.get(r.get("food_category_id"))
            batch.append((int(fid), _tidy(r["description"], brand), _title(brand) if brand else None, category,
                          kind, gtin or None, serving_g, label))
            n_food += 1
            if len(batch) >= 50000:
                con.executemany("INSERT OR IGNORE INTO foods(fdc_id,name,brand,category,data_type,gtin,serving_g,serving_label)"
                                " VALUES(?,?,?,?,?,?,?,?)", batch)
                batch = []
        con.executemany("INSERT OR IGNORE INTO foods(fdc_id,name,brand,category,data_type,gtin,serving_g,serving_label)"
                        " VALUES(?,?,?,?,?,?,?,?)", batch)
        log(f"{zpath.name}: {n_food:,} foods ({time.time() - t0:.0f}s)")
        # nutrients (the big one: streamed)
        n_rows, batch = 0, []
        for r in _rows(z, "food_nutrient.csv"):
            nid = r["nutrient_id"]
            if nid not in idkey and nid not in omega_ids:
                continue
            try:
                v = float(r["amount"])
            except (TypeError, ValueError):
                continue
            k = idkey.get(nid) or "omega3_part"
            if nid in omega_ids:
                k, v = "omega3_part", v * 1000  # g -> mg, summed below
            batch.append((int(r["fdc_id"]), k, v))
            n_rows += 1
            if len(batch) >= 200000:
                con.executemany("INSERT INTO nut VALUES(?,?,?)", batch)
                batch = []
        con.executemany("INSERT INTO nut VALUES(?,?,?)", batch)
        con.commit()
        log(f"  nutrients: {n_rows:,} values kept ({time.time() - t0:.0f}s)")
        stats[kind] = n_food
    log("combining nutrients per food…")
    con.execute("CREATE INDEX nut_fdc ON nut(fdc_id)")
    con.execute("""CREATE TABLE agg AS
        SELECT fdc_id, json_group_object(k, v) AS j FROM (
          SELECT fdc_id, k, MAX(v) AS v FROM nut WHERE k != 'omega3_part' GROUP BY fdc_id, k
          UNION ALL
          SELECT fdc_id, 'omega3', SUM(v) FROM nut WHERE k = 'omega3_part' GROUP BY fdc_id
        ) GROUP BY fdc_id""")
    con.execute("CREATE INDEX agg_fdc ON agg(fdc_id)")
    con.execute("UPDATE foods SET nutrients = (SELECT j FROM agg WHERE agg.fdc_id = foods.fdc_id)")
    con.execute("DELETE FROM foods WHERE nutrients IS NULL")
    con.executescript("DROP TABLE nut; DROP TABLE agg;")
    con.execute("INSERT INTO foods_fts(rowid, name, brand) SELECT fdc_id, name, COALESCE(brand,'') FROM foods")
    con.commit()
    con.execute("VACUUM")
    total = con.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    con.close()
    tmp.replace(DB)
    stats["total"] = total
    stats["seconds"] = round(time.time() - t0)
    stats["size_mb"] = round(DB.stat().st_size / 1e6)
    log(f"done: {total:,} foods, {stats['size_mb']} MB, {stats['seconds']}s → {DB}")
    return stats


# ------------------------------------------------------------------ querying

_con = None


def _db():
    global _con
    if _con is None:
        _con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
        _con.row_factory = sqlite3.Row
    return _con


def to_food(r) -> dict:
    from ..food import check_kcal, guess_group
    n = json.loads(r["nutrients"] or "{}")
    kcal, note = check_kcal(n.get("kcal"), n.get("protein"), n.get("carbs"), n.get("fat"))
    if kcal is not None:
        n["kcal"] = kcal
    if "carbs" in n and "net_carbs" not in n:
        n["net_carbs"] = max(0.0, n["carbs"] - n.get("fiber", 0))
    return {
        "key": f"fdc:{r['fdc_id']}", "name": r["name"], "brand": r["brand"],
        "grp": guess_group(r["name"], r["category"]),
        "kcal": n.get("kcal"), "protein": n.get("protein"), "carbs": n.get("carbs"), "fat": n.get("fat"),
        "fiber": n.get("fiber"), "serving_g": r["serving_g"] or 100,
        "serving_label": r["serving_label"] or (f"{round(r['serving_g'])} g" if r["serving_g"] else "100 g"),
        "barcode": r["gtin"], "note": note, "basis": "g100", "nutrients": n,
        "source": {"branded": "USDA label", "sr_legacy": "USDA", "survey": "USDA (dish)"}.get(r["data_type"], "USDA"),
    }


def _match(words: list[str]) -> str:
    """FTS query: every word must match as a prefix; plurals/possessives also match the stem
    ("joes" → joe, "bananas" → banana)."""
    parts = []
    for w in words:
        w = w.replace("'", "")
        alts = {w}
        if len(w) > 3 and w.endswith("s"):
            alts.add(w[:-1])
        parts.append("(" + " OR ".join(f'"{a}"*' for a in sorted(alts)) + ")")
    return " AND ".join(parts)


SQL = """SELECT f.*, bm25(foods_fts, 1.0, 0.6) AS score FROM foods_fts JOIN foods f ON f.fdc_id = foods_fts.rowid
         WHERE foods_fts MATCH ? {filt} ORDER BY score LIMIT ?"""


def search(q: str, limit: int = 25) -> list[dict]:
    words = [w for w in re.findall(r"[\w']+", q.lower()) if w not in ("by", "the", "and", "of", "with")]
    if not words:
        return []
    db = _db()
    rows = []
    for attempt in range(len(words)):  # if nothing matches every word, drop the shortest word and retry
        m = _match(words)
        generic = db.execute(SQL.format(filt="AND f.data_type != 'branded'"), (m, 15)).fetchall()
        branded = db.execute(SQL.format(filt="AND f.data_type = 'branded'"), (m, 250)).fetchall()
        rows = generic + branded
        if rows or len(words) == 1:
            break
        words = sorted(words, key=len, reverse=True)[:-1]
    brand_words = {w for r in branded for w in re.findall(r"\w+", (r["brand"] or "").lower())}
    generic_words = {w for r in generic for w in re.findall(r"\w+", r["name"].lower())}
    stems = lambda w: {w.replace("'", ""), w.replace("'", "").rstrip("s")}
    # a word hints at a brand only if it's in brand names but not in plain food names ("banana" isn't a brand)
    brand_hint = any(w.isdigit() for w in words) or any(
        (stems(w) & brand_words) and not (stems(w) & generic_words) for w in words)

    def rank(r):
        s = r["score"]  # bm25: lower is better
        if r["data_type"] != "branded":
            s += 2 if brand_hint else -8  # "banana" → raw bananas before 60 kinds of banana chips
        elif brand_hint and r["brand"] and set(words) & set(re.findall(r"\w+", r["brand"].lower())):
            s -= 6
        return s + len(r["name"]) / 50
    out, seen = [], set()
    for r in sorted(rows, key=rank):
        n = json.loads(r["nutrients"] or "{}")
        key = (r["name"].lower(), (r["brand"] or "").lower(), round(n.get("kcal") or 0))
        if key in seen:
            continue  # the same product listed several times
        seen.add(key)
        out.append(to_food(r))
        if len(out) >= limit:
            break
    return out


def by_barcode(code: str) -> dict | None:
    code = re.sub(r"\D", "", code).lstrip("0")
    if not code:
        return None
    r = _db().execute("SELECT * FROM foods WHERE gtin=? LIMIT 1", (code,)).fetchone()
    return to_food(r) if r else None
