"""
Curius → your saved links and highlights (curius.app has no public API; this uses the same JSON its website loads).

Connect: paste your profile link (curius.app/your-name) in the brain graph's Curius view.
Only public, read-only requests, no login, no password, nothing sent but your profile name:
  GET /api/users/<name>                one call: your numeric user id
  GET /api/users/<id>/searchLinks      every saved link at once (title, url, snippet, dates)
  GET /api/users/<id>/links?page=N     30 at a time, with your highlights and topics

Sync is gentle: the full list at most every 6 hours, and highlights a few pages per run (one request per second)
until the whole history has been read once; after that only the newest pages are refreshed.

Stored on this Mac: link title, url, site, snippet (shortened), dates, your highlight texts and topic names.
Not stored: page contents, comments, other people's data.
"""
import json
import re
import time
from collections import Counter
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE = "https://curius.app"
UA = "loop (personal app)"
SCHEMA = """
CREATE TABLE IF NOT EXISTS curius_people(
  anon TEXT PRIMARY KEY,        -- a salted hash: stable, but says nothing about who it is
  shared TEXT,                  -- JSON list of YOUR link ids they also saved
  words TEXT,                   -- JSON list of words from their recent saves' titles
  recent INTEGER,
  at TEXT,
  role TEXT,                    -- a guess: Researcher, Engineer, Designer, Founder, Writer, Artist, Policy, Student, Reader
  profile TEXT                  -- their Curius profile link, only so YOU can reveal it; never exported or shared
);
CREATE TABLE IF NOT EXISTS curius_links(
  id INTEGER PRIMARY KEY,
  url TEXT,
  site TEXT,
  title TEXT,
  snippet TEXT,
  created TEXT,
  modified TEXT,
  favorite INTEGER NOT NULL DEFAULT 0,
  highlights TEXT,              -- JSON list of your highlight texts
  topics TEXT                   -- JSON list of topic names
);
"""
FULL_EVERY = timedelta(hours=6)
PAGES_PER_RUN = 12                # backfill: 12 pages (360 links) per run, one request a second
REFRESH_PAGES = 3                 # after backfill: the newest 90 links' highlights


class CuriusError(RuntimeError):
    pass


def ensure(con):
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(curius_people)")}
    for col in ("role", "profile"):
        if col not in have:
            con.execute(f"ALTER TABLE curius_people ADD COLUMN {col} TEXT")
            con.execute("DELETE FROM settings WHERE key='curius_people_list_at'")   # re-read everyone with the new fields


def _setting(con, k):
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r and r[0] else None


def _set(con, k, v):
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (k, str(v)))


def is_connected(con) -> bool:
    return bool(_setting(con, "curius_user_id"))


