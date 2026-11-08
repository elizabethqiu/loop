# Security design: unlocking loop and protecting secrets

Status: **proposed**. This document records the options considered for strengthening how loop is unlocked and how its secrets are protected, the trade-offs of each, and the recommended direction. The current controls are described in [SECURITY.md](../SECURITY.md).

## Context

loop holds sleep, food, health, mood and browsing data, along with credentials for the services it connects to. Today it is protected by:

- a local password, hashed with PBKDF2-HMAC-SHA256 (600,000 iterations)
- a 30-day session cookie (`HttpOnly`, `SameSite=Strict`), stored only as a hash
- a pause after repeated failed sign-ins
- macOS file permissions on `~/.loop` (0700 for the folder, 0600 for its files)
- API keys and private URLs in the macOS login Keychain

Two gaps remain:

1. **A long-lived session is the only gate.** Once signed in, anyone at the unlocked Mac, or holding a phone that has loop installed, can open everything for 30 days, including exports and private health records.
2. **Some credentials are still in the database.** The Spotify and Together hub tokens are protected by file permissions, not encryption.

## Goals

- Make unlocking loop fast enough that people leave the protection on.
- Require a fresh check before the most sensitive actions.
- Keep loop usable when no one is at the Mac, so background syncs still run.
- Never let biometric data, or the means to reproduce it, reach loop.
- Add no cloud dependency. Everything must work offline on the Mac.

## Non-goals

- Protecting against malware running as the user, or an administrator. Those attackers can read process memory and the Keychain session regardless.
- Replacing FileVault. Full-disk encryption remains the recommended protection at rest.

## Options

### A. Touch ID sign-in (passkeys)

loop registers a passkey (WebAuthn) with the Mac's platform authenticator. Signing in becomes a fingerprint touch. The fingerprint is matched inside the Secure Enclave. loop receives only a signed challenge and stores only a public key.

- **For:** fast, phishing-resistant, standard. Browsers allow WebAuthn on `localhost`, and on HTTPS Tailscale addresses for phones, which can use their own biometrics.
- **Against:** WebAuthn must be implemented on the server with the standard library alone (CBOR decoding and ES256 signature checks). Each device registers its own passkey.
- **Fallback:** the password remains available.

### B. A second check before sensitive actions

Before exporting data, viewing private health records, or changing connectors, keys or the password, loop asks for Touch ID or the password again, unless the user has confirmed within the last 5 minutes.

- **For:** narrows the damage from an unattended, signed-in session without adding friction to everyday use.
- **Against:** the list of protected actions must be maintained, and every new sensitive endpoint must be added to it. Tests should enforce this.
- **Depends on:** A for Touch ID. It works with the password alone.

### C. Automatic lock

The session locks after a set period of inactivity (default 30 minutes) and when the Mac sleeps or its screen locks. Unlocking uses A or the password. Background jobs are not affected.

- **For:** simple, and closes the "Mac left unlocked" case.
- **Against:** a lock that is too short is irritating. The period must be adjustable.

### D. Authenticator-app codes (TOTP)

A six-digit code from an authenticator app as a second factor, in addition to the password.

- **For:** protects remote access, from phones and other computers, if a password is guessed or reused. Implementable with the standard library (HMAC-SHA1, RFC 6238).
- **Against:** of little value on the Mac itself, where A is stronger and faster. Recovery codes are needed.
- **When:** if loop is reached from devices other than the Mac, or a hosted version is offered.

### E. Touch ID before reading stored secrets

Secrets are stored with a Keychain access control that requires user presence, so the Mac asks for a fingerprint before loop can read, for example, the Zotero key.

- **For:** a stolen copy of the user's session cannot extract the credentials.
- **Against:** the `security` command-line tool cannot set this kind of access control, so a small signed native helper is required. Background syncs would trigger prompts unless secrets are unlocked once per login and held in memory, and holding them in memory undoes much of the benefit.
- **When:** after A to C, and only for secrets that background jobs do not need.

### F. Encrypting the private tables

Mood notes, journal entries and private health records are encrypted inside the database with a key held in the Keychain.

- **For:** protects those records in backups and copies of the database made outside FileVault.
- **Against:** SQLite has no built-in encryption, and the standard library has no authenticated cipher, so this needs either a dependency or a careful custom construction. Encrypted fields also cannot be searched with SQL.
- **When:** if loop offers sync or cloud backup, where the database leaves the Mac.

### G. All remaining credentials in the Keychain

The Spotify and Together hub tokens move to the Keychain, using the existing migration that already moved the AI, Zotero, Last.fm and Calendar secrets.

- **For:** no plaintext credentials in the database. Small, and uses existing code.
- **Against:** none of note.

## Comparison

| Option | Protects against | Everyday friction | Effort | Dependencies |
|---|---|---|---|---|
| A. Touch ID sign-in | Password theft, phishing, shoulder-surfing | Lower than today | Medium | None |
| B. Second check | Misuse of an unattended session | Occasional touch | Small | A recommended |
| C. Auto-lock | Mac left unlocked | Occasional touch | Small | None |
| D. TOTP | Guessed password on remote access | Code per sign-in | Small to medium | None |
| E. Touch ID for secrets | Credential theft from a live session | Prompts unless cached | Medium | Native helper |
| F. Encrypted tables | Database copies outside FileVault | None | Large | Cipher implementation |
| G. Keychain for all tokens | Plaintext tokens on disk | None | Small | None |

## Recommendation

Build **G, A, B and C**, in that order.

- **G** first: it is small and removes the last plaintext credentials.
- **A, B and C** together replace a single 30-day gate with layered checks that are faster than typing a password: Touch ID to get in, a fresh touch for anything sensitive, and a lock when the Mac is left.
- **D** is deferred until loop is used beyond a single Mac.
- **E and F** are deferred until data leaves the Mac. Their cost is high, and FileVault already covers the main case.

## Decision record

| Date | Decision |
|---|---|
| 2026-11-07 | Options documented. Recommendation: G, A, B, C. Awaiting approval. |
