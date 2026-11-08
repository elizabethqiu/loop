"""Tiny JSON API + static UI on the standard library. No auth: binds to localhost by default."""
import json
import re
import threading
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from http.cookies import SimpleCookie

from . import activity, ai, auth, search, bodyclock, build, community, cycle, home, review, scores, connectors, consume, energy, food, health, history, music, predict, profile_doc, runner
from .sources import gcal
from .db import rows, settings, upsert_daily, upsert_item
from .sources import garmin, lastfm, mac

STATIC = Path(__file__).parent / "static"
LOCK = threading.RLock()  # re-entrant: auth checks run inside locked sections too


def _day(q) -> date:
    v = (q.get("date") or [None])[0]
    return date.fromisoformat(v) if v else date.today()


def today_bundle(con, d: date) -> dict:
    s = settings(con)
    days = rows(con, "SELECT * FROM daily WHERE date <= ? ORDER BY date", (d.isoformat(),))
    ev = rows(con, "SELECT * FROM events WHERE substr(at,1,10) = ? ORDER BY at", (d.isoformat(),))
    caffeine = [e for e in ev if e["kind"] == "caffeine"]
    wake = next((e["at"] for e in ev if e["kind"] == "wake"), None)
    meals = rows(con, "SELECT * FROM events WHERE kind='meal' AND at >= ? ORDER BY at",
                 ((d - timedelta(days=14)).isoformat(),)) + food.meal_times(con, d - timedelta(days=14))
    sleep = predict.predict_sleep(days, d, caffeine + food.caffeine_events(con, d), s, wake_event=wake)
    if sleep.get("ok"):
        # The sleep plan (energy.py) is the source of truth for tonight's wake time; the realistic
        # bedtime and the reasons still come from predict.py.
        en = energy.bundle(days, d, s, caffeine + food.caffeine_events(con, d), wake)
        p0 = en["plan"][0]
        sleep.update(realistic_bedtime=sleep["bedtime"], bedtime=p0["bedtime"], bedtime_iso=p0["bedtime_iso"],
                     wake_anchor=p0["wake"], wake_anchor_iso=p0["wake_iso"], sleep_opportunity_h=p0["sleep_h"],
                     goals_set=en["goals_set"],
                     reasons=en["tonight_reasons"] + ([p0["note"][0].upper() + p0["note"][1:] + "."] if p0["note"] else [])
                     + [r for r in sleep["reasons"] if r.startswith(("Your sleep onset", "Get outside", "~"))])
    focus = consume.focus_by_hour_since_wake(con, days, d)
    work = predict.work_readiness(days, d, s, sleep.get("wake_anchor_iso"), focus)
    return {
        "date": d.isoformat(),
        "sleep": sleep,
        "eating": {**predict.meal_regularity(meals, d, s), "three_logged": food.days_with_three_meals_logged(con, d)},
        "work": work,
        "trends": predict.health_trends(days, d, s),
        "events": ev,
        "recent_days": days[-14:],
        "settings": public_settings(s),
        "tracker_last_seen": mac.last_seen(con),
        "sources": runner.sources_status(con),
        "now": mac.current(con),
        "today_totals": consume.day_totals(con, d),
    }


def energy_bundle(con, d: date) -> dict:
    s = settings(con)
    days = rows(con, "SELECT * FROM daily WHERE date <= ? ORDER BY date", (d.isoformat(),))
    ev = rows(con, "SELECT * FROM events WHERE substr(at,1,10) = ? ORDER BY at", (d.isoformat(),))
    caffeine = [e for e in ev if e["kind"] == "caffeine"] + food.caffeine_events(con, d)
    wake = next((e["at"] for e in ev if e["kind"] == "wake"), None)
    en = energy.bundle(days, d, s, caffeine, wake)
    en["night"] = bodyclock.last_night(con, date.fromisoformat(en["date"]), days, s)
    return en


def _named(rows_, key="source"):
    from . import names
    for r in rows_:
        if r.get("group"):
            continue  # e.g. the combined Music row already has its name
        r["name"] = "Music" if r.get(key) == "music" else names.pretty(r.get(key))
    return rows_


def consume_bundle(con, d: date) -> dict:
    from . import names
    names.load(con)
    b = {
        "date": d.isoformat(),
        "timeline": consume.timeline(con, d),
        "plate": consume.plate(con, d),
        "review": consume.review_queue(con, d),
        "cuts": consume.cut_list(con, d),
        "influence": consume.influence_summary(con, d),
        "tags": consume.TAGS, "modes": consume.MODES, "depths": consume.DEPTHS,
        "tracker_last_seen": mac.last_seen(con),
    }
    _named(b["timeline"]); _named(b["plate"]["top_sources"]); _named(b["review"])
    _named(b["cuts"]["candidates"]); _named(b["cuts"]["cut"]); _named(b["influence"])
    return b


