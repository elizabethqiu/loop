# What's new in loop

Versions follow MAJOR.MINOR.PATCH: a new MINOR for new features, a new PATCH for fixes. Within a version, changes are
grouped by type.

## Unreleased

### Search
- Periods: "last week", "this month", "march 2025", "summer 2025", "sep 1-7", "since june", "past 10 days". Shows averages
  for sleep, calories, steps, exercise, water, weight and deep work, a small daily chart for each, and links to the
  longest and shortest sleep, most steps and most calories in that period.
- Comparisons: "this week vs last week", "march 2025 compared to april 2025", side by side with the difference.
- Questions about your numbers: "when did I sleep the most", "fewest steps in 2025", "days over 2500 kcal", "last time
  I ate salmon", "first time I logged pilates". Answered from the database on the Mac.
- Meals by name, including loop's guessed names ("chicken salad bowl").
- Recent searches, kept in the browser, with a Clear option.

### Food
- Meal names in Eat: each meal shows loop's guess or the name you gave it, with rename. Names you give are remembered
  for the same set of foods.

### Sleep
- Moving toward a bedtime goal now goes at a speed worked out for you: up to 60 min a night earlier with morning light
  (90 min later), adjusted for age (15 min a night from 55), for teenagers, and for how steady the last week was. The
  Sleep tab's Methodology panel shows the working.

### Documentation
- New README, security policy and security design notes.

## 0.1.1 (2026-11-06)

### Today and planning
- To-dos: urgent / important (Eisenhower quadrants) with a List or Matrix view, and repeating to-dos ("every 3 days"):
  checking one off schedules the next.
- Lou on workload: when overdue work piles up she offers to go through it with you (do today, tomorrow, next week, a
  date, or let it go). When it's too much she makes the call: nothing new goes on until you push back or cut enough.
  Things pushed three times get a "commit or let it go".
- Notes dump on Today, capped at 15: one in, one out. Sort a note into a to-do (pick its quadrant) or let it go.
- Catch up cards (recap and heads up) from your own data, no AI: sleep debt, new workouts, what's next on your calendar,
  a connection that stopped, mood trend, a streak at risk, the goal you've hit least. Swipe left when you're done with
  one, right to bring it back later today, tomorrow or next week (Done / Later buttons do the same).
