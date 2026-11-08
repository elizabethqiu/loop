"""
Export everything loop knows about you as one .zip that's easy for people, spreadsheets and AI to use.

  README.md        what's inside and how to use it
  llms.txt         an index for AI assistants (the llms.txt convention): what each file is
  summary.md       your stats in plain Markdown (averages, goals, sleep, focus): paste into any AI
  report.html      a single readable page, no scripts, opens in any browser
  schema.json      every table: what it is, and its columns
  data/*.jsonl     one JSON object per line, one file per table (AI- and code-friendly)
  data/*.csv       the same tables for spreadsheets
  private/         cycle and health log, only if you tick "include private health"

Never included: passwords and sessions, API keys and tokens, your calendar's secret link, your location.
"""
import csv
import io
import json
import zipfile
from datetime import date, datetime
from html import escape

from . import __version__

SKIP_TABLES = {"users", "sessions", "sync_status", "lab_events", "track_lengths", "music_tags", "app_names",
               "curius_people"}   # people you follow: shown only to you in the brain graph, never exported
PRIVATE_TABLES = {"cycle_days", "health_log", "day_ratings", "about_you"}   # day_ratings: your mood notes; about_you: the optional About you answers
SECRET_HINTS = ("api_key", "token", "secret", "password", "ics_url", "client_id", "client_secret", "anthropic", "geo_lat", "geo_lon",
                "_after_ms", "hub_", "feedback_sheet_url")
DESCRIBE = {
    "daily": "One row per wake date: the sleep that ended that morning plus that day's steps, heart rate and activity.",
    "sleep_detail": "Sleep stages, score and overnight heart rate per night (Garmin).",
    "items": "Everything you consumed or worked on, rolled up: source (app/site/artist), mode, depth (deep/skim/doom), minutes.",
    "activity": "Raw spans from the Mac tracker: which app or site was in front, start/end, title (if titles are on).",
    "events": "Things you logged by hand: meals, caffeine (mg), wake-ups.",
    "food_log": "Each food you logged: when, meal, amount and nutrient totals.",
    "foods": "Your food library (from USDA, Open Food Facts, labels you entered, imports).",
    "measurements": "Daily measurements: water (fl oz), weight (lb), body fat (%), waist and more (in).",
    "workouts": "Exercise sessions.",
    "todos": "Your to-dos, with due dates and when you finished them.",
    "journal": "One journal entry per day.",
    "thoughts": "Notes from Build → Thinking, with their privacy level.",
    "projects": "Projects you track in Build.",
    "calendar_events": "Calendar events loop pulled from your Google Calendar (the last week and next month).",
    "music_plays": "Songs you played (Spotify / Last.fm), with how long you listened.",
    "zotero_items": "Your Zotero library: titles, authors, year, tags, collections.",
    "day_ratings": "How you rated each day (1-5), your notes, and the features loop learned from.",
    "about_you": "Your optional About you answers, and when (if ever) you shared them with the loop team.",
    "goal_changes": "Every time you raised or snoozed a goal.",
    "focus_sessions": "Focus blocks you started: planned length, when they ended, whether you completed them.",
    "nudges": "Focus nudges loop showed you.",
    "feedback": "Messages you sent through Contact.",
    "settings": "Your preferences (secrets removed).",
    "profile_log": "History of changes to your profile and goals.",
    "steps_intraday": "Steps in 15-minute buckets (Garmin).",
    "tags": "How you tagged sources in Reflect (made me think / calm / anxious / noise / changed a decision).",
    "source_rules": "How you reclassified apps and sites (mode, depth, cut).",
    "ai_cache": "AI-written daily summaries.",
    "suggestion_votes": "Suggestions you marked helpful or not for me.",
    "cycle_days": "PRIVATE. Period days: flow, symptoms, notes.",
    "health_log": "PRIVATE. Symptoms, mood, medications, conditions, notes.",
}


def _secret(key: str) -> bool:
    return any(h in key for h in SECRET_HINTS)


def _rows(con, table):
    cur = con.execute(f'SELECT * FROM "{table}"')
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if table == "settings":
        rows = [r for r in rows if not _secret(r["key"])]
    return cols, rows