def food_get(con, path: str, q) -> dict:
    arg = lambda k: (q.get(k) or [""])[0]
    if path == "/api/food/recipes":
        from . import recipes
        return {"recipes": recipes.all_recipes(con), "suggestions": recipes.suggestions(con, _day(q))}
    if path == "/api/food/day":
        d, st = _day(q), settings(con)
        out = food.day(con, d, st)
        from . import dishes
        out["dishes"] = dishes.names_for(con, d, out["entries"])   # a name for each meal, from simple rules on this Mac
        # dashboard extras: this week's calories (Sun–Sat), exercise, steps, water
        sun = d - timedelta(days=(d.weekday() + 1) % 7)
        kc = history.series(con, "kcal", sun, sun + timedelta(days=6), st)
        out["week"] = [{"date": (sun + timedelta(days=k)).isoformat(), "kcal": round(kc.get((sun + timedelta(days=k)).isoformat(), 0))}
                       for k in range(7)]
        row = con.execute("SELECT active_kcal FROM daily WHERE date=?", (d.isoformat(),)).fetchone()
        wk = con.execute("SELECT COALESCE(SUM(kcal),0) FROM workouts WHERE substr(at,1,10)=?", (d.isoformat(),)).fetchone()[0]
        out["exercise_kcal"] = round(max((row[0] or 0) if row else 0, wk or 0))
        out["steps"] = round(history.series(con, "steps", d, d, st).get(d.isoformat(), 0))
        out["water"] = history.series(con, "water", d, d, st).get(d.isoformat(), 0)
        out["water_target"], _ = history.goal_for("water", st)
        out["units"] = st.get("units") or "imperial"
        # no budget set yet: use loop's suggestion from your profile (TDEE), clearly marked as suggested
        tg = out["targets"]
        if not tg.get("kcal"):
            try:
                sug = health.bundle(con, st, d).get("targets") or {}
            except Exception:
                sug = {}
            if sug.get("kcal"):
                tg["kcal"] = sug["kcal"]
                tg["carbs"] = tg.get("carbs") or sug.get("carbs")
                tg["fat"] = tg.get("fat") or sug.get("fat")
                out["budget_suggested"] = True
        out["score"] = food.food_score(out, st)
        out["quotes_on"] = st.get("food_quotes", "on") != "off"
        try:   # your long-term food goal, if one is linked to a food measure
            from . import lifegoals
            fg = [g for g in lifegoals.overview(con, d, st)["goals"]
                  if g.get("metric") in ("kcal", "protein", "fiber", "carbs", "fat", "sugar", "added_sugar", "sodium", "meals_good", "water")]
            out["food_goal"] = ({k: fg[0].get(k) for k in ("id", "what", "why", "days_left", "metric", "target")} | {"progress": fg[0].get("progress")}) if fg else None
        except Exception:
            out["food_goal"] = None
        return out
    if path == "/api/food/recent_meals":
        return {"meals": food.recent_meals(con, _day(q))}
    if path == "/api/food/trends":
        return food.trends(con, _day(q), settings(con), int(arg("days") or 30))
    if path == "/api/food/search":
        term = arg("q").strip()
        local = food.search_local(con, term)
        out = {"local": local, "online": [], "online_error": None}
        if arg("online") == "1" and len(term) >= 3:
            try:
                seen = {f["key"] for f in local}
                out["online"] = [f for f in food.search_usda(con, term) if f["key"] not in seen]
            except food.FoodError as e:
                out["online_error"] = str(e)
        return out
    if path == "/api/food/barcode":
        code = arg("code")
        hit = con.execute("SELECT * FROM foods WHERE barcode=? OR ltrim(barcode,'0')=ltrim(?,'0')",
                          (code, code)).fetchone()
        if hit:
            return {"food": dict(hit)}
        try:
            return {"food": food.lookup_barcode(con, code)}
        except food.FoodError as e:
            return {"food": None, "error": str(e)}
    raise KeyError(path)


