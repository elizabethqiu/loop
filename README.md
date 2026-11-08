# loop

loop is a local-first personal tracker for macOS. It brings sleep, food, activity, focus, reading and goals together in one place, explains how its estimates are made, and keeps the data on the user's own computer.

- **Local by default.** All data is stored in one SQLite database in `~/.loop/`, readable only by the user's macOS account.
- **No required cloud service.** The core runs on the Python standard library. Every network connection is optional, chosen by the user, and listed in the app.
- **Explained, not opaque.** Each score, prediction and suggestion shows its inputs and method.
- **AI is optional.** loop works fully without AI. If an AI provider is connected, it receives only a stats summary, never notes, journal entries, mood notes, private health records or browsing titles.
- **Not for sale.** User data is never sold, shared for advertising, or used to train models.

## Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Quick start](#quick-start)
4. [Data sources](#data-sources)
5. [Data storage](#data-storage)
6. [Network connections](#network-connections)
7. [Command reference](#command-reference)
8. [Architecture](#architecture)
9. [Development](#development)
10. [Documentation](#documentation)

## Features

| Area | What it does |
|---|---|
| **Today** | A daily summary, to-dos with urgency and importance, the day's calendar, check-ins, and recap cards built from the user's own data. |
| **Build** | Time by project, coding activity and local git commits, writing and design work, an idea inbox, and a graph of the user's research library and saved links, viewable in 2D or 3D. |
| **Consume** | Time spent on what the user reads, watches and listens to, sorted into deep, light and nonproductive, with after-the-fact tags that produce a list of sources to cut. |
| **Eat** | A food log backed by the USDA database and barcode lookup, calories, macros and fiber against targets, meal names, repeat-meal shortcuts and a food score. |
| **Sleep** | Last night's stages and heart rate from Garmin, an estimated body clock, and a suggested bedtime with its working shown. Bedtime moves toward the user's goal at a pace set by age and sleep regularity. |
| **Health** | Weight, water, workouts and measurements, plus a private cycle and symptom log that never leaves the Mac. |
| **Progress** | Goals with streaks that reward consistency and offer gradual increases. |
| **Together** | Optional sharing of weekly goal summaries with friends through a self-hosted hub. |
| **Search** | Cmd/Ctrl+K finds a day in any date format, summarizes a period ("last week", "march 2025"), compares two periods, answers questions about the user's own numbers ("when did I sleep the most"), routes questions to the right page, and searches foods, meals, journal, to-dos, papers and calendar. Search runs locally. |
| **Ask** | Optional questions to an AI provider of the user's choice: Claude, any OpenAI-compatible API, or a local model through Ollama. |

Methods for every score and prediction are listed in the app under **Methods**, generated from the code.

## Requirements

- macOS
- Python 3.10 or later (`python3 --version`)
- Optional: `garminconnect` for Garmin sync, `anthropic` for Claude

## Quick start

```sh
git clone <repository-url> loop
cd loop
python3 -m loop install          # starts loop now and at every login
open http://127.0.0.1:8765
```

On first visit, loop asks the user to create a local account. To try loop with sample data first, run `python3 -m loop test` and open `http://localhost:8767` (username `test`, password `test`). The test instance uses its own database and never reads the real one.

Setup for Garmin, music, phones and other computers is covered step by step in [INSTALL.md](INSTALL.md).

## Data sources

| Source | Data | Connection |
|---|---|---|
| Mac activity tracker | Frontmost app, browser site and tab title, music playing | Built in. macOS asks once per browser for Automation permission. No screenshots, page contents or keystrokes are recorded. |
| Manual entry | Food, workouts, reading, water, weight, mood, journal | Built in |
| Garmin Connect | Sleep, sleep stages, resting heart rate, steps, intensity minutes | `python3 -m loop connect garmin` |
| Last.fm or Spotify | Listening history | You → Connectors, or `python3 -m loop connect spotify` |
| Google Calendar | Events, read-only | Private iCal address, pasted in You → Connectors |
| Zotero | Research library, read-only | Read-only API key, stored in the macOS Keychain |
| Curius | Saved links | Public profile name |
| Files | Health CSV, MyNetDiary export, Claude conversation export | `python3 -m loop import-health`, `import-mynetdiary`, `import-claude` |

The tracker can be limited in **You → What you track**: domains and apps can be excluded entirely or recorded without titles, titles can be turned off, and tracking can be paused. Private and incognito windows are recorded only as "private browsing".

## Data storage

| Location | Contents |
|---|---|
| `~/.loop/loop.db` | The SQLite database: activity, food, sleep, health, goals, settings and the login |
| `~/.loop/profile.md` | A Markdown "About me" summary, refreshed hourly, without browsing titles |
| `~/.loop/garmin_tokens/` | Garmin session tokens. The Garmin password is never stored. |
| `~/.loop/loop.log`, `~/.loop/egress.log` | The background process log, and a log of every outbound request |
| macOS Keychain | API keys and tokens, listed in Keychain Access as `loop: <name>` |

loop sets `~/.loop` to mode 0700 and its files to 0600 on every start. **You → Your data** downloads everything as one .zip of JSON, CSV and Markdown. Private health records are left out of exports unless the user includes them.

To remove tracked titles for one day while keeping the time totals:

```sh
sqlite3 ~/.loop/loop.db "update activity set title=null where start like '2026-11-04%'; update items set title=null where started_at like '2026-11-04%'"
```

To erase everything, run `python3 -m loop uninstall` and delete `~/.loop`.

## Network connections

loop makes no network requests unless the user connects a service. The full register, with exactly what each connection sends, is in the app under **You → Where your data goes**, and is defined in `loop/egress.py`.

| Connection | Destination | Sends |
|---|---|---|
| AI provider | The provider chosen in Ask | The question and a stats summary. Never notes, journal, mood notes, private health, weight or browsing. |
| Garmin | connect.garmin.com | The saved Garmin session |
| Last.fm, Spotify, MusicBrainz | Their APIs | The account token, and artist and track names for tags |
| Google Calendar | calendar.google.com | A request for the private calendar link |
| Zotero | api.zotero.org | The read-only key |
| Curius | curius.app | The public profile name |
| Food search | api.nal.usda.gov, world.openfoodfacts.org | The search text or barcode, only when the offline food database is not built |
| Together hub | The hub the user joined | Display name and the weekly summary the user chooses to share |
| Feedback | The feedback endpoint the user configures | The message and the page it was sent from |
| Website snapshot | The user's own site repository, by `git push` | Topic names and sizes from the reading graph. Off by default. Never people, notes, AI chats, to-dos or paper titles. |

## Command reference

| Command | Purpose |
|---|---|
| `python3 -m loop install` / `uninstall` | Register or remove the background service (launchd) |
| `python3 -m loop run` | Run the service in the current terminal |
| `python3 -m loop connect <garmin\|lastfm\|spotify\|claude\|usda\|zotero>` | Connect a source |
| `python3 -m loop sync <source> --days N` | Pull or backfill a source |
| `python3 -m loop usda-build` | Build the offline USDA food database (about 5 minutes) |
| `python3 -m loop test` | Run a separate test instance with sample data |
| `python3 -m loop demo` | Serve sample data from a separate demo database |
| `python3 -m loop reset-password` | Reset the local account password |
| `python3 -m loop publish-brain --dry-run \| --on \| --off` | Preview or toggle the website snapshot |
| `python3 -m loop hub` | Run a Together hub |

## Architecture

- **Server:** `loop/server.py`, a Python standard-library HTTP server bound to 127.0.0.1:8765.
- **Background jobs:** `loop/runner.py` runs the Mac tracker every 5 seconds and the source syncs on their own schedules, in one launchd process (`com.loop.app`).
- **Storage:** SQLite in WAL mode, schema in `loop/db.py` and the feature modules.
- **Interface:** a progressive web app in `loop/static/`, plain JavaScript and CSS with no build step. It can be installed on a phone over a private network such as Tailscale.
- **Sources:** one module per integration in `loop/sources/`.

## Development

```sh
python3 -m unittest          # run the test suite
python3 -m loop test         # run a disposable instance at http://localhost:8767
```

Tests never use the real database or the Keychain. New network calls must be registered in `loop/egress.py`, and the tests check that private data does not reach exports, the AI summary or the hub.

## Documentation

- [INSTALL.md](INSTALL.md): installation for the Mac, Garmin, music, Android, iPhone and other computers
- [SECURITY.md](SECURITY.md): security model and how to report a vulnerability
- [docs/security-design.md](docs/security-design.md): security options under consideration and the reasoning behind them
- [CHANGELOG.md](CHANGELOG.md): release notes
- [howwebuilt/](howwebuilt/): the development story