- "Your check-ins" lists what needs you: rate your day, tag what you spent time on, log meals (with a faint "I'm done
  for today"), a nudge to move until your step goal ("I did it" / "done for today" after 8pm), plus your own practices
  to check off (meditate, say your goals out loud, write them down, read your sticky notes; edit in You → Homepage).
- Guides: some links now open the exact setting and highlight it (e.g. Change your practices).

### Food
- Food card rebuilt: big amount with − / +, units (tsp, tbsp, cup, g, oz, the label's serving), fractions
  (1/2, 1 1/2, ¾), one-tap meal, wider star. Spoons use the label's own weight when it has one.
- Tap a logged food to change its amount, unit or meal, or remove it. Each food remembers its unit.
- A meal has one time: foods added later join it, and changing the time moves the whole meal. Each meal shows
  cal · carb · protein · fat · fiber.
- Copy a past meal: "Same as yesterday" appears on any meal that's empty today and you had yesterday, and the Add panel
  lists Recent meals from the past week. Untick anything you didn't have, then copy.
- Recipes: loop notices foods you keep having together in one meal and offers to save them, or you can save what you
  just added ("Save as recipe"). Log a recipe in one tap; change amounts or skip a food just this once, or keep the new
  amounts from now on. Stays on this Mac.
- A Trends card (calories, protein, carbs, fat, fiber, sugars or sodium over 30 days, 90 days or a year; tap a bar to
  open that day), and ‹ › arrows to move the week strip a week at a time.
- Quick log: Breakfast / Lunch / Dinner / Snack instead of "Ate". Meals take Eat's time for that meal, or your usual
  time on a past day; the confirmation says which day and time.
- Food groups: one "Carb" group by default; split into fruit / veg / grain in You → Food & meals.

### Sleep, health and mood
- "How are you today?" replaces "Are you happy with today?", with five faces: rough, meh, okay, good, excellent.
  loop's own rating of your day uses the same 1–5 scale (the 0–100 score is still shown underneath); the cut-offs sit
  a little below even fifths, so a solid ordinary day reads "good".
- Consume → Your mood: every rated day, month by month, a weekly average, and your notes (only you see it).
- Private history: Log → Private health → See all history (month by month), and Past journal entries.
- Workouts come from your Garmin watch automatically (runs, strength, Pilates…); you can still add one it missed.
- Reflect: six clearer tags (productive, made me think, enjoyed it, calmed me, made me anxious, waste of time).

### Focus and Build
- Focus map (Build): what you've been thinking about across screen time, commits, notes, drafts, Zotero and calendar,
  with a heatmap of where each day's time went and a flag for projects you haven't touched this week.

- Brain graph (Build): your Zotero papers, notes, to-dos, projects and Claude chats as an Obsidian-style graph,
  linked by projects, Zotero tags and ideas that recur across them. Import your claude.ai export to add your chats
  (titles and keywords only, kept on this Mac). Mark items "shareable later"; nothing is shared or sent yet.

- Brain graph → Curius: connect your Curius profile to see your saved links and highlights as their own graph, linked
  by site, topics and shared ideas. Public, read-only, no login; kept on this Mac.

### Easier to read
- Less to read at once: detail sections (body clock, energy tank, listening, screen time, the plate, reading, reflect
  and more) fold into one line with a short summary. Click to open; loop remembers what you keep open.
- Log is split into "From your watch" (steps, workouts: nothing to enter) and "You log these" (water, journal, body,
  caffeine…). On Eat, water sits under "you add", exercise and steps under "from your watch".
- Eat: one Add button per meal, and the add-food panel no longer jumps when it opens.
- Your trajectory lists each goal and its target on one line.
- Recently played shows your last 3 tracks; a replayed track shows its play count instead of repeating.

### Setup and settings
- Setup: five short steps (welcome, what you track and your top 3, connect, optional AI, a 30-second tour).
  Connections keep syncing in the background while you go. Replay any time: You → Password & sign-out → replay, or #/setup.
- You → What you track: turn areas on or off (sleep, movement, food, focus, what you consume, mood, private health,
  friends). Off hides tabs, cards, check-ins and catch up cards; nothing is deleted. Your top 3 lead the homepage.
- Connections can be switched off in You → Connectors (data stays; nothing new is collected).
- Test account: `python3 -m loop test` opens a throwaway copy at localhost:8767 (test / test) with fresh demo data every
  start. It never touches your real database, Keychain, Garmin session or picture.
- The feedback inbox now says exactly what to change when Google refuses a submission.

### Privacy and security
- You → Where your data goes: every place loop can send data (what, to whom, when, how to turn it off), what's stored on
  this Mac, and a log of each request with what was sent (never the secret itself). A test fails if new code sends data
  without being listed and logged.
- Secrets in the macOS Keychain (encrypted, unlocked by your Mac password): your AI keys, Last.fm key, Zotero key and
  private calendar link. Existing ones move there automatically when loop starts and the old copy is scrubbed.
- The "How are you today?" note saves on its own and is private: never sent to AI or shared, and left out of exports
  unless you include private data. Ask sends only your 1–5 ratings, never mood notes.
- All notes are private (no more privacy levels). A side project moved to its own repo.
- The feedback inbox link is no longer sent to the browser or included in exports, and loop only answers to this
  computer's own addresses (blocks DNS-rebinding tricks from websites).
- "Delete all private data" now also clears SQLite's write-ahead log, so deleted entries can't linger on disk.
- Removed unused code.

### Fixes
- Logging on a past day with no time saved it to today.
- Pasting a key you'd just copied from another tab could be wiped by a refresh when you came back, and any click
  reloaded the current page in the background, clearing text boxes right after you clicked into them.
- Sunrise and sunset far north (near the Arctic Circle) could crash the homepage and focus plan.
- Ending a break early now sticks; it no longer comes back a minute later.
- Looking at tomorrow's plan no longer lengthens your focus blocks early.
- A focus block that ends while loop isn't open still counts toward growing your blocks, and an old unfinished block
  is never marked complete by mistake.
- Streaks are no longer capped at 14 days on some screens.
- Citation links on the Methods page jump to the source instead of sending you to Today.
- Cycle day when viewing a past date; the cycle strip shows all six weeks.
- Big numbers on Progress (and elsewhere) were misaligned; long names in the Progress list now wrap.
- A serving that is only a weight (e.g. "30 g") no longer shows as its own unit.

## 0.1.0 (2026-11-05)

The first numbered release. Everything built so far:

### Today
- Health and work scores with open methods, a focus plan, to-dos (with undo and "recently done"), calendar with
  details you can pin open and live countdowns, your health at a glance, and a trajectory for the goals you choose.
- Streaks count days at 70%+ of a goal; "Ready for more?" offers to raise the bar, and your streak is protected
  for 14 days while you adjust.

### Focus training
- Deep work planned like training: pick a style (deliberate practice, long blocks, pomodoro, custom), start where
  you are, and blocks grow as you complete them. 3 hours a day by default, backed by research on deliberate practice.
- Blocks are placed in your energy peaks, inside your work hours, preferring daylight; your calendar is only used
  after you say yes in a reflection. A live countdown runs each block and the break after it.
- Lou the leopard nudges you when you switch apps and tabs a lot.

### Sleep
- Last night as one smooth line, your body clock (melatonin, wake zone, caffeine), a suggested bedtime that eases
  you toward your goal, and an energy tank that sleep refills. Every number shows its methodology.

### Eat
- A dashboard with calories left, each meal, macros and the week, and one "+" to log a whole meal quickly.
- Guardrails: calorie targets never below your BMR or 1,200 / 1,500 kcal; added sugar defaults to the WHO's 25 g.

### Consume
- "Your day": loop's rating, time spent, what you focused on and searched, what you ate, what could be better,
  and your own rating, which loop learns from. Music is one row; Zotero papers show up too.

### Private and yours
- Private health (cycle, symptoms, medications) never leaves your Mac.
- Export everything as one .zip: JSON Lines and CSV, a schema, a Markdown summary and llms.txt for AI, and a
  readable HTML report.
- Sunrise and sunset theme, computed locally from a rounded location that's never sent anywhere.