def handle_post(con, path: str, body: dict) -> dict:
    now = datetime.now().isoformat(timespec="minutes")
    if path == "/api/food/log":
        fid = body.get("food_id") or food.upsert_food(con, body["food"])
        if body.get("grp"):
            con.execute("UPDATE foods SET grp=? WHERE id=?", (body["grp"], fid))
        at = body.get("at")
        if body.get("meal") and at:  # everything in a meal shares one time
            at = food.meal_time(con, at[:10], body["meal"]) or at
        food.log(con, int(fid), grams=float(body["grams"]) if body.get("grams") else None,
                 qty=float(body["qty"]) if body.get("qty") else None, at=at, meal=body.get("meal"))
        con.commit()
        return {"ok": True, "food_id": int(fid)}
    elif path == "/api/food/copy_meal":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.get("date", "")) or not re.fullmatch(r"\d{2}:\d{2}", body.get("time", "")):
            raise ValueError("bad date or time")
        if body.get("meal") not in (*food.MEALS, None, "", "same"):
            raise ValueError("bad meal")
        meal_ = body["meal"] if body.get("meal") in food.MEALS else None      # "same" / none: each food keeps its meal
        if body.get("move"):
            n = food.move_entries(con, body.get("ids") or [], body["date"], meal_, body["time"])
        else:
            n = food.copy_entries(con, body.get("ids") or [], body["date"], meal_, body["time"])
        con.commit()
        return {"ok": True, "logged": n}
    elif path.startswith("/api/food/recipe/"):
        from . import recipes
        act = path.rsplit("/", 1)[1]
        if act == "save":
            return {"ok": True, "id": recipes.save(con, body.get("name"), body.get("items") or [], body.get("meal"), body.get("id"))}
        if act == "log":
            return {"ok": True, "logged": recipes.log(con, int(body["id"]), body["at"], body["meal"], body.get("items"), bool(body.get("keep")))}
        if act == "dismiss":
            recipes.dismiss(con, str(body["sig"]))
        elif act == "delete":
            recipes.delete(con, int(body["id"]))
    elif path == "/api/food/custom":
        fid = food.custom_food(con, body)
        if body.get("favorite"):
            con.execute("UPDATE foods SET favorite=1 WHERE id=?", (fid,))
        con.commit()
        return {"ok": True, "food_id": fid}
    elif path == "/api/food/favorite":
        fid = body.get("food_id") or food.upsert_food(con, body["food"])
        con.execute("UPDATE foods SET favorite=? WHERE id=?", (1 if body.get("on", True) else 0, fid))
        con.commit()
        return {"ok": True, "food_id": fid}
    elif path == "/api/food/group":
        con.execute("UPDATE foods SET grp=? WHERE id=?", (body["grp"], body["food_id"]))
    elif path == "/api/food/entry":
        if body.get("delete"):
            con.execute("DELETE FROM food_log WHERE id=?", (body["id"],))
        else:
            if body.get("amount") is not None:
                food.update_entry(con, int(body["id"]), float(body["amount"]))
            if body.get("meal"):
                food.move_entry(con, int(body["id"]), body["meal"])
    elif path == "/api/food/meal_time":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.get("date", "")) or not re.fullmatch(r"\d{2}:\d{2}", body.get("time", "")):
            raise ValueError("bad date or time")
        food.set_meal_time(con, body["date"], body["meal"], body["time"])
    elif path == "/api/focus/start":
        from . import focus
        return focus.start(con, int(body.get("minutes") or 25))
    elif path == "/api/focus/break_end":
        con.execute("DELETE FROM settings WHERE key='focus_break_until'")
        con.commit()
        return {"ok": True}
    elif path == "/api/focus/finish":
        from . import focus
        r = focus.finish(con, bool(body.get("completed")))
        if body.get("break_min"):
            until = (datetime.now() + timedelta(minutes=int(body["break_min"]))).isoformat(timespec="seconds")
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('focus_break_until', ?)", (until,))
            con.commit()
        return r
    elif path == "/api/nudge":
        from . import nudge
        return nudge.answer(con, body.get("id"), body.get("action") or "dismiss")
    elif path == "/api/feedback":
        from . import feedback
        return feedback.submit(con, body)
    elif path == "/api/about":
        from . import about
        return about.submit(con, body)
    elif path == "/api/about/stop":
        from . import about
        return about.stop_sharing(con)
    elif path == "/api/about/delete":
        from . import about
        return about.delete(con)
    elif path == "/api/goals/raise":
        from . import goals
        return goals.apply(con, body["metric"], body["action"], body.get("new"), body.get("old"))
    elif path == "/api/review/rate":
        return review.rate(con, date.fromisoformat(body["date"]), settings(con), int(body["rating"]), body.get("note"))
    elif path == "/api/review/dish":
        from . import dishes
        d_ = date.fromisoformat(body["date"])
        if body.get("meal") not in food.MEALS:
            raise ValueError("bad meal")
        ids = [r[0] for r in con.execute("SELECT food_id FROM food_log WHERE substr(at,1,10)=? AND meal=?", (d_.isoformat(), body["meal"]))]
        dishes.rename(con, d_, body["meal"], str(body.get("name") or ""), ids)
        con.commit()
        return {"ok": True}
    elif path == "/api/review/note":
        return {"ok": True, "saved": review.save_note(con, date.fromisoformat(body["date"]), str(body.get("note") or ""))}
    elif path.startswith("/api/lifegoals/"):
        from . import lifegoals
        act = path.rsplit("/", 1)[1]
        try:
            if act == "save":
                return {"ok": True, "id": lifegoals.save(con, body)}
            if act == "status":
                lifegoals.set_status(con, int(body["id"]), str(body["status"]))
            elif act == "check":
                lifegoals.check_in(con, int(body["id"]), date.fromisoformat(body["date"]), str(body.get("kind") or "weekly"),
                                   str(body["result"]), body.get("note"))
        except lifegoals.GoalError as e:
            raise ValueError(str(e))
    elif path == "/api/home/arrange":
        try:
            return ai.arrange(settings(con), str(body.get("instruction") or ""), body.get("cards") or [])
        except ai.AIError as e:
            raise ValueError(str(e))
    elif path == "/api/catchup/dismiss":
        from . import catchup
        catchup.dismiss(con, date.fromisoformat(body["date"]), str(body["key"]), body.get("until"))
    elif path == "/api/practice":
        home.toggle_practice(con, date.fromisoformat(body["date"]), str(body["label"]), bool(body.get("on")))
    elif path == "/api/review/vote":
        review.vote(con, date.fromisoformat(body["date"]), str(body["key"]), int(body["vote"]))
    elif path == "/api/private/day":
        cycle.log_day(con, date.fromisoformat(body["date"]), body.get("flow"), body.get("symptoms"), body.get("note"))
    elif path == "/api/private/log":
        if body.get("delete"):
            con.execute("DELETE FROM health_log WHERE id=?", (int(body["id"]),))
        else:
            cycle.add_log(con, date.fromisoformat(body["date"]), body["kind"], body.get("value") or "", body.get("note"))
    elif path == "/api/private/all":
        if body.get("confirm") != "delete":
            raise ValueError("send confirm: 'delete' to erase all private health data")
        cycle.delete_all(con)
        return {"ok": True}
    elif path == "/api/water":
        history.add_water(con, date.fromisoformat(body["date"]), add=body.get("add"), set_to=body.get("set"))
    elif path == "/api/workout":
        history.save_workout(con, body)
    elif path == "/api/measure":
        history.set_measure(con, date.fromisoformat(body["date"]), body["kind"], body.get("value"))
    elif path == "/api/steps":
        total = history.add_steps(con, date.fromisoformat(body["date"]), body.get("add") or 0)
        con.commit()
        return {"ok": True, "total": total}
    elif path == "/api/journal":
        history.set_journal(con, date.fromisoformat(body["date"]), body.get("text", ""))
    elif path == "/api/profile":
        if body.get("profile_birthdate"):
            import re as _re
            b = str(body["profile_birthdate"])[:7]
            if not _re.fullmatch(r"(19|20)\d\d-(0[1-9]|1[0-2])", b):
                raise ValueError("Birth month should look like 1995-06.")
            body["profile_birthdate"] = b  # month and year only
        allowed = {"profile_sex", "profile_birthdate", "profile_height_in", "profile_activity",
                   "profile_goal_weight_lb", "profile_rate_lb_week", "profile_protein_per_lb", "profile_fat_pct", "units"}
        if body.get("units") not in (None, "imperial", "metric"):
            raise ValueError("units must be imperial or metric")
        profile_doc.save_settings(con, {k: str(v).strip() for k, v in body.items() if k in allowed})
        if body.get("weight_lb"):
            history.set_measure(con, date.today(), "weight", body["weight_lb"])
    elif path == "/api/health/apply":
        b = health.bundle(con, settings(con), date.today())
        if not b["targets"]:
            raise ValueError("Fill in your profile first (sex, birth month, height, weight).")
        health.apply_targets(con, b["targets"])
        con.commit()
        return {"ok": True, "targets": b["targets"]}
    elif path == "/api/avatar":
        data = community.convert_photo(body["raw"]) if body.get("raw") else body.get("data")
        return {"ok": True, **community.save_avatar(con, data)}
    elif path == "/api/together/join_hub":
        return community.join_hub(con, body.get("url", ""), body.get("name", ""))
    elif path == "/api/together/leave_hub":
        community.leave_hub(con)
    elif path.startswith("/api/together/"):
        return {"ok": True, **(community.act(con, path.removeprefix("/api/together/"), body) or {})}
    elif path == "/api/project":
        build.save_project(con, body)
    elif path == "/api/brain/mark":
        from . import brain
        return brain.mark(con, body.get("node", ""), bool(body.get("shareable")))
    elif path == "/api/brain/curius/connect":
        from .sources import curius
        try:
            who = curius.connect(con, body.get("profile", ""))
            got = curius.sync(con, max_pages=0)          # the whole list now (one request); highlights follow in the background
        except curius.CuriusError as e:
            raise ValueError(str(e))
        return {**who, **got}
    elif path == "/api/brain/curius/disconnect":
        from .sources import curius
        return curius.disconnect(con)
    elif path == "/api/brain/forget_claude":
        from . import brain
        return brain.forget_claude(con)
    elif path == "/api/thought":
        build.save_thought(con, body)
    elif path == "/api/todo":
        from . import workload
        d_ = date.fromisoformat(body["date"]) if body.get("date") else date.today()
        if "done" in body and body.get("id"):
            workload.complete(con, int(body["id"]), bool(body["done"]), d_)
        elif body.get("delete"):
            build.save_todo(con, body)
        else:
            try:
                return {"ok": True, "id": workload.add_todo(con, body, d_, settings(con), enforce=not body.get("force"))}
            except ValueError as e:
                if str(e) == "LOU_TOO_MUCH":
                    return {"ok": False, "lou": workload.check(con, d_, settings(con))}
                raise
    elif path == "/api/todo/triage":
        from . import workload
        workload.triage(con, int(body["id"]), str(body["action"]), date.fromisoformat(body["date"]) if body.get("date") else date.today(), body.get("on"))
    elif path == "/api/note":
        from . import workload
        d_ = date.fromisoformat(body["date"]) if body.get("date") else date.today()
        if body.get("id"):
            workload.process_note(con, int(body["id"]), str(body.get("action") or "delete"), d_, settings(con), body.get("quadrant"))
        else:
            try:
                return {"ok": True, "id": workload.add_note(con, body.get("text") or "", settings(con))}
            except ValueError as e:
                if str(e).startswith("LOU_NOTES_FULL"):
                    return {"ok": False, "full": int(str(e).split(":")[1])}
                raise
    elif path == "/api/calendar/hide":
        gcal.hide(con, body["uid"], body["start"], body.get("hidden", True))
    elif path == "/api/connect/gcal":
        u = (body.get("url") or "").strip()
        if not u:
            from . import keychain
            keychain.forget_secret(con, "gcal_ics_url")
            con.execute("DELETE FROM calendar_events")
            con.commit()
            return {"ok": True, "disconnected": True}
        try:
            events = gcal.parse(gcal.fetch(u))
        except gcal.CalendarError as e:
            raise ValueError(str(e))
        if not events and "BEGIN:VCALENDAR" not in gcal.fetch(u):
            raise ValueError("That address didn't return a calendar. Copy the 'Secret address in iCal format'.")
        from . import keychain
        keychain.save_secret(con, "gcal_ics_url", u)   # your private calendar link is a secret: Keychain
        con.commit()
        return {"ok": True, **gcal.sync(con)}
    elif path == "/api/connect/zotero":
        from .sources import zotero
        if body.get("disconnect"):
            zotero.disconnect(con)
            return {"ok": True}
        try:
            r = zotero.connect(con, body.get("key") or "")
        except zotero.ZoteroError as e:
            raise ValueError(str(e))
        return {"ok": True, "username": r["username"], "items": r["items"]}
    elif path == "/api/connect/lastfm":
        user, key = (body.get("user") or "").strip(), (body.get("key") or "").strip()
        if not (user and key):
            raise ValueError("Enter your Last.fm username and API key.")
        try:
            info = lastfm.verify(user, key)
        except lastfm.LastfmError as e:
            raise ValueError(f"Last.fm said: {e}")
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('lastfm_user',?)", (user,))
        from . import keychain
        keychain.save_secret(con, "lastfm_api_key", key)
        con.commit()
        result = lastfm.sync(con, days=14)
        return {"ok": True, "user": info["name"], "plays": result["scrobbles"]}
    elif path == "/api/brief/refresh":
        try:
            d = date.fromisoformat(body["date"]) if body.get("date") else date.today()
            return {**ai.brief(con, settings(con), d, refresh=True), "ai": ai.status(settings(con))}
        except ai.AIError as e:
            raise ValueError(str(e))
    elif path == "/api/ai/config":
        prov = body.get("provider") or "anthropic"
        if prov not in ai.PROVIDERS:
            raise ValueError("Unknown AI provider.")
        values = {"ai_provider": prov, "ai_model": (body.get("model") or "").strip(),
                  "ai_base_url": (body.get("base_url") or "").strip()}
        if body.get("api_key"):
            from . import keychain
            keychain.save_secret(con, "ai_api_key", body["api_key"].strip())
        for k, v in values.items():
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (k, v))
        con.commit()
        return {"ok": True, **ai.status(settings(con))}
    elif path == "/api/ai/key":
        key = (body.get("key") or "").strip()
        if key and not key.startswith("sk-ant-"):
            raise ValueError("That doesn't look like an Anthropic API key (they start with sk-ant-).")
        from . import keychain
        if key:
            keychain.save_secret(con, "anthropic_api_key", key)   # macOS Keychain; the database only notes it's there
        else:
            keychain.forget_secret(con, "anthropic_api_key")
        con.commit()
        return {"ok": True, **ai.status(settings(con))}
    elif path == "/api/ask":
        try:
            d = date.fromisoformat(body["date"]) if body.get("date") else date.today()
            return {"ok": True, **ai.ask(con, settings(con), d, body["question"], body.get("history"))}
        except ai.AIError as e:
            raise ValueError(str(e))
    elif path == "/api/goal":
        v = body.get("target")
        try:
            health.check_goal(con, settings(con), body["metric"], v, bool(body.get("confirm")))
        except health.NeedsConfirm as e:
            return {"ok": False, "confirm": str(e)}
        profile_doc.save_settings(con, {f"target_{body['metric']}": "off" if v == "off" else ("" if v in (None, "") else str(float(v)))})
    elif path == "/api/item":
        mode = body.get("mode") or "reading"
        upsert_item(con, {
            "origin": "manual", "source": body["source"].strip(), "title": body.get("title") or None,
            "mode": mode, "depth": body.get("depth") or "deep",
            "started_at": body.get("started_at") or now, "minutes": float(body.get("minutes") or 0),
            "ext_key": None,
        })
    elif path == "/api/tag":
        if body.get("on", True):
            con.execute("INSERT OR IGNORE INTO tags(source, day, tag) VALUES(?,?,?)",
                        (body["source"], body["day"], body["tag"]))
        else:
            con.execute("DELETE FROM tags WHERE source=? AND day=? AND tag=?",
                        (body["source"], body["day"], body["tag"]))
    elif path == "/api/rule":
        con.execute("INSERT OR IGNORE INTO source_rules(source) VALUES(?)", (body["source"],))
        for k in ("mode", "depth", "status"):
            if body.get(k):
                con.execute(f"UPDATE source_rules SET {k}=? WHERE source=?", (body[k], body["source"]))
        if body.get("status"):
            con.execute("UPDATE source_rules SET changed_at=? WHERE source=?", (now, body["source"]))
    elif path == "/api/event":
        con.execute("INSERT INTO events(kind, at, value, note) VALUES(?,?,?,?)",
                    (body["kind"], body.get("at") or now, body.get("value"), body.get("note")))
    elif path == "/api/event/delete":
        con.execute("DELETE FROM events WHERE id=?", (body["id"],))
    elif path == "/api/item/delete":
        con.execute("DELETE FROM items WHERE id=? AND origin='manual'", (body["id"],))
    elif path == "/api/daily":
        row = {k: body.get(k) for k in ("date", "sleep_start", "sleep_end", "resting_hr")}
        if row["sleep_start"] and row["sleep_end"]:
            row["sleep_minutes"] = (datetime.fromisoformat(row["sleep_end"]) -
                                    datetime.fromisoformat(row["sleep_start"])).total_seconds() / 60
        row["origin"] = "manual"
        upsert_daily(con, row)
    elif path == "/api/settings":
        if "priorities" in body:   # your top priorities become the homepage focus
            from . import tracking
            pr = [p for p in str(body["priorities"]).split(",") if p in tracking.IDS]
            fk = tracking.focus_for(pr)
            if fk:
                body = {**body, "home_focus": ",".join(fk)}
        profile_doc.save_settings(con, body)
    elif path == "/api/sync":
        src = body.get("source")
        for name, _, connected, job in runner.JOBS:
            if name == src:
                runner.run_job(con, name, connected, job)
                st = dict(con.execute("SELECT * FROM sync_status WHERE source=?", (name,)).fetchone())
                if st["ok"] is None:
                    raise ValueError(f"{name} isn't connected yet. Run: python3 -m loop connect {name}")
                if not st["ok"]:
                    raise ValueError(st["message"])
                return {"ok": True, "result": json.loads(st["message"])}
        raise ValueError(f"unknown source {src}")
    else:
        raise KeyError(path)
    con.commit()
    return {"ok": True}


