# Stochastic Tracker

Checks a watchlist's **14-day stochastic oscillator** (%K / %D) every 2 hours during US market hours, and pings your phone when a stock is **overbought (%K ≥ 80)** or **oversold (%K ≤ 20)**. It runs entirely on free services.

```
NVDA   OVERSOLD   %K  18.1  %D  23.5  $217.10  NEW
```

`NEW` means the stock entered the zone since the previous close. Those alerts go out at high priority.

It also watches the **50-day moving average**. When a stock closed above its 50-day MA at the previous close and is trading below it now, it sends a high-priority ping:
```
Fell below 50-day MA:
QQQ    $723.88  50d MA $726.33  (-0.3%)
```

## Web watchlist
```bash
.venv/bin/python app.py
```
To keep it running all the time, starting at login and restarting if it crashes, run this once. The log goes to `web.log`.
```bash
./install_web_launchd.sh
```
Then open http://127.0.0.1:8050. On this page you can:
- Add and remove tickers.
- See each stock's price and daily gain/loss.
- See %K and %D on a 0–100 bar. Overbought rows are **green** and oversold rows are **red**.
- See how far each stock is above (▲) or below (▼, red) its 50-day MA, with NEW on the day it crosses below.

### Positions
The **Positions** section at the top tracks what you own. It shows market value, today's gain/loss, and total gain/loss in dollars and percent for each holding and for the whole portfolio.
- **Add by price:** type the ticker, shares and the price you paid, then click **Add position**. If you buy the same stock more than once, each buy is kept separately and the holding shows your average cost.
- **+ / − in the watchlist:** **+** fills in the form with that stock at today's price, so you only enter shares. **−** removes your position in that stock; click it twice to confirm.
- **+40% alert:** the 2-hour job sends one high-priority ping when a holding, or the whole portfolio, reaches +40%. If it drops back below and climbs past again, you get another ping. Change the threshold with `gain_alert_pct` under `[positions]` in `config.toml`.

Positions are saved in `positions.json`, which is git-ignored so your holdings don't end up on GitHub. That means the +40% alert only works in the local (launchd) setup.

Click a column header to sort. The page refreshes every 5 minutes. The list is saved to `watchlist.json`, and the 2-hour alert job reads the same file, so a stock you add on the page gets alerts too.

## How it stays free

| Piece | Choice | Why |
|---|---|---|
| Market data | **yfinance** (Yahoo Finance) | No API key and no hard quota. It fetches the whole watchlist in one request. |
| Scheduler | **GitHub Actions cron** | Free and unlimited for public repos. The workflow re-enables itself so GitHub's 60-day inactivity shutoff never kicks in. |
| Alerts | **ntfy.sh** | Free push notifications to iOS and Android. No account, one HTTP POST. |
| Alt. alerts | Discord webhook, Telegram bot, macOS banner | Also free. Turn on any combination. |

### Data alternatives (if Yahoo ever breaks)
yfinance is unofficial: it scrapes Yahoo and is for personal use. Free keyed APIs that could replace it:
- **Twelve Data**: about 800 calls/day, 8/min.
- **Finnhub**: about 60 calls/min. Personal use only.
- **Alpha Vantage**: only about 25 calls/day, so it fits small watchlists only.

With ~10 tickers checked 4×/day you'd use ~40 calls/day. That fits Twelve Data easily; Alpha Vantage would be tight.

## The math

```
%K raw = 100 × (Close − Lowest Low₁₄) / (Highest High₁₄ − Lowest Low₁₄)
%K     = 3-period SMA of %K raw     ← "slow" stochastic, the 14,3,3 most charts show
%D     = 3-period SMA of %K
```
Data is daily bars. During market hours the last bar is today's bar so far, so each 2-hour check uses the latest price. Set `smooth_k = 1` in `config.toml` for the fast stochastic.

## Setup

### 1. Get notifications on your phone (ntfy)
1. Install the **ntfy** app (App Store / Google Play).
2. Subscribe to a topic name that's hard to guess, e.g. `stoch-alex-8f3k2q`. Anyone who knows the name can read the topic.

### 2. Option A: run in the cloud with GitHub Actions (recommended, works when your Mac is off)
```bash
git init && git add . && git commit -m "Stochastic tracker"
```
```bash
gh repo create stochastic-tracker --public --source . --push
```
```bash
gh secret set NTFY_TOPIC --body "stoch-alex-8f3k2q"
```
```bash
gh workflow run stochastic.yml
```
The schedule is weekdays at 14:07, 16:07, 18:07 and 20:07 UTC. That's 10am, 12pm, 2pm and 4pm ET in summer, an hour earlier in winter. GitHub can start scheduled runs 5–30 min late. Each run's table appears in the Actions tab under the job summary.

> Private repos also work on the free plan (2,000 min/month, and this uses roughly 80). Some people report that schedules in private repos on free accounts don't fire reliably, so public is the safe choice. The repo holds no secrets; your topic stays in GitHub Secrets.

### 2. Option B: run on this Mac (launchd)
```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt
```
```bash
echo 'NTFY_TOPIC=stoch-alex-8f3k2q' > .env
```
```bash
./install_launchd.sh
```
By default it runs weekdays at 7:05, 9:05, 11:05 and 13:05 local time (Pacific), which is every 2h across the ET session. It also shows a macOS banner. It only runs while the Mac is awake.

## Configure
The watchlist is in `watchlist.json`, which the web page edits. It takes any Yahoo symbol: `BRK-B`, `SPY`, `^GSPC`, `BTC-USD`.

Edit `config.toml` for everything else:
- `period`, `smooth_k`, `d`: the stochastic settings.
- `overbought`, `oversold`: the thresholds.
- `line`: alert on `"k"` or `"d"`.
- `notify_when_quiet`: send a ping even when nothing is flagged.
- `[moving_average]`: `period` sets the MA length (default 50). Set `alert_on_cross_below = false` to keep the MA on the page but turn off its alerts.

Other alert channels are turned on by environment variables or secrets: `DISCORD_WEBHOOK_URL`, or `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

## Run manually
```bash
.venv/bin/python tracker.py --dry-run
```
```bash
.venv/bin/python -m pytest -q
```
