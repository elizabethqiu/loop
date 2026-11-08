"""
Login for loop. One account per loop install (it's your personal app), created on first visit.

  - Passwords: PBKDF2-HMAC-SHA256, 600,000 iterations, random salt. Stored only in your local database.
  - Sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie; 30 days, revocable (log out).
  - Brute force: after 5 wrong passwords from one address, logins pause for 30 seconds (then doubling).
  - Forgot it? On the Mac:  python3 -m loop reset-password
"""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

ITERATIONS = 600_000
SESSION_DAYS = 30
COOKIE = "loop_session"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  username TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,     -- sha256 of the cookie value; the raw token is never stored
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  agent TEXT
);
"""

_fails: dict[str, list[float]] = {}  # address -> [count, locked_until, lock_seconds]


class AuthError(RuntimeError):
    pass


def ensure(con):
    con.executescript(SCHEMA)


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def has_user(con) -> bool:
    return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def _check_password_rules(password: str):
    if len(password) < 8:
        raise AuthError("Use at least 8 characters.")


def create_user(con, name: str, username: str, password: str) -> int:
    if has_user(con):
        raise AuthError("An account already exists. Log in instead.")
    name, username = name.strip(), username.strip().lower()
    if not name or not username:
        raise AuthError("Name and username are required.")
    _check_password_rules(password)
    salt = secrets.token_hex(16)
    cur = con.execute("INSERT INTO users(name, username, pw_hash, salt, created_at) VALUES(?,?,?,?,?)",
                      (name, username, _hash(password, salt), salt, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return cur.lastrowid


def _throttled(addr: str) -> float:
    rec = _fails.get(addr)
    return max(0.0, rec[1] - time.time()) if rec else 0.0


def _record_fail(addr: str):
    rec = _fails.setdefault(addr, [0, 0.0, 15.0])
    rec[0] += 1
    if rec[0] >= 5:
        rec[2] = min(rec[2] * 2, 900)
        rec[1] = time.time() + rec[2]
        rec[0] = 0


def login(con, username: str, password: str, addr: str = "", agent: str = "") -> str:
    wait = _throttled(addr)
    if wait:
        raise AuthError(f"Too many tries. Wait {int(wait) + 1} seconds.")
    row = con.execute("SELECT id, pw_hash, salt FROM users WHERE username=?", ((username or "").strip().lower(),)).fetchone()
    ok = bool(row) and hmac.compare_digest(_hash(password or "", row[2]), row[1])
    if not ok:
        if not row:
            _hash(password or "", "00" * 16)  # same work either way, so timing doesn't reveal usernames
        _record_fail(addr)
        raise AuthError("Wrong username or password.")
    _fails.pop(addr, None)
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    con.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, agent) VALUES(?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), row[0], now.isoformat(timespec="seconds"),
                 (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"), (agent or "")[:200]))
    con.commit()
    return token


def user_for(con, token: str | None) -> dict | None:
    if not token:
        return None
    r = con.execute(
        "SELECT u.id, u.name, u.username, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?",
        (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not r or r[3] < datetime.now().isoformat(timespec="seconds"):
        return None
    return {"id": r[0], "name": r[1], "username": r[2]}


def logout(con, token: str | None):
    if token:
        con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        con.commit()


def change_password(con, user_id: int, old: str, new: str):
    row = con.execute("SELECT pw_hash, salt FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not hmac.compare_digest(_hash(old, row[1]), row[0]):
        raise AuthError("Current password is wrong.")
    _check_password_rules(new)
    salt = secrets.token_hex(16)
    con.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?", (_hash(new, salt), salt, user_id))
    con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))  # sign out everywhere else
    con.commit()


def reset_password(con, new: str) -> str:
    """For the CLI (someone at the Mac): set a new password for the account, ending all sessions."""
    _check_password_rules(new)
    row = con.execute("SELECT id, username FROM users LIMIT 1").fetchone()
    if not row:
        raise AuthError("No account yet. Open loop in a browser to create one.")
    salt = secrets.token_hex(16)
    con.execute("UPDATE users SET pw_hash=?, salt=? WHERE id=?", (_hash(new, salt), salt, row[0]))
    con.execute("DELETE FROM sessions")
    con.commit()
    return row[1]


def rename(con, user_id: int, name: str):
    if name.strip():
        con.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), user_id))
        con.commit()
