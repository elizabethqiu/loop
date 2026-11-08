"""
Zotero -> your research library (Web API v3, https://www.zotero.org/support/dev/web_api/v3/).

Connect: create a read-only key at https://www.zotero.org/settings/keys (tick "Allow library access";
nothing else), then paste it in You → Connectors, or run `python3 -m loop connect zotero`.
loop asks Zotero who the key belongs to (GET /keys/current), stores your user ID and the key in
~/.loop/loop.db (never sent to the browser), and syncs incrementally: only items whose library
version changed since last time (`since=`), plus deletions (/deleted).

Stored per item: title, type, creators, year, publication, DOI/URL, tags, collections, dates
added/modified, number of notes/attachments. Not stored: attachment files, full text, notes' content.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

API = "https://api.zotero.org"
SCHEMA = """
CREATE TABLE IF NOT EXISTS zotero_items(
  key TEXT PRIMARY KEY,
  version INTEGER,
  item_type TEXT,
  title TEXT,
  creators TEXT,              -- "Smith, Jones & Lee"
  year TEXT,
  publication TEXT,
  url TEXT,
  doi TEXT,
  tags TEXT,                  -- JSON list
  collections TEXT,           -- JSON list of collection names
  date_added TEXT,            -- local ISO
  date_modified TEXT,
  children INTEGER
);
CREATE TABLE IF NOT EXISTS zotero_collections(key TEXT PRIMARY KEY, name TEXT);
"""
SKIP_TYPES = {"attachment", "note", "annotation"}


class ZoteroError(RuntimeError):
    pass


def ensure(con):
    con.executescript(SCHEMA)


def _setting(con, k):
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r and r[0] else None


def is_connected(con) -> bool:
    return bool(_setting(con, "zotero_api_key") and _setting(con, "zotero_user_id"))


def _get(path: str, key: str, params: dict | None = None):
    """GET from the Zotero API -> (json, headers). Honors Backoff / Retry-After."""
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"Zotero-API-Key": key, "Zotero-API-Version": "3", "User-Agent": "loop (personal app)"})
    from .. import egress
    egress.record("zotero", API, f"read {path.split('?')[0]} with your key (read-only)")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                headers = {k.lower(): v for k, v in r.headers.items()}
                if headers.get("backoff"):
                    time.sleep(min(30, int(headers["backoff"])))
                return (json.loads(body) if body else None), headers
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return None, {"last-modified-version": e.headers.get("Last-Modified-Version")}
            if e.code == 429 and attempt < 2:
                time.sleep(min(30, int(e.headers.get("Retry-After") or 5)))
                continue
            if e.code in (401, 403):
                raise ZoteroError("Zotero rejected the key. Make a read-only key with library access at zotero.org/settings/keys.")
            raise ZoteroError(f"Zotero said {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise ZoteroError(f"couldn't reach Zotero ({e.reason})")
    raise ZoteroError("Zotero is rate-limiting; try again in a minute")


def verify(key: str) -> dict:
    data, _ = _get("/keys/current", key)
    if not data or not data.get("userID"):
        raise ZoteroError("That key isn't linked to a Zotero user.")
    if not (data.get("access", {}).get("user", {}) or {}).get("library"):
        raise ZoteroError("That key can't read your library. Edit it at zotero.org/settings/keys and tick 'Allow library access'.")
    return {"user_id": str(data["userID"]), "username": data.get("username") or ""}


def connect(con, key: str) -> dict:
    key = (key or "").strip()
    if not key:
        raise ZoteroError("Paste your Zotero API key.")
    info = verify(key)
    from .. import keychain
    keychain.save_secret(con, "zotero_api_key", key)   # macOS Keychain; the database only notes that it's there
    for k, v in (("zotero_user_id", info["user_id"]), ("zotero_username", info["username"]), ("zotero_version", "0")):
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (k, v))
    con.commit()
    return {**info, **sync(con)}


def disconnect(con):
    ensure(con)
    from .. import keychain
    keychain.forget_secret(con, "zotero_api_key")
    con.execute("DELETE FROM settings WHERE key IN ('zotero_user_id','zotero_username','zotero_version')")
    con.execute("DELETE FROM zotero_items")
    con.execute("DELETE FROM zotero_collections")
    con.commit()


def _local(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    except ValueError:
        return iso[:16]


def _creators(cs: list) -> str:
    names = [c.get("lastName") or c.get("name") or "" for c in cs or [] if c.get("creatorType") in (None, "author", "editor", "presenter", "programmer")]
    names = [n for n in names if n] or [c.get("lastName") or c.get("name") or "" for c in cs or []]
    names = [n for n in names if n]
    if len(names) > 3:
        return f"{names[0]} et al."
    return " & ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else (names[0] if names else "")


def parse_item(it: dict, collections: dict) -> dict | None:
    d = it.get("data") or {}
    if d.get("itemType") in SKIP_TYPES or d.get("parentItem"):
        return None
    meta = it.get("meta") or {}
    year = (meta.get("parsedDate") or d.get("date") or "")[:4]
    return {"key": it.get("key") or d.get("key"), "version": it.get("version") or d.get("version"),
            "item_type": d.get("itemType"), "title": (d.get("title") or d.get("caseName") or d.get("nameOfAct") or "(untitled)").strip(),
            "creators": _creators(d.get("creators")), "year": year if year.isdigit() else None,
            "publication": d.get("publicationTitle") or d.get("bookTitle") or d.get("proceedingsTitle") or d.get("websiteTitle")
                           or d.get("publisher") or None,
            "url": d.get("url") or None, "doi": d.get("DOI") or None,
            "tags": json.dumps([t.get("tag") for t in d.get("tags") or [] if t.get("tag")][:30]),
            "collections": json.dumps([collections.get(c, c) for c in d.get("collections") or []]),
            "date_added": _local(d.get("dateAdded")), "date_modified": _local(d.get("dateModified")),
            "children": meta.get("numChildren") or 0}


def sync(con, max_pages: int = 50) -> dict:
    ensure(con)
    from .. import keychain
    key, uid = keychain.load_secret(con, "zotero_api_key"), _setting(con, "zotero_user_id")
    if not (key and uid):
        raise ZoteroError("Zotero isn't connected. Paste a read-only key in You → Connectors.")
    since = int(_setting(con, "zotero_version") or 0)
    # collection names (small)
    cols, _ = _get(f"/users/{uid}/collections", key, {"limit": 100})
    names = {c["key"]: c["data"]["name"] for c in cols or [] if c.get("data")}
    con.executemany("INSERT OR REPLACE INTO zotero_collections(key, name) VALUES(?,?)", list(names.items()))
    n, start, latest = 0, 0, since
    for _ in range(max_pages):
        items, h = _get(f"/users/{uid}/items/top", key, {"since": since, "limit": 100, "start": start, "format": "json",
                                                          "sort": "dateModified", "direction": "asc"})
        latest = max(latest, int(h.get("last-modified-version") or latest))
        for it in items or []:
            row = parse_item(it, names)
            if row:
                cols_ = list(row)
                con.execute(f"INSERT OR REPLACE INTO zotero_items({', '.join(cols_)}) VALUES({', '.join('?' * len(cols_))})",
                            [row[c] for c in cols_])
                n += 1
        if not items or len(items) < 100:
            break
        start += 100
    deleted = 0
    if since:
        gone, _ = _get(f"/users/{uid}/deleted", key, {"since": since})
        for k in (gone or {}).get("items", []):
            deleted += con.execute("DELETE FROM zotero_items WHERE key=?", (k,)).rowcount
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('zotero_version', ?)", (str(latest),))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM zotero_items").fetchone()[0]
    return {"updated": n, "deleted": deleted, "items": total}


def summary(con, today: date) -> dict:
    ensure(con)
    if not is_connected(con):
        return {"connected": False}
    since7 = (today - timedelta(days=6)).isoformat()
    def rows_(sql, *p):
        cur = con.execute(sql, p)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    recent = rows_("SELECT key, title, creators, year, publication, item_type, url, doi, date_added, date_modified FROM zotero_items "
                   "ORDER BY COALESCE(date_modified, date_added) DESC LIMIT 8")
    added = rows_("SELECT title, creators, year, date_added FROM zotero_items WHERE date_added >= ? ORDER BY date_added DESC", since7)
    tag_count: dict[str, int] = {}
    for (t,) in con.execute("SELECT tags FROM zotero_items WHERE COALESCE(date_modified, date_added) >= ?",
                            ((today - timedelta(days=90)).isoformat(),)):
        for tag in json.loads(t or "[]"):
            tag_count[tag] = tag_count.get(tag, 0) + 1
    by_type = dict(con.execute("SELECT item_type, COUNT(*) FROM zotero_items GROUP BY 1 ORDER BY 2 DESC LIMIT 6").fetchall())
    return {"connected": True, "username": _setting(con, "zotero_username"),
            "total": con.execute("SELECT COUNT(*) FROM zotero_items").fetchone()[0],
            "added_7d": len(added), "added": added[:6], "recent": recent,
            "touched_7d": con.execute("SELECT COUNT(*) FROM zotero_items WHERE date_modified >= ?", (since7,)).fetchone()[0],
            "top_tags": sorted(tag_count.items(), key=lambda kv: -kv[1])[:10], "by_type": by_type}


def added_on(con, d: date) -> list[dict]:
    ensure(con)
    return [{"title": r[0], "creators": r[1]} for r in con.execute(
        "SELECT title, creators FROM zotero_items WHERE substr(date_added,1,10)=? ORDER BY date_added", (d.isoformat(),))]
