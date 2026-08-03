# Video Asset Downloader — Setup & Usage Guide

A reusable Python tool for bulk-downloading archival, copyright-free images and videos for your YouTube episodes. Point it at any list of URLs, and it downloads direct media files AND scrapes images from webpages into a clean folder structure.

---

## FIRST-TIME SETUP (5 minutes, one-time only)

### Step 1 — Install Python (if you don't have it)

**Windows:** Download from https://www.python.org/downloads/ — during install, **check the box that says "Add Python to PATH."**

**Mac:** Python 3 is pre-installed, but the current version is old. Install a fresh one from https://www.python.org/downloads/ or via Homebrew: `brew install python3`.

**Verify:** open a terminal (Command Prompt on Windows, Terminal on Mac) and type:
```
python --version
```
You should see `Python 3.10` or higher. On some Macs the command is `python3` instead.

### Step 2 — Install the 3 required packages

In your terminal, run:
```
pip install requests beautifulsoup4 tqdm
```
On Mac, if that fails, try:
```
pip3 install requests beautifulsoup4 tqdm
```

Done. You're set up forever.

---

## HOW TO USE IT

### Running the Port Chicago 50 downloader (right now)

1. Put both files (`download_assets.py` and `port_chicago_50_assets.txt`) in the same folder.
2. Open a terminal in that folder. On Windows: Shift+Right-click inside the folder → "Open in Terminal" or "Open PowerShell here."
3. Type:
```
python download_assets.py port_chicago_50_assets.txt
```
4. Wait 5–15 minutes depending on your connection.

**Output:** everything lands in `./downloads/port_chicago_50_assets/`
- `media/` — direct video and image files
- `scraped/` — images extracted from webpages, one folder per source page
- `download_report.txt` — full list of what was downloaded and what failed

The script is safe to re-run. Already-downloaded files are skipped, so if it crashes or you cancel, just run it again to resume.

---

## USING IT FOR YOUR NEXT VIDEO

**The whole point of this tool** is that you never build another asset list by hand. Here's the workflow for every future topic:

### Step 1 — Ask me to generate a new asset list

When you're ready to research the next episode (say Charles Jackson French, or Freddie Stowers), just tell me the topic. I'll build you a new `<topic>_assets.txt` file with verified URLs, and you drop it in the same folder as the script.

### Step 2 — Run the same command with the new filename

```
python download_assets.py charles_french_assets.txt
```

That's it. Same tool, new topic. Downloads to `./downloads/charles_french_assets/`.

---

## ASSET LIST FILE FORMAT

If you want to build asset lists yourself, here's the format. It's very forgiving.

**Rules:**
- One URL per line, starting with `http://` or `https://`
- Lines starting with `#` are comments (ignored)
- Blank lines are ignored
- Optional labels: `Photo: https://...` — the script picks out the URL and ignores the label

**Example:**
```
# WWII Home Front — assets
# ------------------------

# NPS B-roll video
https://www.nps.gov/nps-audiovideo/example.mp4

# Wikimedia file — script auto-derives direct download URL
https://commons.wikimedia.org/wiki/File:Some_Photo.jpg

# Archive.org page — script auto-derives direct MP4 URL
https://archive.org/details/some-newsreel-id

# LoC search page — script scrapes all substantial images from the page
https://www.loc.gov/pictures/search/?q=some+query&fa=access-restricted%3Afalse
```

---

## WHAT THE SCRIPT AUTO-HANDLES

Because these are the archives you'll use most often, the script knows how to handle them intelligently:

| Site | What you paste | What the script does |
|---|---|---|
| Direct file URL (ends in .mp4/.jpg/.png/etc.) | The URL | Downloads it |
| Any URL where the server says it's media | The URL | Downloads it |
| `archive.org/details/ITEM` | The page URL | Auto-tries `/download/ITEM/ITEM.mp4` |
| `commons.wikimedia.org/wiki/File:X.jpg` | The page URL | Auto-uses `Special:FilePath/X.jpg` (direct file) |
| Any HTML page with images | The page URL | Scrapes ALL images ≥20 KB into a subfolder |

---

## COMMAND-LINE OPTIONS

```
python download_assets.py <asset_file> [options]
```

| Option | What it does | Default |
|---|---|---|
| `--topic NAME` | Custom folder name for this run | filename stem |
| `--output DIR` | Where to save everything | `./downloads` |
| `--min-size KB` | Skip images smaller than this when scraping (helps skip icons/logos) | 20 |

**Example — get bigger images only, and give it a custom name:**
```
python download_assets.py my_list.txt --topic freddie_stowers --min-size 100
```

---

## TROUBLESHOOTING

**"pip: command not found"** — try `pip3` instead. Or on Windows, close and reopen the terminal after installing Python (PATH refresh).

**"python: command not found"** — same. Try `python3` on Mac/Linux.

**Downloads are very slow** — some archives (LoC in particular) are slow servers; that's not the script. Grab a coffee.

**"SSL certificate error"** — usually only on very old Windows setups. Update Python or run `pip install --upgrade certifi`.

**A specific URL fails** — check the report file. If a site is temporarily down, wait an hour and re-run; already-downloaded files are skipped so you won't repeat work.

**Getting rate-limited** — this happens if you scrape the same archive too aggressively. The script has built-in delays and retries, but if a site starts refusing, wait 30 minutes and try again. NHHC and NPS are the strictest.

**Wikimedia file didn't download** — the file may have been renamed on Commons. Open the File: page in your browser, note the current filename, and update the URL in your asset list.

---

## FILES IN THIS TOOLKIT

- `download_assets.py` — the script itself
- `port_chicago_50_assets.txt` — the ready-to-run list for your current episode
- `README.md` — this file

Keep all three together in a folder like `~/Documents/valor_untold_downloader/`. Every future asset list drops in the same folder and runs the same way.

---

## HOW THIS SAVES YOU TIME OVER MANY EPISODES

**Per episode without this tool:** ~2 hours hunting archives, right-clicking, saving files, organizing folders.

**Per episode with this tool:** ~10 minutes writing the asset list (or asking me to), then 15 minutes of unattended download time while you do something else.

Over 10 episodes = ~17 hours saved.
