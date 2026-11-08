# Security

loop stores personal health, activity and reading data. This document describes how that data is protected, what loop does not protect against, and how to report a vulnerability.

## Reporting a vulnerability

Report vulnerabilities privately through the repository's **Security → Report a vulnerability** form (GitHub private vulnerability reporting). Do not open a public issue.

Include the affected version (see [CHANGELOG.md](CHANGELOG.md)), steps to reproduce, and the impact. Reports are acknowledged within 7 days. A fix or mitigation is released before the issue is discussed publicly.

## Supported versions

Only the latest release receives security fixes.

## Threat model

loop is a single-user application that runs on the user's own Mac.

**In scope:**
- other local user accounts on the same Mac
- websites open in the user's browser that try to reach loop at 127.0.0.1 (cross-site requests, DNS rebinding)
- devices on the same network
- third-party services that loop connects to, receiving more data than necessary

**Out of scope:**
- an attacker who is already signed in to the user's macOS account, or has administrator access
- malware running as the user
- physical access to an unlocked Mac

FileVault full-disk encryption is recommended and protects data at rest when the Mac is off or locked.

## Controls

### Storage

- All data is stored in `~/.loop/`. loop sets the folder to mode 0700 and its files to 0600 on every start, so other macOS accounts cannot read them.
- API keys for Zotero, Anthropic, other AI providers and Last.fm, and the Google Calendar private address, are stored in the macOS login Keychain. The database records only that a secret exists. Secrets are passed to the `security` tool on standard input, never as command-line arguments.
- The Garmin password is never stored. Garmin session tokens are kept in `~/.loop/garmin_tokens/`.
- The Spotify tokens and the Together hub token are stored in the database. Moving them to the Keychain is planned.

### Authentication

- A local account is created on first visit.
- Passwords are hashed with PBKDF2-HMAC-SHA256: 600,000 iterations and a random salt per password.
- Sessions use a random 256-bit token in an `HttpOnly`, `SameSite=Strict` cookie, valid for 30 days. Only a SHA-256 hash of the token is stored, and signing out revokes it.
- After 5 failed sign-ins from one address, sign-in is paused for 30 seconds. The pause doubles with each further failure.
- A forgotten password can only be reset from the Mac itself: `python3 -m loop reset-password`.

### Network exposure

- The server listens on 127.0.0.1 only. Remote devices reach it only through a private network the user sets up, such as Tailscale.
- Requests whose `Host` header is not a local name, an IP address, or a Tailscale name are refused, which blocks DNS rebinding.
- State-changing requests must be JSON. Form-encoded posts are refused (HTTP 415), so other websites cannot submit forms to loop.
- Secrets are never sent to the browser. The settings sent to the page remove every key, token and private URL.

### Outbound data

- loop makes no network requests unless the user connects a service.
- Every outbound connection is declared in `loop/egress.py`, shown in the app under **You → Where your data goes**, and logged to `~/.loop/egress.log`.
- An optional AI provider receives a stats summary only. Notes, journal entries, mood notes, private health records, weight and browsing titles are never included. The test suite enforces this.
- Private health records are excluded from exports and from the Together hub. The test suite enforces this too.

### Tracking limits

The Mac tracker records the frontmost app, the browser site and tab title, and music playing. It does not capture screenshots, page contents or keystrokes. Private and incognito windows are recorded only as "private browsing". Titles can be turned off entirely or per site in **You → What you track**.

## Planned improvements

Options for Touch ID sign-in, a second check before sensitive actions, automatic locking, and moving the remaining tokens into the Keychain are compared in [docs/security-design.md](docs/security-design.md), with the reasoning behind the recommended order.
