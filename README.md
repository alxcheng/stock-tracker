# Stock Tracker

A stock watchlist that runs on your own computer. It shows each stock's **14-day stochastic** (green when overbought, red when oversold), how it sits against its **50-day moving average**, and your **positions' gains and losses**. It sends **phone alerts** when something needs attention.

Everything runs locally, and it's free. The app only talks to Yahoo Finance for prices, ntfy.sh for phone alerts, and GitHub to check for updates.

## Install

**Mac:** open **Terminal** (search for it in Spotlight), paste this line, and press Return:

```bash
curl -fsSL https://github.com/alxcheng/stock-tracker/releases/latest/download/install.sh | sh
```

**Windows:** open **PowerShell** (search for it in the Start menu), paste this line, and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/alxcheng/stock-tracker/releases/latest/download/install.ps1 | iex"
```

It takes about a minute and needs no admin password. When it finishes, the page opens in your browser. Open it again any time from the **Stock Tracker** app (Mac: Launchpad or Spotlight; Windows: desktop or Start menu). It runs in the background and starts by itself when you log in.

## Set up phone alerts (2 minutes)

Scroll to **Alerts & updates** at the bottom of the page:
1. Install the free **ntfy** app on your phone.
2. Subscribe to the topic shown on the page. Each install gets its own private topic.
3. Press **Send a test alert**.

You'll get pings when:
- a stock on one of your lists is **overbought or oversold**;
- a stock **drops below its 50-day moving average**;
- one of your positions, or your whole portfolio, is **up 40% or more**.

You choose which of these each list sends (see below).

Checks run on weekdays at 10am, 12pm, 2pm and 4pm US Eastern while your computer is on. If it was asleep, the missed check runs when it wakes up.

## Lists and custom alerts

- **Make as many lists as you like**, such as "Holding", "Ideas" or "Tech", with **+ New list**.
- **Each list has its own alerts switch.** Choose what it alerts on: overbought, oversold, or dropping below the 50-day average. A list can also stay silent and just be for looking.
- **Position alerts have their own switch** in the Positions section, where you can change the +40% threshold too.
- **Share a list:** press **Share** on a list and send the link. When someone with Stock Tracker opens it, their app offers to add the list, with your alert settings as a suggestion. If a link gets mangled in a message, they can paste the code (starts with `ST1-`) into **Import**. The list travels inside the link itself; it isn't uploaded anywhere.

## Updates

Updates install automatically once a day, so there's nothing to do. Each update is downloaded and tested before it's switched on. If a new version fails to start, the app goes back to the previous one on its own. The page shows a short note after an update.

## Uninstall

Your watchlist and positions are kept unless you ask for them to be deleted.

- **Mac:**
  ```bash
  ~/"Library/Application Support/Stock Tracker/uninstall.sh"
  ```
  Add `--delete-data` to remove everything.
- **Windows:**
  ```powershell
  powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\StockTracker\uninstall.ps1"
  ```
  Add `-DeleteData` to remove everything.

---

## For the maintainer

### How it fits together
| Piece | What it does |
|---|---|
| `app.py` | The local web server at http://127.0.0.1:8050. It runs the alert schedule (`scheduler.py`) and the daily update check (`updater.py`) itself, so each OS only needs one background job. |
| `tracker.py`, `stoch.py`, `positions.py`, `lists.py` | Market data (yfinance), the indicators, positions, lists with their alert rules and share codes, and alert logic. |
| `bootstrap.py` | Started at login: launchd on Mac, the Startup folder with `pythonw` on Windows. It runs the current version, restarts it after updates, and rolls back an update that keeps crashing. Standard library only. |
| `install.sh`, `install.ps1` | Install `uv` into the user's home folder; uv brings its own Python. They download the release, verify its checksum, install the locked dependencies, run `selftest.py`, and register the background job. |

Install folder: `~/Library/Application Support/Stock Tracker` on Mac, `%LOCALAPPDATA%\StockTracker` on Windows. Code lives in `versions/<version>/`, each with its own `.venv`. The person's data lives in `data/`, which updates never touch.

### Develop
```bash
uv sync
```
```bash
uv run pytest -q
```
```bash
STOCK_TRACKER_PORT=8051 uv run python app.py
```
A dev checkout keeps its data (`lists.json`, `positions.json`, `settings.json`) next to the code. Those files are git-ignored, and automatic updates are off in a dev checkout.

### Release
```bash
./release.sh 1.1.0 "What changed, in a sentence or two"
```
It runs the tests, bumps `VERSION`, tags the commit, builds `stock-tracker.zip` plus its checksum, and publishes a GitHub release. Every install picks it up within a day. The notes appear in the update banner.

CI (`.github/workflows/ci.yml`) runs the tests on macOS and Windows. On both, it installs a local test release with the real installer and checks that the auto-update switches to a newer one.

### Settings
`config.toml` holds the defaults:
- stochastic period and thresholds;
- MA period;
- the +40% gain alert;
- schedule times.

To change a setting for one person, put a `config.toml` with only the keys that differ in their `data/` folder.
