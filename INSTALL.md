# Installing loop

loop runs on your **Mac**: tracking, syncing, the database and the app itself all live there.
Every other device (Android phone, iPhone, another computer) opens the Mac's loop over a private
network and can be added to its home screen like a normal app.

```
Garmin watch ──bluetooth──▶ Garmin Connect (phone) ──▶ Garmin cloud ──┐
Spotify / Apple Music ──▶ Last.fm ──────────────────────────────────────┤
                                                                         ▼
                                              Mac: loop (tracker + syncs + database + app)
                                                                         ▲
                             phone / tablet / laptop ── Tailscale ──────┘
```

Contents:

1. [Mac (required)](#1-mac-required)
2. [Garmin watch](#2-garmin-watch)
3. [Music](#3-music)
4. [Android](#4-android)
5. [iPhone / iPad](#5-iphone--ipad)
6. [Another computer](#6-another-computer)
7. [Troubleshooting](#troubleshooting)
8. [Uninstalling](#uninstalling)

---

## 1. Mac (required)

**You need:** macOS and Python 3.10 or newer (check with `python3 --version`).

```sh
cd loop                     # the folder you cloned
python3 -m loop install
```

Open **http://127.0.0.1:8765**.

**You only run `install` once.** It registers loop with macOS so it starts on its own every
time you log in, and restarts it if it crashes. You don't need to run it again after a restart
or update. The exceptions are if you move the loop folder, or you ran `uninstall`. It runs:

- the Mac activity tracker (frontmost app, site and tab title, music playing)
- scheduled Garmin and Last.fm pulls
- the app at port 8765

**Allow the browser prompts.** The first time you use Chrome, Safari, Arc, Brave or Edge while
loop is running, macOS asks whether "python3" may control that browser. Click **Allow**, or
loop only sees "Chrome" and not which site you're on.
If you clicked Don't Allow: **System Settings → Privacy & Security → Automation → python3**,
then turn the browser on.

Firefox can't share its tabs this way, so it's counted as the app only.

**Check it's working:** the Today tab → **Sources** should show *Mac activity: recording*.
Logs are in `~/.loop/loop.log`.

**Want to see it first?** `python3 -m loop demo` serves 5 weeks of fake data from a separate
demo database. Stop it with Ctrl-C.

---

## 2. Garmin watch

### On the watch and phone

- **Wear the watch to bed.** Sleep is what most of loop's predictions run on.
- Keep the **Garmin Connect** app installed on your phone and paired with the watch. loop
  doesn't talk to the watch directly. It reads what Garmin Connect uploads to Garmin's cloud.
- So syncs don't get killed in the background: **Settings → Apps → Garmin Connect → App battery
  usage → Unrestricted**.
- **Open Garmin Connect after you wake up.** That pushes last night's sleep to the cloud, so
  loop's bedtime and wake predictions use it.

### On the Mac (once)

```sh
pip3 install garminconnect
python3 -m loop connect garmin        # email, password (not saved), MFA code if you have one
python3 -m loop sync garmin --days 30 # backfill a month of history
```

After that, loop pulls on its own every 60 minutes. You can change this under
**Log → Settings → Garmin pull every**, minimum 15. There's also a **sync now** button in
Sources.

Notes:

- Some watches, such as the Forerunner 235, don't record overnight HRV. On those, the HRV warning stays off and loop says so.
- This uses an unofficial Garmin login. If Sources turns red with a login error, run
  `python3 -m loop connect garmin` again.

---

## 3. Music

### Spotify (direct, recommended)

1. Go to <https://developer.spotify.com/dashboard> → **Create app**. Any name and description.
   Redirect URI: `http://127.0.0.1:8766/callback`. API: **Web API**. Save.
2. Copy the app's **Client ID**, then on the Mac:

```sh
python3 -m loop connect spotify   # paste the Client ID; a browser opens, click Agree
```

loop pulls every 15 minutes with real song lengths. No password or secret is stored, only a
token that can read what you've played.

The limit is that Spotify only shares your **last 50 plays**. If the Mac is asleep through
hours of phone listening, the oldest plays can be lost.

### Last.fm (optional, for a gap-free history)

Last.fm records every play on its own servers, so nothing is lost while the Mac sleeps. It
also covers Apple Music and YouTube Music through scrobbler apps.

1. Create a free account at [last.fm](https://www.last.fm), then **Settings → Applications →
   Spotify → Connect**.
2. Get a free API key at <https://www.last.fm/api/account/create>.
3. `python3 -m loop connect lastfm`

You can connect both. Songs that show up in both aren't counted twice.

---

### Food database (once, about 5 minutes)

loop searches a local copy of USDA FoodData Central: 1.9 million foods, private and offline. The
downloaded zips live in `data/usda/raw/` (462 MB, not committed to git):

```sh
python3 -m loop usda-build        # builds data/usda/usda.db (~740 MB) from the zips, about 2.5 min
```

To refresh with a newer USDA release, put the new zips in `data/usda/raw/` (from
<https://fdc.nal.usda.gov/download-datasets>) and run it again. Without the local database,
search falls back to the USDA website; a free key from <https://api.data.gov/signup> plus
`python3 -m loop connect usda` lifts its hourly limit.

### Ask loop (optional)

```sh
pip3 install anthropic
python3 -m loop connect claude     # paste an Anthropic API key (stored only on this Mac)
```

Ask loop sends a summary of your stats to Claude when you ask a question. Progress → "what gets
sent?" shows exactly what it sends.

---

## 4. Android

The phone shows the same live dashboard, Log buttons and Reflect queue, served by your Mac
through **Tailscale**, a free private network between your own devices. loop is never exposed
to the internet.

### Once, on the Mac

1. Install Tailscale: `brew install --cask tailscale`, or download it from
   [tailscale.com/download](https://tailscale.com/download). Open it and sign in.
2. Give loop a secure address:

   ```sh
   tailscale serve --bg 8765
   ```

   If it says HTTPS isn't enabled, open the link it prints and turn on **HTTPS certificates**
   (and **MagicDNS** if asked), then run the command again. It prints an address like
   `https://your-mac.tail1234.ts.net`. That's your loop address from now on.

   `tailscale` command not found? With the downloaded app, the command lives inside it:
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8765`

### On the phone

1. Install **Tailscale** from the Play Store and sign in with the **same account**.
2. Open your loop address in **Chrome**.
3. ⋮ menu → **Add to Home screen** → **Install**.

loop now opens from its own icon, full-screen. Long-press the icon for **Log** and **Reflect**
shortcuts.

On the phone, **Eat → Scan** reads barcodes with the camera. That only works from the
`https://…ts.net` address.

**The Mac has to be awake** for the phone to reach loop. To stop a plugged-in Mac from
sleeping: **System Settings → Battery → Options → Prevent automatic sleeping when the display
is off**. Tracking and syncing keep running on the Mac whether or not the phone is connected.

---

## 5. iPhone / iPad

Do the Mac setup in [section 4](#once-on-the-mac) first. Then:

1. Install **Tailscale** from the App Store and sign in with the same account.
2. Open your loop address in **Safari**.
3. **Share** button → **Add to Home Screen** → **Add**.

---

## 6. Another computer

- **Any laptop (Mac, Windows, Linux):** install Tailscale, sign in with the same account, and
  open your loop address in a browser. In Chrome or Edge you can also use **⋮ → Cast, save,
  and share → Install page as app**. The dashboard shows the main Mac's data. It doesn't
  track the laptop.
- **Tracking a second Mac too:** not supported yet. Each Mac would keep its own separate
  database.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Page won't load on the Mac | `launchctl list \| grep com.loop.app` should show a line. If it doesn't, run `python3 -m loop install` again. Check `~/.loop/loop.log`. |
| Sources: Mac activity "not running" | Same as above. |
| Browser shows as "Google Chrome", not the site | Allow Automation (see [section 1](#1-mac-required)). Also: Chrome with no window open has no site to report. |
| Mac time looks too low | Time stops after 2 min without keyboard or mouse input, and while the screen is locked. Watching and listening are the exception. This is on purpose. |
| Garmin red: "session expired" or "login" | `python3 -m loop connect garmin` |
| Garmin green but last night's sleep missing | Open Garmin Connect on the phone so the watch syncs, then press **sync now**. |
| Phone can't reach loop | Is the Mac awake? Are both devices signed in to Tailscale with the same account? Does `tailscale serve status` on the Mac show port 8765? |
| Phone shows "Add to Home screen" but no **Install** | Use the `https://…ts.net` address, not an IP address. Chrome only installs apps from HTTPS addresses. |
| Moved the loop folder | `python3 -m loop install` again from the new location. |

---

## Importing your MyNetDiary history

```sh
python3 -m loop import-mynetdiary ~/Downloads/FoodTracker_2025.xls ~/Downloads/FoodTracker_2026.xls
```

This brings in food entries with all their logged nutrients, workouts, water and weight. Numbers
opens briefly to convert the .xls files, all on your Mac. Running it again later only adds new
entries, so you can re-export from MyNetDiary any time.

---

## Uninstalling

```sh
python3 -m loop uninstall     # stop loop and remove it from login
tailscale serve reset         # stop sharing it with your devices (if you set this up)
```

Your data stays in `~/.loop/`: the `loop.db` database and the Garmin session in
`garmin_tokens/`. Delete that folder yourself if you want it gone.


## Test account (for trying things, demos and reviewing the setup)

```bash
python3 -m loop test        # then open http://localhost:8767
```

- Username: `test`, password: `test`.
- It's a separate database (`~/.loop/test.db`) filled with demo data, wiped and re-seeded **every time the command starts**,
  so the setup and tour run fresh each time.
- It can't touch your real loop: it never opens `~/.loop/loop.db`, never reads or writes your Keychain, doesn't use your
  Garmin session or profile picture, and logs its requests to `~/.loop/test-egress.log`. The test login doesn't exist in
  your real database.
- It uses `localhost` (not `127.0.0.1`), so signing in doesn't sign you out of your real loop in the same browser.