def build(con, settings: dict, d: date | None = None, include_private: bool = False) -> tuple[str, bytes]:
    from . import ai, scores
    d = d or date.today()
    root = f"loop-export-{d.isoformat()}"
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
              if r[0] not in SKIP_TABLES and not r[0].startswith("sqlite_")]
    buf = io.BytesIO()
    schema, counts = {}, {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            private = t in PRIVATE_TABLES
            if private and not include_private:
                continue
            cols, rows = _rows(con, t)
            folder = "private" if private else "data"
            z.writestr(f"{root}/{folder}/{t}.jsonl", "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))
            s = io.StringIO()
            w = csv.DictWriter(s, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
            z.writestr(f"{root}/{folder}/{t}.csv", s.getvalue())
            schema[t] = {"file": f"{folder}/{t}.jsonl", "description": DESCRIBE.get(t, ""), "columns": cols, "rows": len(rows)}
            counts[t] = len(rows)
        z.writestr(f"{root}/schema.json", json.dumps({"exported_at": datetime.now().isoformat(timespec="seconds"),
                                                       "loop_version": __version__, "tables": schema}, indent=2))
        # summaries
        try:
            ctx = ai.context(con, settings, d)
        except Exception:
            ctx = {}
        h, w = scores.health_day(con, d, settings), scores.work_day(con, d, settings)
        z.writestr(f"{root}/summary.md", summary_md(ctx, h, w, d))
        z.writestr(f"{root}/report.html", report_html(con, ctx, h, w, d, counts))
        z.writestr(f"{root}/llms.txt", llms_txt(schema, d))
        z.writestr(f"{root}/README.md", readme(schema, d, include_private))
    return f"{root}.zip", buf.getvalue()


def summary_md(ctx: dict, h: dict, w: dict, d: date) -> str:
    out = [f"# My loop summary ({d.isoformat()})", "",
           f"- Health score today: {h.get('score')} / 100", f"- Work score today: {w.get('score')} / 100 "
           f"({w.get('deep_min')} min deep, {w.get('light_min')} light, {w.get('nonproductive_min')} nonproductive)", ""]
    av = ctx.get("averages_last_30_days") or {}
    if av:
        out += ["## 30-day averages", "", "| Metric | 30-day avg | This week | Last week | Days logged |", "|---|---|---|---|---|"]
        out += [f"| {k} | {v.get('avg_30d')} {v.get('unit', '')} | {v.get('this_week')} | {v.get('last_week')} | {v.get('logged_days')} |" for k, v in av.items()]
        out.append("")
    g = ctx.get("goals") or []
    if g:
        out += ["## Goals", "", "| Goal | Target | Days met this week | Streak |", "|---|---|---|---|"]
        out += [f"| {x['goal']} | {x['kind']} {x['target']} | {x['days_met_this_week']} | {x['streak']} |" for x in g]
        out.append("")
    sl = ctx.get("sleep") or {}
    if sl:
        out += ["## Sleep", "", f"- Need: {sl.get('need_h')} h · debt: {sl.get('debt_h')} h ({sl.get('debt_level')})",
                f"- Tonight's plan: bed {(sl.get('tonight_plan') or {}).get('bed')}, up {(sl.get('tonight_plan') or {}).get('wake')}", ""]
    st = ctx.get("screen_time_this_week") or {}
    if st:
        out += ["## Screen time this week", "", f"- Share: {st.get('pct')}", f"- Top sources: {', '.join(st.get('top_sources') or [])}", ""]
    out += ["Generated by loop. Full data is in data/ (see schema.json)."]
    return "\n".join(out) + "\n"


def report_html(con, ctx: dict, h: dict, w: dict, d: date, counts: dict) -> str:
    days = [dict(r) for r in con.execute("SELECT date, sleep_minutes, steps, resting_hr, active_minutes FROM daily ORDER BY date DESC LIMIT 30")]
    rows = "".join(f"<tr><td>{escape(r['date'])}</td><td>{round(r['sleep_minutes'] / 60, 1) if r['sleep_minutes'] else ''}</td>"
                   f"<td>{r['steps'] or ''}</td><td>{r['resting_hr'] or ''}</td><td>{r['active_minutes'] or ''}</td></tr>" for r in days)
    goals = "".join(f"<tr><td>{escape(str(g['goal']))}</td><td>{escape(str(g['kind']))} {escape(str(g['target']))}</td>"
                    f"<td>{escape(str(g['days_met_this_week']))}</td><td>{escape(str(g['streak']))}</td></tr>" for g in ctx.get("goals") or [])
    files = "".join(f"<li><code>{escape(t)}</code>: {n:,} rows</li>" for t, n in sorted(counts.items()))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>My loop data · {d.isoformat()}</title>
<style>body{{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;color:#2a221b;background:#f4efe7}}
h1{{font-size:26px}}h2{{font-size:18px;margin-top:32px}}table{{border-collapse:collapse;width:100%;background:#fffcf7}}
td,th{{padding:6px 10px;border-bottom:1px solid #e8dfd2;text-align:left;font-variant-numeric:tabular-nums}}code{{background:#efe7db;padding:1px 5px;border-radius:4px}}
.big{{display:flex;gap:32px}}.big b{{font-size:30px;display:block}}</style></head><body>
<h1>My loop data</h1><p>Exported {d.isoformat()} from loop {escape(__version__)}. Everything here came from my own computer.</p>
<div class="big"><div><b>{h.get('score') if h.get('score') is not None else '–'}</b>health score today</div>
<div><b>{w.get('score') if w.get('score') is not None else '–'}</b>work score today</div>
<div><b>{w.get('deep_min')}</b>minutes of deep work</div></div>
<h2>Goals</h2><table><tr><th>Goal</th><th>Target</th><th>Days met this week</th><th>Streak</th></tr>{goals}</table>
<h2>Last 30 days</h2><table><tr><th>Date</th><th>Sleep (h)</th><th>Steps</th><th>Resting HR</th><th>Active min</th></tr>{rows}</table>
<h2>Files in this export</h2><ul>{files}</ul>
<p>Data is in <code>data/</code> as JSON Lines and CSV; <code>schema.json</code> describes every table; <code>summary.md</code> and
<code>llms.txt</code> are ready to give an AI assistant.</p></body></html>"""


def llms_txt(schema: dict, d: date) -> str:
    lines = [f"# loop export ({d.isoformat()})", "",
             "> A person's own data from loop, a local-first tracker of what they consume (screens, music, food), how they sleep and move, "
             "and what they build. Start with summary.md for an overview, then schema.json for the tables.", "",
             "## Overview", "- [summary.md](summary.md): averages, goals, sleep and screen time in Markdown",
             "- [schema.json](schema.json): every table with its description and columns", "", "## Data"]
    lines += [f"- [{v['file']}]({v['file']}): {v['description'] or t} ({v['rows']} rows)" for t, v in sorted(schema.items())]
    lines += ["", "## Notes", "- Times are local, ISO 8601 (YYYY-MM-DDTHH:MM).", "- Weight is stored in lb, lengths in inches, water in fl oz.",
              "- depth: deep = focused/productive, skim = light, doom = nonproductive."]
    return "\n".join(lines) + "\n"


def readme(schema: dict, d: date, include_private: bool) -> str:
    return f"""# Your loop data ({d.isoformat()})

Everything loop stored about you, exported from your own computer.

- **report.html**: open it in a browser for a readable overview.
- **summary.md**: your stats in Markdown. Paste it into any AI assistant to talk about your habits.
- **llms.txt**: an index of these files for AI tools.
- **schema.json**: what each table holds and its columns.
- **data/**: every table as `.jsonl` (one JSON object per line, for code and AI) and `.csv` (for spreadsheets).
{"- **private/**: your cycle and health log, mood notes and About you answers (you chose to include them)." if include_private else "- Private data (cycle, health log, mood notes, About you answers) was not included. Tick \"include private health\" to add it."}

Not included, on purpose: passwords, login sessions, API keys and tokens, your calendar's secret link, your location.

Tables: {", ".join(sorted(schema))}.
"""