PUBLIC_FILES = {"/login.html", "/login.js", "/style.css", "/icon-192.png", "/icon-512.png", "/manifest.webmanifest"}


SECRET_KEYS = ("api_key", "token", "secret", "password", "client_id", "_after_ms", "anthropic", "ics_url")
WRITE_ONLY = ("feedback_sheet_url", "about_team_url")   # the browser only learns whether these are set


def public_settings(s: dict) -> dict:
    """Settings safe to send to the browser: secrets dropped, write-only values reduced to 'is it set'."""
    out = {k: v for k, v in s.items() if not any(x in k for x in SECRET_KEYS) and k not in WRITE_ONLY}
    for k in WRITE_ONLY:
        out[k + "_set"] = bool(s.get(k))
    return out


def host_ok(host: str) -> bool:
    """Block DNS rebinding: only answer to this machine's own names and IP addresses (plus LOOP_ALLOWED_HOSTS)."""
    import ipaddress
    import os
    h = (host or "").strip().lower()
    h = h[1:h.index("]")] if h.startswith("[") and "]" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    if not h:
        return True   # HTTP/1.0 clients may omit Host
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    extra = [x.strip().lower() for x in os.environ.get("LOOP_ALLOWED_HOSTS", "").split(",") if x.strip()]
    return h == "localhost" or h.endswith((".localhost", ".local", ".ts.net")) or h in extra


