"""
Secrets in the macOS Keychain, not in loop's database.

The login keychain is encrypted on disk and unlocked by your Mac password; only your macOS user can read it,
and you can see or delete each entry in Keychain Access (search "loop"). loop's database only records that a
secret exists. The secret is passed to `security` on stdin, never on the command line, so it never shows up
in the process list.

Off macOS (or with LOOP_NO_KEYCHAIN=1, which the tests use) secrets fall back to the settings table, which
is still a 0600 file in a 0700 folder.
"""
import os
import shutil
import subprocess
import sys

SERVICE = "loop"   # entries appear in Keychain Access as "loop: <name>"


def available() -> bool:
    return sys.platform == "darwin" and not os.environ.get("LOOP_NO_KEYCHAIN") and bool(shutil.which("security"))


def _label(name: str) -> str:
    return f"{SERVICE}: {name}"


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set(name: str, value: str) -> None:
    if any(c in value for c in "\r\n\0"):
        raise ValueError("that key has line breaks in it; paste it again")
    cmd = f"add-generic-password -U -a {_quote(SERVICE)} -s {_quote(_label(name))} -l {_quote(_label(name))} -w {_quote(value)}\n"
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0 or "error" in (r.stderr or "").lower():
        raise RuntimeError("couldn't save it to your Keychain (is it locked?)")


def get(name: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-a", SERVICE, "-s", _label(name), "-w"],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\n") or None


def delete(name: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-a", SERVICE, "-s", _label(name)], capture_output=True, timeout=20)


# ------------------------------------------------------------ used by connectors

def save_secret(con, name: str, value: str) -> None:
    """Keychain when possible; the settings row then holds only a marker."""
    if available():
        set(name, value)
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, 'keychain')", (name,))
    else:
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (name, value))


def load_secret(con, name: str) -> str | None:
    r = con.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
    v = r[0] if r and r[0] else None
    if v == "keychain":
        return get(name) if available() else None
    return v


def forget_secret(con, name: str) -> None:
    con.execute("DELETE FROM settings WHERE key=?", (name,))
    if available():
        delete(name)


SECRETS = ("zotero_api_key", "anthropic_api_key", "ai_api_key", "lastfm_api_key", "gcal_ics_url")


def resolve(name: str, raw: str | None) -> str | None:
    """A settings value that may be the 'keychain' marker -> the real secret."""
    if raw == "keychain":
        return get(name) if available() else None
    return raw or None


def migrate(con, names=SECRETS) -> list[str]:
    """Move secrets saved in the database before this existed into the Keychain, then scrub the old copies."""
    if not available():
        return []
    moved = []
    for name in names:
        r = con.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
        if r and r[0] and r[0] != "keychain":
            set(name, r[0])
            if get(name) != r[0]:
                continue   # couldn't verify: keep the database copy rather than lose the key
            moved.append(name)
    if moved:
        con.execute("PRAGMA secure_delete=ON")   # overwrite the old value instead of leaving it in free pages
        for name in moved:
            con.execute("UPDATE settings SET value='keychain' WHERE key=?", (name,))
        con.commit()
        con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return moved
