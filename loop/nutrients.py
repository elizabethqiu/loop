"""
The nutrients loop tracks, chosen to match what your MyNetDiary history actually has data for
(columns at least ~40% filled in), plus omega-3s.

Each: key, label, unit, USDA nutrient number(s), MyNetDiary column, daily target, kind.
  kind = "goal"  (aim to reach: fiber, potassium, vitamins…)
         "limit" (stay under: sodium, added sugar, saturated fat…)
         "info"  (no target: calories without a goal, total carbs, alcohol…)
Targets are the FDA Daily Values used for the % on US nutrition labels; override any of them
in Log → Settings as `target_<key>`.
"""

NUTRIENTS = [
    # key, label, unit, usda numbers, mynetdiary column, target, kind, group
    ("kcal", "Calories", "kcal", ["208", "957", "958"], "Calories, cals", None, "info", "macros"),
    ("carbs", "Carbs", "g", ["205"], "Total Carbs, g", None, "info", "macros"),
    ("protein", "Protein", "g", ["203"], "Protein, g", 100, "goal", "macros"),
    ("fat", "Fat", "g", ["204"], "Total Fat, g", None, "info", "macros"),
    ("fiber", "Fiber", "g", ["291"], "Dietary Fiber, g", 28, "goal", "macros"),
    ("net_carbs", "Net carbs", "g", [], "Net Carbs, g", None, "info", "macros"),
    ("sugar", "Sugars", "g", ["269", "2000"], "Total Sugars, g", None, "info", "carbs"),
    ("added_sugar", "Added sugars", "g", ["539"], "Added Sugars, g", 50, "limit", "carbs"),
    ("sat_fat", "Saturated fat", "g", ["606"], "Saturated Fat, g", 20, "limit", "fats"),
    ("trans_fat", "Trans fat", "g", ["605"], "Trans Fat, g", 2, "limit", "fats"),
    ("mono_fat", "Monounsaturated fat", "g", ["645"], "Monounsaturated Fat, g", None, "info", "fats"),
    ("poly_fat", "Polyunsaturated fat", "g", ["646"], "Polyunsaturated Fat, g", None, "info", "fats"),
    ("omega3", "Omega-3s", "mg", [], "Omega-3s, mg", 1100, "goal", "fats"),
    ("cholesterol", "Cholesterol", "mg", ["601"], "Cholesterol, mg", 300, "limit", "fats"),
    ("sodium", "Sodium", "mg", ["307"], "Sodium, mg", 2300, "limit", "minerals"),
    ("potassium", "Potassium", "mg", ["306"], "Potassium, mg", 4700, "goal", "minerals"),
    ("calcium", "Calcium", "mg", ["301"], "Calcium, mg", 1300, "goal", "minerals"),
    ("iron", "Iron", "mg", ["303"], "Iron, mg", 18, "goal", "minerals"),
    ("magnesium", "Magnesium", "mg", ["304"], "Magnesium, mg", 420, "goal", "minerals"),
    ("zinc", "Zinc", "mg", ["309"], "Zinc, mg", 11, "goal", "minerals"),
    ("phosphorus", "Phosphorus", "mg", ["305"], "Phosphorus, mg", 1250, "goal", "minerals"),
    ("selenium", "Selenium", "mcg", ["317"], "Selenium, mcg", 55, "goal", "minerals"),
    ("copper", "Copper", "mg", ["312"], "Copper, mg", 0.9, "goal", "minerals"),
    ("manganese", "Manganese", "mg", ["315"], "Manganese, mg", 2.3, "goal", "minerals"),
    ("vit_a", "Vitamin A", "IU", ["318"], "Vitamin A, iu", 3000, "goal", "vitamins"),
    ("vit_c", "Vitamin C", "mg", ["401"], "Vitamin C, mg", 90, "goal", "vitamins"),
    ("vit_d", "Vitamin D", "IU", ["324"], "Vitamin D, iu", 800, "goal", "vitamins"),
    ("vit_e", "Vitamin E", "mg", ["323"], "Vitamin E, mg", 15, "goal", "vitamins"),
    ("vit_k", "Vitamin K", "mcg", ["430"], "Vitamin K, mcg", 120, "goal", "vitamins"),
    ("thiamin", "Thiamin (B1)", "mg", ["404"], "Thiamin, mg", 1.2, "goal", "vitamins"),
    ("riboflavin", "Riboflavin (B2)", "mg", ["405"], "Riboflavin, mg", 1.3, "goal", "vitamins"),
    ("niacin", "Niacin (B3)", "mg", ["406"], "Niacin, mg", 16, "goal", "vitamins"),
    ("pantothenic", "Pantothenic acid (B5)", "mg", ["410"], "Pantothenic Acid, mg", 5, "goal", "vitamins"),
    ("b6", "Vitamin B6", "mg", ["415"], "Vitamin B-6, mg", 1.7, "goal", "vitamins"),
    ("folate", "Folate", "mcg", ["417", "435"], "Folate, mcg", 400, "goal", "vitamins"),
    ("b12", "Vitamin B12", "mcg", ["418"], "Vitamin B-12, mcg", 2.4, "goal", "vitamins"),
    ("choline", "Choline", "mg", ["421"], "Choline, mg", 550, "goal", "vitamins"),
    ("caffeine", "Caffeine", "mg", ["262"], "Caffeine, mg", 400, "limit", "other"),
    ("alcohol", "Alcohol", "g", ["221"], "Alcohol, g", None, "info", "other"),
]

KEYS = [n[0] for n in NUTRIENTS]
META = {n[0]: {"label": n[1], "unit": n[2], "target": n[5], "kind": n[6], "group": n[7]} for n in NUTRIENTS}
USDA_NUMBER = {num: n[0] for n in NUTRIENTS for num in n[3]}
MND_COLUMN = {n[4]: n[0] for n in NUTRIENTS}
MACROS = ["kcal", "carbs", "protein", "fat", "fiber"]  # also kept as columns on foods
GROUP_ORDER = ["macros", "carbs", "fats", "minerals", "vitamins", "other"]
# USDA reports omega-3s as separate fatty acids in grams: ALA 18:3 n-3, EPA 20:5, DPA 22:5, DHA 22:6
USDA_OMEGA3_G = {"851", "629", "631", "621"}


def from_usda(food_nutrients: list[dict]) -> dict:
    out, omega3 = {}, 0.0
    for x in food_nutrients:
        num, val = str(x.get("nutrientNumber")), x.get("value")
        if val is None:
            continue
        if num in USDA_OMEGA3_G:
            omega3 += float(val) * 1000
        key = USDA_NUMBER.get(num)
        if key and key not in out:
            out[key] = float(val)
    if omega3:
        out["omega3"] = omega3
    if "carbs" in out and "net_carbs" not in out:
        out["net_carbs"] = max(0.0, out["carbs"] - out.get("fiber", 0))
    return out


def targets(settings: dict) -> dict:
    out = {}
    for k in KEYS:
        v = settings.get(f"target_{k}")
        if k == "protein" and not v:
            v = settings.get("target_protein_g")
        if k == "fiber" and not v:
            v = settings.get("target_fiber_g")
        if k == "kcal" and not v:
            v = settings.get("target_kcal")
        try:
            out[k] = float(v) if v not in (None, "") else META[k]["target"]
        except ValueError:
            out[k] = META[k]["target"]
    return out