def _get(path: str, what: str | None = None):
    from .. import egress
    egress.record("curius", BASE, what or f"read {re.sub(r'[?].*', '', path)} (public, no login)")
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(min(30, int(e.headers.get("Retry-After") or 5)))
                continue
            if e.code == 404:
                raise CuriusError("Curius doesn't know that profile. Use the name in your profile link (curius.app/your-name).")
            raise CuriusError(f"Curius said {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise CuriusError(f"couldn't reach Curius ({e.reason})")
    raise CuriusError("Curius is rate-limiting; try again in a minute")


def slug_of(profile: str) -> str:
    """'https://curius.app/elizabeth-qiu', 'curius.app/elizabeth-qiu' or 'elizabeth-qiu' → 'elizabeth-qiu'."""
    s = (profile or "").strip().rstrip("/")
    s = re.sub(r"^(https?://)?(www\.)?curius\.app/", "", s, flags=re.I)
    s = s.split("?")[0].split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", s):
        raise CuriusError("Paste your Curius profile link, e.g. curius.app/your-name.")
    return s


def connect(con, profile: str) -> dict:
    slug = slug_of(profile)
    u = (_get(f"/api/users/{urllib.parse.quote(slug)}") or {}).get("user") or {}
    if not u.get("id"):
        raise CuriusError("Curius doesn't know that profile.")
    _set(con, "curius_user_id", int(u["id"]))
    _set(con, "curius_user", u.get("userLink") or slug)
    _set(con, "curius_name", " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x))
    for k in ("curius_full_at", "curius_hl_page", "curius_hl_done", "curius_hl_at"):
        con.execute("DELETE FROM settings WHERE key=?", (k,))
    con.commit()
    return {"user": u.get("userLink") or slug, "name": _setting(con, "curius_name")}


def disconnect(con) -> dict:
    for k in ("curius_user_id", "curius_user", "curius_name", "curius_full_at", "curius_hl_page", "curius_hl_done", "curius_hl_at"):
        con.execute("DELETE FROM settings WHERE key=?", (k,))
    con.execute("DELETE FROM curius_links")
    con.execute("DELETE FROM curius_people")
    for k in ("curius_salt", "curius_people_queue", "curius_people_list_at", "curius_topics_at"):
        con.execute("DELETE FROM settings WHERE key=?", (k,))
    con.execute("DELETE FROM brain_marks WHERE node LIKE 'link:%'")
    con.commit()
    return {"ok": True}


def _site(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/?#:]+)", url or "")
    return m.group(1).lower() if m else ""


def _upsert(con, l: dict, with_detail: bool):
    if not l.get("id"):
        return
    row = (int(l["id"]), l.get("link") or "", _site(l.get("link") or ""), (l.get("title") or "").strip()[:300],
           (l.get("snippet") or "").strip()[:400], (l.get("createdDate") or "")[:19], (l.get("modifiedDate") or "")[:19],
           int(bool(l.get("favorite"))))
    con.execute("""INSERT INTO curius_links(id, url, site, title, snippet, created, modified, favorite) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET url=excluded.url, site=excluded.site, title=excluded.title, snippet=excluded.snippet,
                   created=excluded.created, modified=excluded.modified, favorite=excluded.favorite""", row)
    if with_detail:
        hl = [h.get("highlight") or h.get("rawHighlight") for h in l.get("highlights") or [] if isinstance(h, dict)]
        tp = [t.get("topic") or t.get("name") for t in l.get("topics") or [] if isinstance(t, dict)]
        con.execute("UPDATE curius_links SET highlights=?, topics=? WHERE id=?",
                    (json.dumps([h[:600] for h in hl if h][:40]), json.dumps([t for t in tp if t][:20]), int(l["id"])))


def _sync_links(con, now: datetime | None = None, pause: float = 1.0, max_pages: int | None = None) -> dict:
    uid = _setting(con, "curius_user_id")
    if not uid:
        raise CuriusError("Curius not connected. Paste your profile link in the brain graph's Curius view.")
    now = now or datetime.now()
    out = {}
    last_full = _setting(con, "curius_full_at")
    if not last_full or now - datetime.fromisoformat(last_full) >= FULL_EVERY:
        links = (_get(f"/api/users/{uid}/searchLinks") or {}).get("links") or []
        seen = set()
        for l in links:
            _upsert(con, l, with_detail=False)
            seen.add(int(l["id"])) if l.get("id") else None
        if links:     # links you deleted on Curius go here too
            con.execute(f"DELETE FROM curius_links WHERE id NOT IN ({','.join('?' * len(seen))})", tuple(seen))
        _set(con, "curius_full_at", now.isoformat(timespec="seconds"))
        out["links"] = len(links)
        con.commit()
    # highlights and topics: page through once, then keep the newest pages fresh
    done = _setting(con, "curius_hl_done") == "1"
    if not done:
        start, pages = int(_setting(con, "curius_hl_page") or 0), PAGES_PER_RUN if max_pages is None else max_pages
    else:
        last = _setting(con, "curius_hl_at")
        if last and now - datetime.fromisoformat(last) < FULL_EVERY:
            return out or {"up_to_date": True}
        start, pages = 0, REFRESH_PAGES if max_pages is None else max_pages
    read = 0
    for page in range(start, start + pages):
        batch = (_get(f"/api/users/{uid}/links?page={page}") or {}).get("userSaved") or []
        for l in batch:
            _upsert(con, l, with_detail=True)
        read += len(batch)
        if not batch:
            done = True
            break
        if not done:
            _set(con, "curius_hl_page", page + 1)
        con.commit()
        time.sleep(pause)
    if done:
        _set(con, "curius_hl_done", "1")
    _set(con, "curius_hl_at", now.isoformat(timespec="seconds"))
    con.commit()
    out["highlights"] = "all read" if done else f"{read} links read so far, continuing in the background"
    out["total"] = con.execute("SELECT COUNT(*) FROM curius_links").fetchone()[0]
    return out


def sync(con, now: datetime | None = None, pause: float = 1.0, max_pages: int | None = None) -> dict:
    out = _sync_links(con, now, pause, max_pages)
    if max_pages is None and _setting(con, "curius_hl_done") == "1":   # people and topics only once your own links are in
        out.update(sync_people(con, now, pause))
        out.update(sync_topics(con, now, pause))
    return out


# ------------------------------------------------------------------ people you follow, kept anonymous
PEOPLE_EVERY = timedelta(days=7)
PEOPLE_PER_RUN = 12


def _anon(con, user_id) -> str:
    """A stable, meaningless id for a person: a hash with a secret salt that never leaves this Mac. Not reversible."""
    import hashlib
    import secrets
    salt = _setting(con, "curius_salt")
    if not salt:
        salt = secrets.token_hex(16)
        _set(con, "curius_salt", salt)
    return hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:16]


ROLES = [   # (role, words in their profile or saved titles, sites they save from)
    ("Researcher", "research researcher phd professor postdoc lab paper papers arxiv neural model models theory scientist science "
                   "study experiment interpretability alignment physics biology neuroscience math mathematics",
     "arxiv.org openreview.net scholar.google.com nature.com science.org biorxiv.org pubmed.ncbi.nlm.nih.gov distill.pub"),
    ("Engineer", "engineer engineering software developer code coding programming rust python javascript compiler database "
                 "infrastructure systems kernel backend frontend devops gpu",
     "github.com news.ycombinator.com stackoverflow.com docs.python.org developer.mozilla.org"),
    ("Designer", "design designer typography font ux ui interface interaction figma visual brand illustration product-design",
     "dribbble.com figma.com behance.net fonts.google.com"),
    ("Founder", "founder startup startups ceo cofounder fundraising venture vc investor investing seed company yc",
     "ycombinator.com techcrunch.com a16z.com paulgraham.com"),
    ("Writer", "writer writing essay essays author poetry poem fiction novel journalist editor newsletter",
     "substack.com medium.com newyorker.com theatlantic.com"),
    ("Artist", "artist art painting music musician film photography photographer illustration animation",
     "artstation.com soundcloud.com bandcamp.com vimeo.com"),
    ("Policy", "policy government governance law legal security national geopolitics regulation economics economist",
     "whitehouse.gov congress.gov brookings.edu rand.org"),
]


def infer_role(profile: dict, saved: list[dict]) -> str:
    """A rough guess at what kind of person this is, from their public profile and what they save. Only the guess is kept."""
    from ..focusmap import _words
    about = " ".join(str(profile.get(k) or "") for k in ("major", "interests", "expertise", "school"))
    words = Counter(w for w in _words(about) for _ in range(4))            # what they say about themselves counts most
    for l in saved:
        words.update(_words(l.get("title") or ""))
    sites = Counter(_site(l.get("link") or "") for l in saved)
    best, best_score = "Reader", 0.0
    for role, kw, st in ROLES:
        score = sum(words[w] for w in kw.split()) + 2 * sum(sites[s] for s in st.split())
        if role == "Engineer" and profile.get("github"):
            score += 3
        if score > best_score:
            best, best_score = role, score
    if best_score < 3:
        best = "Student" if profile.get("school") else "Reader"
    return best


def sync_people(con, now: datetime | None = None, pause: float = 1.0) -> dict:
    """For each person you follow: a guess at what kind of person they are (Researcher, Engineer, Designer…), which of
    *your* links they also saved, and a few words about what they read. Their profile link is kept only so you can
    reveal it yourself; never their name, website, or their links. Twelve people per run, one request a second, weekly."""
    from ..focusmap import _words
    uid = _setting(con, "curius_user_id")
    now = now or datetime.now()
    last = _setting(con, "curius_people_list_at")
    if not last or now - datetime.fromisoformat(last) >= PEOPLE_EVERY:
        feed = (_get(f"/api/users/{uid}/followingLinks") or {}).get("users") or []
        who = {}
        for e in feed:
            u = isinstance(e, dict) and (e.get("user") or {})
            if u and u.get("id") and int(u["id"]) != int(uid) and re.fullmatch(r"[\w-]{1,80}", str(u.get("userLink") or "")):
                who[int(u["id"])] = u["userLink"]
        _set(con, "curius_people_queue", json.dumps(sorted(who.items())))   # kept only until processed, then cleared
        keep = {_anon(con, i) for i in who}
        for (a,) in con.execute("SELECT anon FROM curius_people").fetchall():
            if a not in keep:
                con.execute("DELETE FROM curius_people WHERE anon=?", (a,))
        _set(con, "curius_people_list_at", now.isoformat(timespec="seconds"))
        con.commit()
    queue = json.loads(_setting(con, "curius_people_queue") or "[]")
    if not queue:
        return {}
    mine = {(u or "").rstrip("/"): i for i, u in con.execute("SELECT id, url FROM curius_links")}
    done = 0
    for pid, slug in queue[:PEOPLE_PER_RUN]:
        profile = (_get(f"/api/users/{urllib.parse.quote(str(slug))}", "read one followed person's public profile (only a role guess is kept)")
                   or {}).get("user") or {}
        time.sleep(pause)
        saved = (_get(f"/api/users/{int(pid)}/links?page=0", "read one followed person's 30 latest public saves (kept anonymous)")
                 or {}).get("userSaved") or []
        shared = sorted({mine[(l.get("link") or "").rstrip("/")] for l in saved if (l.get("link") or "").rstrip("/") in mine})
        words = Counter(w for l in saved for w in _words(l.get("title") or ""))
        top = [w for w, n in words.most_common(10) if n >= 2]
        con.execute("INSERT OR REPLACE INTO curius_people(anon, shared, words, recent, at, role, profile) VALUES(?,?,?,?,?,?,?)",
                    (_anon(con, pid), json.dumps(shared), json.dumps(top), len(saved), now.isoformat(timespec="seconds"),
                     infer_role(profile, saved), f"{BASE}/{slug}"))
        done += 1
        time.sleep(pause)
    _set(con, "curius_people_queue", json.dumps(queue[done:]) if queue[done:] else "")
    con.commit()
    return {"people": f"{done} read, {len(queue) - done} to go"}


# ------------------------------------------------------------------ your public topics (if you make any public)
def sync_topics(con, now: datetime | None = None, pause: float = 1.0) -> dict:
    """Curius topics are private unless you make them public. Public ones are read weekly and attached to their links."""
    uid = _setting(con, "curius_user_id")
    now = now or datetime.now()
    last = _setting(con, "curius_topics_at")
    if last and now - datetime.fromisoformat(last) < PEOPLE_EVERY:
        return {}
    topics = (_get(f"/api/user/topics?uid={uid}") or {}).get("topics") or []
    names = {}
    for t in topics[:60]:
        slug = isinstance(t, dict) and (t.get("slug") or t.get("topicSlug"))
        name = isinstance(t, dict) and (t.get("topic") or t.get("name") or slug)
        if not slug or not re.fullmatch(r"[\w-]{1,80}", str(slug)):
            continue
        for page in range(0, 30):
            batch = (_get(f"/api/users/{uid}/links?page={page}&topicSlug={urllib.parse.quote(str(slug))}") or {}).get("userSaved") or []
            for l in batch:
                if l.get("id"):
                    names.setdefault(int(l["id"]), set()).add(str(name))
            time.sleep(pause)
            if len(batch) < 30:
                break
    for lid, ns in names.items():
        r = con.execute("SELECT topics FROM curius_links WHERE id=?", (lid,)).fetchone()
        if r:
            have = set(json.loads(r[0] or "[]"))
            con.execute("UPDATE curius_links SET topics=? WHERE id=?", (json.dumps(sorted(have | ns)), lid))
    _set(con, "curius_topics_at", now.isoformat(timespec="seconds"))
    con.commit()
    return {"public_topics": len(topics)}


def every_s(con) -> int:
    """Every 2 minutes while there's a first pass to finish (highlights, people), then every 10."""
    busy = _setting(con, "curius_hl_done") != "1" or bool(json.loads(_setting(con, "curius_people_queue") or "[]"))
    return 120 if busy else 600


def status(con) -> dict:
    n = con.execute("SELECT COUNT(*), SUM(highlights IS NOT NULL AND highlights != '[]') FROM curius_links").fetchone()
    return {"connected": is_connected(con), "user": _setting(con, "curius_user"), "name": _setting(con, "curius_name"),
            "links": n[0] or 0, "with_highlights": n[1] or 0, "synced_at": _setting(con, "curius_full_at"),
            "highlights_done": _setting(con, "curius_hl_done") == "1",
            "people": con.execute("SELECT COUNT(*) FROM curius_people").fetchone()[0]}
