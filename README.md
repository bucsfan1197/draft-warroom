# Draft War Room — put it online, free, on every device

This gives you **one web link** (e.g. `https://YOURNAME.github.io/draft-warroom/`) that works on your
phone, laptop, and any in-person draft — no login, no app store. Your desktop PC runs one Python
script all day that keeps the data fresh (ADP, projections, injuries) and pushes it to the site
automatically.

**How it works, in one sentence:** your PC pulls fresh data → pushes it to GitHub → GitHub serves your
site for free → you open the link anywhere. The site stays up even if your PC is off; it just won't
update until the PC is back on.

---

## What's in this folder

| File | What it does |
|------|--------------|
| `index.html` | The tool itself (the dashboard you already use). |
| `data.js` | All the player data. **This is the file that gets refreshed.** |
| `base.json` | Historical stuff that doesn't change mid-season (backtests, matchup math). Used by the script. |
| `refresh.py` | The script your PC runs all day. Pulls fresh data, rebuilds `data.js`, pushes it. |
| `start.bat` | Double-click this to run the script (so you don't have to type). |
| `requirements.txt` | The two Python packages the script needs. |

---

## One-time setup (about 15 minutes, you only do this once)

### 1. Install the two things you need
- **Python** — you already have it (you run bots). ✅
- **Git** — download from **https://git-scm.com/download/win** and click Next through the installer
  (all defaults are fine).

Then open a terminal **in this folder** (in File Explorer, click the address bar, type `cmd`, press Enter)
and install the Python packages:
```
pip install -r requirements.txt
```

### 2. Make a free GitHub account
Go to **https://github.com/signup** and make an account. Remember your username.

### 3. Create an empty repository
- Go to **https://github.com/new**
- **Repository name:** `draft-warroom`
- Set it to **Public**
- **Do NOT** check "Add a README" (leave everything unchecked)
- Click **Create repository**

Leave that page open — you'll need the web address it shows you, which looks like:
`https://github.com/YOURNAME/draft-warroom.git`

### 4. Connect this folder to your repository
In the terminal you opened in step 1 (make sure it's in this folder), run these lines **one at a time**.
Replace `YOURNAME` with your GitHub username:
```
git init
git branch -M main
git add .
git commit -m "first upload"
git remote add origin https://github.com/YOURNAME/draft-warroom.git
git push -u origin main
```
On the last line a browser window pops up asking you to sign in to GitHub — do it once, and Git
remembers it forever. (If it asks in the terminal instead, choose "Sign in with a browser".)

### 5. Turn on the free website (GitHub Pages)
- On your repository page, click **Settings** (top right).
- In the left menu click **Pages**.
- Under **Branch**, pick **main**, keep the folder as **/ (root)**, click **Save**.
- Wait ~1 minute, refresh the page. It will show your live link:
  **`https://YOURNAME.github.io/draft-warroom/`**

### 6. Open it and bookmark it
Open that link on your **laptop** and your **phone**. Add it to your home screen / bookmarks.
That's the tool — same one, now everywhere.

### 7. Start the daily refresher
Back on your desktop PC, **double-click `start.bat`**. A window opens and starts pulling data.
Leave it open (or minimized) all day. Every 6 hours it pulls fresh numbers and pushes them; your
website updates about a minute later, on its own.

**You're done.** From now on, the only thing you ever do is make sure `start.bat` is running on your PC.

---

## Daily use
- **On your PC:** double-click `start.bat` in the morning (or leave it running 24/7). That's it.
- **Anywhere else:** just open your bookmark. It always shows the latest data your PC pushed.
- Want to force an update right now? Close the window and re-run `start.bat`, or in the terminal run
  `python refresh.py --once`.

## Good to know
- **Change how often it refreshes:** open `refresh.py`, change `REFRESH_HOURS = 6` near the top.
- **The site is up but data looks old:** your PC's `start.bat` isn't running, or lost internet. Re-run it.
- **`git push failed` in the window:** you skipped step 4, or GitHub sign-in didn't stick. Redo step 4.
- **Your picks/settings are saved per-device** (in the browser), so your phone and laptop each keep
  their own draft in progress — handy if you run two drafts at once.
- **Privacy:** your GitHub repo is public, meaning anyone with the exact link could view the tool.
  There's nothing personal in it (just public projections), so that's fine. If you'd rather it be
  unlisted, that's a paid GitHub feature — not needed for this.

## Next season
When 2027 rolls around, the historical `base.json` and the season year in `refresh.py`
(`SEASON = "2026"`) need a one-line bump. Ping me then and I'll regenerate it.