_BUILT = {}


def built_site():
    """Load howwebuilt/server.py once (public view, links under /built). None if that folder isn't there."""
    if "mod" not in _BUILT:
        import importlib.util
        path = Path(__file__).resolve().parent.parent / "howwebuilt" / "server.py"
        mod = None
        if path.exists():
            spec = importlib.util.spec_from_file_location("loop_howwebuilt", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.BASE, mod.PRIVATE = "/built", False
            mod.NAV = [n for n in mod.NAV_ALL if n[0] != "/timeline"]
        _BUILT["mod"] = mod
    return _BUILT["mod"]


def make_handler(con, require_auth: bool = True):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATIC), **kw)

        extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                          ".webmanifest": "application/manifest+json", ".js": "text/javascript"}

        def log_message(self, *a):
            pass

        def end_headers(self):
            if getattr(self, "_static", False):  # always check for a newer app version (cheap 304 if unchanged)
                self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def _json(self, obj, code=200, cookie: str | None = None):
            data = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        # ---------------- auth
        def _token(self):
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c[auth.COOKIE].value if auth.COOKIE in c else None

        def _user(self):
            if not require_auth:
                return {"id": 0, "name": "", "username": ""}
            with LOCK:
                return auth.user_for(con, self._token())

        @staticmethod
        def _cookie(token: str, max_age: int = auth.SESSION_DAYS * 86400) -> str:
            return f"{auth.COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"

        def _redirect(self, to: str):
            self.send_response(302)
            self.send_header("Location", to)
            self.end_headers()

        def _built(self, sub: str, q: dict):
            """The "How we built loop" site (howwebuilt/), served here so the footer link works on every device."""
            site = built_site()
            out = site.route(sub, q) if site else None
            if out is None:
                return self._json({"error": "not found"}, 404)
            data = out.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", site.CSP)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _brain_import(self, n: int):
            """Import a claude.ai data export. Only zip / octet-stream bodies: neither is a type a cross-site form can
            send without a CORS preflight, so this stays as CSRF-safe as the JSON-only endpoints."""
            from . import brain
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype not in ("application/zip", "application/octet-stream", "application/x-zip-compressed"):
                return self._json({"error": "send the export as a file"}, 415)
            if not self._user():
                return self._json({"error": "login required"}, 401)
            if n <= 0 or n > 2 * 1024 ** 3:
                return self._json({"error": "the file is empty or too large (2 GB max)"}, 413)
            blob = self.rfile.read(n)
            try:
                with LOCK:
                    return self._json(brain.import_claude(con, blob))
            except brain.ImportError_ as e:
                return self._json({"error": str(e)}, 400)

        def _bad_host(self) -> bool:
            if host_ok(self.headers.get("Host")):
                return False
            self._json({"error": "unknown host (set LOOP_ALLOWED_HOSTS to allow it)"}, 421)
            return True

        def do_GET(self):
            if self._bad_host():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/auth/state":
                with LOCK:
                    u_ = self._user()
                    return self._json({"has_user": auth.has_user(con), "user": u_,
                                       "avatar": bool(u_) and community.has_avatar(con)})
            user = self._user()
            if not user:
                if u.path.startswith("/api/"):
                    return self._json({"error": "login required"}, 401)
                if u.path in ("/", "/index.html"):
                    return self._redirect("/login.html")
                if u.path not in PUBLIC_FILES and not u.path.endswith((".js", ".css")):
                    return self._redirect("/login.html")
            if u.path == "/built" or u.path.startswith("/built/"):
                return self._built(u.path[len("/built"):] or "/", q)
            try:
                with LOCK:
                    if u.path == "/api/today":
                        b = today_bundle(con, _day(q))
                        b["user"] = {**user, "avatar": community.has_avatar(con)}
                        return self._json(b)
                    if u.path == "/api/export":
                        from . import export
                        name, blob = export.build(con, settings(con), date.today(), (q.get("private") or ["0"])[0] == "1")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                        self.send_header("Content-Length", str(len(blob)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return self.wfile.write(blob)
                    if u.path == "/api/version":
                        from . import __version__
                        log = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
                        return self._json({"version": __version__, "changelog": log.read_text() if log.exists() else ""})
                    if u.path == "/api/avatar":
                        av = community.load_avatar(con)
                        if not av:
                            return self._json({"error": "no picture"}, 404)
                        self.send_response(200)
                        self.send_header("Content-Type", av[1])
                        self.send_header("Content-Length", str(len(av[0])))
                        self.send_header("Cache-Control", "private, no-cache")
                        self.end_headers()
                        return self.wfile.write(av[0])
                    if u.path == "/api/together":
                        try:
                            return self._json(community.state(con))
                        except community.HubError as e:
                            return self._json({"connected": True, "hub": community._get(con, "hub_url"), "hub_error": str(e)})
                    if u.path == "/api/together/preview":
                        return self._json(community.share_preview(con, settings(con), _day(q)))
                    if u.path == "/api/books":
                        from .sources import books as books_src
                        return self._json(books_src.summary(settings(con)))
                    if u.path == "/api/brain":
                        from . import brain
                        return self._json(brain.graph(con, _day(q), (q.get("orphans") or ["0"])[0] == "1",
                                                      "curius" if (q.get("source") or [""])[0] == "curius" else "mind"))
                    if u.path == "/api/focusmap":
                        from . import focusmap
                        days_ = max(7, min(91, int((q.get("days") or ["28"])[0])))
                        return self._json(focusmap.build_map(con, settings(con), _day(q), days_))
                    if u.path == "/api/build":
                        return self._json(build.overview(con, settings(con), _day(q), int((q.get("days") or ["7"])[0])))
                    if u.path == "/api/workload":
                        from . import workload
                        d_ = _day(q)
                        return self._json({"lou": workload.check(con, d_, settings(con)), "notes": workload.notes(con)})
                    if u.path == "/api/todos":
                        return self._json(build.todo_list(con, _day(q)))
                    if u.path == "/api/calendar":
                        d_ = _day(q)
                        evs = gcal.day(con, d_)
                        en = energy_bundle(con, d_) if evs else {}
                        return self._json({"connected": gcal.is_connected(con), "events": gcal.flags(evs, en)})
                    if u.path == "/api/scores":
                        d_ = _day(q)
                        s_ = settings(con)
                        return self._json({"health": scores.health_day(con, d_, s_), "work": scores.work_day(con, d_, s_)})
                    if u.path == "/api/review":
                        return self._json(review.bundle(con, _day(q), settings(con)))
                    if u.path == "/api/private/history":
                        return self._json({"health": cycle.history(con), "journal": history.journal_entries(con)})
                    if u.path == "/api/private":
                        return self._json(cycle.bundle(con, _day(q)))
                    if u.path == "/api/focus/plan":
                        from . import focus
                        d_ = _day(q)
                        s_ = settings(con)
                        return self._json(focus.plan(con, d_, s_, energy_bundle(con, d_), datetime.now()))
                    if u.path == "/api/nudge":
                        from . import nudge
                        return self._json(nudge.current(con))
                    if u.path == "/api/zotero":
                        from .sources import zotero
                        return self._json(zotero.summary(con, _day(q)))
                    if u.path == "/api/methods":
                        return self._json(scores.methods())
                    if u.path == "/api/evals":
                        from . import evals
                        return self._json({**evals.profile(con, _day(q), settings(con)), "snapshots": evals.snapshots(con)})
                    if u.path == "/api/lifegoals":
                        from . import lifegoals
                        return self._json(lifegoals.overview(con, _day(q), settings(con)))
                    if u.path == "/api/about":
                        from . import about
                        return self._json(about.overview(con))
                    if u.path == "/api/onboard":
                        from . import tracking
                        st = settings(con)
                        today = date.today().isoformat()
                        mac = con.execute("SELECT COALESCE(SUM((julianday(end)-julianday(start))*1440),0) FROM activity WHERE lane='focus' AND substr(start,1,10)=?", (today,)).fetchone()[0]
                        from . import connectors as _cn
                        cs = [{"key": x.get("key"), "name": x["name"], "connected": bool(x.get("connected")), "message": x.get("message") or ""}
                              for x in _cn.connectors(con, st)["connected"] if x.get("key")]
                        from . import ai as _ai
                        return self._json({"onboarded": st.get("onboarded") == "on", "dev_mode": st.get("dev_mode") == "on", "test_mode": st.get("test_mode") == "on",
                                           "areas": tracking.AREAS, "on": sorted(tracking.on(st)),
                                           "priorities": [p for p in (st.get("priorities") or "").split(",") if p],
                                           "mac_min_today": round(mac), "connectors": cs, "ai": _ai.status(st)})
                    if u.path == "/api/dataflow":
                        from . import egress
                        return self._json(egress.overview(con, settings(con)))
                    if u.path == "/api/mood":
                        return self._json(review.mood_history(con, _day(q)))
                    if u.path == "/api/home":
                        d_ = _day(q)
                        return self._json(home.bundle(con, d_, settings(con), energy_bundle(con, d_)))
                    if u.path == "/api/master":
                        return self._json(history.master(con, _day(q), int((q.get("days") or ["14"])[0]), settings(con)))
                    if u.path == "/api/brief":
                        d_ = _day(q)
                        # what you finished today: shown in the evening summary, rendered here only (never sent to AI)
                        done = [t["text"] for t in build.todo_list(con, d_)["done_today"]]
                        return self._json({**ai.brief(con, settings(con), d_), "ai": ai.status(settings(con)), "done": done[::-1]})
                    if u.path == "/api/connectors":
                        return self._json(connectors.connectors(con, settings(con)))
                    if u.path == "/api/ai/status":
                        return self._json(ai.status(settings(con)))
                    if u.path == "/api/consume":
                        return self._json(consume_bundle(con, _day(q)))
                    if u.path == "/api/energy":
                        return self._json(energy_bundle(con, _day(q)))
                    if u.path == "/api/activity":
                        return self._json(activity.activity(con, _day(q), settings(con)))
                    if u.path == "/api/screen":
                        return self._json(consume.screen_by_app(con, _day(q)))
                    if u.path == "/api/search":          # Cmd/Ctrl+K: local only, nothing leaves this Mac
                        return self._json(search.search(con, (q.get("q") or [""])[0], date.today()))
                    if u.path == "/api/history":
                        a = lambda k, dflt: (q.get(k) or [dflt])[0]
                        return self._json(history.history(con, a("metric", "steps"), _day(q), int(a("days", "30")), settings(con)))
                    if u.path == "/api/goals":
                        return self._json({"goals": history.goals_overview(con, _day(q), settings(con))})
                    if u.path == "/api/health":
                        s_ = settings(con)
                        b = health.bundle(con, s_, _day(q))
                        b["ai_configured"] = ai.is_configured(s_)
                        return self._json(b)
                    if u.path == "/api/you":
                        s_ = settings(con)
                        doc = profile_doc.build(con, s_, _day(q))
                        doc["file"] = profile_doc.write_file(con, doc)
                        return self._json({"doc": doc, "health": health.bundle(con, s_, _day(q)),
                                           "settings": public_settings(s_),
                                           "ai_configured": ai.is_configured(s_)})
                    if u.path == "/api/ai/context":
                        return self._json(ai.context(con, settings(con), _day(q)))
                    if u.path == "/api/track":
                        return self._json(history.track_day(con, _day(q), settings(con)))
                    if u.path == "/api/music/viz":
                        return self._json(music.viz(con, _day(q), int((q.get("days") or ["14"])[0])))
                    if u.path == "/api/music":
                        return self._json(music.summary(con, _day(q)))
                    if u.path.startswith("/api/food/"):
                        return self._json(food_get(con, u.path, q))
            except Exception as e:  # surface errors to the UI instead of a blank page
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            self._static = True
            return super().do_GET()

        def do_POST(self):
            if self._bad_host():
                return
            n = int(self.headers.get("Content-Length") or 0)
            path = urlparse(self.path).path
            if path == "/api/brain/import":   # a claude.ai export zip, sent as raw bytes
                return self._brain_import(n)
            # JSON only: a cross-site form can't send this content type without a CORS preflight (CSRF guard)
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json({"error": "JSON required"}, 415)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                addr = self.client_address[0]
                with LOCK:
                    if path == "/api/auth/setup":
                        auth.create_user(con, body.get("name", ""), body.get("username", ""), body.get("password", ""))
                        token = auth.login(con, body["username"], body["password"], addr, self.headers.get("User-Agent", ""))
                        return self._json({"ok": True}, cookie=self._cookie(token))
                    if path == "/api/auth/login":
                        token = auth.login(con, body.get("username", ""), body.get("password", ""), addr, self.headers.get("User-Agent", ""))
                        return self._json({"ok": True}, cookie=self._cookie(token))
                    if path == "/api/auth/logout":
                        auth.logout(con, self._token())
                        return self._json({"ok": True}, cookie=self._cookie("", 0))
                user = self._user()
                if not user:
                    return self._json({"error": "login required"}, 401)
                with LOCK:
                    if path == "/api/auth/password":
                        auth.change_password(con, user["id"], body.get("old", ""), body.get("new", ""))
                        token = auth.login(con, user["username"], body["new"], addr, self.headers.get("User-Agent", ""))
                        return self._json({"ok": True}, cookie=self._cookie(token))
                    if path == "/api/auth/name":
                        auth.rename(con, user["id"], body.get("name", ""))
                        return self._json({"ok": True})
                    return self._json(handle_post(con, path, body))
            except auth.AuthError as e:
                return self._json({"error": str(e)}, 400)
            except KeyError as e:
                return self._json({"error": f"missing or unknown: {e}"}, 400)
            except (garmin.GarminError, lastfm.LastfmError, community.HubError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def serve(con, host="127.0.0.1", port=8765):
    httpd = ThreadingHTTPServer((host, port), make_handler(con))
    print(f"loop running at http://{host}:{port}")
    httpd.serve_forever()
