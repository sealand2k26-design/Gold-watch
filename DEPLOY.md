# Put it on the web — free, always on, no server to die

This turns the tracker into a real website with its own address that works on
your phone, updates itself every 15 minutes, and keeps running whether or not
anyone is talking to me.

**Cost: nothing. No credit card. No server to maintain.**

---

## How it works

GitHub runs the data-fetching for you on a timer and republishes the page.

```
every 15 min  →  GitHub's computer wakes up
              →  runs build_site.py
              →  fetches gold, oil, dollar, Fed data
              →  rewrites docs/index.html
              →  your website shows the new numbers
```

Nothing on your side is switched on. There is no server to crash, no preview
link to expire, no sandbox to recycle. A static page cannot go down.

---

## Setup — five steps, about ten minutes

### 1. Make a GitHub account
<https://github.com/signup> — free.

### 2. Make an empty repository
Click **New repository**. Name it `gold-watch`. Set it to **Public**
(GitHub Pages is only free on public repos). Don't tick any of the
"initialise with" boxes.

### 3. Upload these files

Download the `gold-watch` folder from this workspace and push it:

```bash
cd gold-watch
git init
git add .
git commit -m "gold tracker"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/gold-watch.git
git push -u origin main
```

Not comfortable with the command line? On the repo page click
**Add file → Upload files** and drag the whole folder in. You must include the
hidden `.github` folder — if your file manager hides it, press
`Cmd+Shift+.` (Mac) or tick "Hidden items" (Windows).

### 4. Switch on Pages
Repo → **Settings** → **Pages** →
Source: **Deploy from a branch** → Branch: **main**, folder: **/docs** → **Save**.

Your address appears within a minute:

```
https://YOUR-USERNAME.github.io/gold-watch/
```

Bookmark it on your phone's home screen.

### 5. Let the robot run
Repo → **Actions** tab → if prompted, click **I understand my workflows,
enable them**. Open **Update gold page** → **Run workflow** to do the first
build immediately. After that it runs itself every 15 minutes.

---

## Checking it is alive

- **Actions** tab shows a green tick for every run. A red X means a feed broke.
- The page prints its own timestamp at the top — "Snapshot taken 09:09 UTC".
  If that stamp is hours old, the robot has stopped.
- The page also refreshes itself in your browser every 15 minutes, so you can
  leave the tab open.

---

## Honest limitations

| | |
|---|---|
| **Delay** | GitHub queues free cron jobs. "Every 15 minutes" is really 15–25. Fine for a tranche buyer; useless for a day trader. |
| **Feeds can refuse** | Yahoo occasionally rate-limits GitHub's shared IPs. The build keeps the last good value and carries on rather than publishing a blank page. |
| **Five inputs are still manual** | Oracle CDS, CoreWeave yields, central-bank tonnage, GLD tonnage, Fed odds. Edit `config.json`, commit, done. |
| **Dormancy** | GitHub pauses schedules on repos with no commits for 60 days. The bot's own commits reset the clock, so this only bites if everything is already broken. |
| **It reads numbers, not meaning** | It will show you the Fed statement on 9 December. It cannot tell you what Warsh *meant*. |

---

## If you would rather host the live app

The minute-by-minute Flask version runs anywhere that keeps a process alive —
Render, Railway and Fly all have free tiers:

```
Build command:  pip install -r requirements.txt
Start command:  python3 app.py
```

It reads `PORT` from the environment, which is what those platforms set.

The trade-off: free tiers sleep after ~15 minutes idle and take 30+ seconds to
wake. **For your use case the static site is the better choice** — it is never
asleep, never slow, and never down.

---

## Want alerts instead of checking?

The same GitHub Action can email you only when a line is crossed — below
$3,400, below $3,268, above $3,750, or on a Fed day. That is the version
worth having: silence until something actually matters. Ask me and I'll add it.
