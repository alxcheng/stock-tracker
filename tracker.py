"""Check the watchlist and positions, and ping on anything worth knowing.

The web app runs this on a schedule (see scheduler.py). By hand:
  python tracker.py              # check and notify
  python tracker.py --dry-run    # check and print only
"""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

import lists
import notifiers
import positions
import settings
from market import RateLimited
from paths import CODE, DATA
from stoch import stochastic, zone

def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_config(path: Path | None = None) -> dict:
    """Shipped defaults (config.toml next to the code), overridden by an optional
    config.toml in the data folder, so new settings in updates just work."""
    cfg = tomllib.loads((CODE / "config.toml").read_text())
    user = path or DATA / "config.toml"
    if user.exists() and user.resolve() != (CODE / "config.toml").resolve():
        cfg = _merge(cfg, tomllib.loads(user.read_text()))
    return cfg


@dataclass
class Reading:
    ticker: str
    price: float
    prev_close: float
    k: float
    d: float
    zone: str
    prev_zone: str  # zone at the previous daily close
    sma: float | None = None  # N-day simple moving average of the close
    crossed_below: bool = False  # closed above the MA last session, below it now
    sma_long: float | None = None  # long (200-day) moving average
    death_cross: bool = False  # the 50-day MA is below the 200-day MA
    death_cross_since: str | None = None  # date the 50-day dipped below, if within the data
    death_cross_before: str | None = None  # otherwise: it was already below on this date

    @property
    def change(self) -> float:
        return self.price - self.prev_close

    @property
    def change_pct(self) -> float:
        return 100 * self.change / self.prev_close if self.prev_close else 0.0

    @property
    def is_new(self) -> bool:
        return self.zone != "neutral" and self.zone != self.prev_zone

    @property
    def below_sma(self) -> bool:
        return self.sma is not None and self.price < self.sma

    @property
    def sma_pct(self) -> float | None:
        """How far price is above (+) or below (-) the moving average, in %."""
        return 100 * (self.price / self.sma - 1) if self.sma else None

    def ma_line(self, period: int) -> str:
        return (
            f"{self.ticker:<6} FELL BELOW {period}D MA  ${self.price:,.2f}"
            f" (MA ${self.sma:,.2f}, {self.sma_pct:+.1f}%)"
        )

    def line(self) -> str:
        new = "  NEW" if self.is_new else ""
        return (
            f"{self.ticker:<6} {self.zone.upper():<10} "
            f"%K {self.k:5.1f}  %D {self.d:5.1f}  ${self.price:,.2f}{new}"
        )


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _rate_limited(text: str) -> bool:
    text = text.lower()
    return "too many requests" in text or "rate limit" in text or "ratelimit" in text


def fetch(tickers: list[str], threads: bool = True, period: str = "1y") -> dict[str, pd.DataFrame]:
    """Daily OHLC for each ticker. During market hours the last row is today's
    in-progress bar, so the reading reflects the latest price.

    Raises market.RateLimited (with whatever did arrive) if Yahoo throttled us.
    yf.download doesn't raise for that; it logs the error and returns no data
    for the affected tickers, so its log is watched for the message.
    """
    capture = _LogCapture()
    yf_log = logging.getLogger("yfinance")
    yf_log.addHandler(capture)
    try:
        raw = yf.download(
            tickers,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=threads,
        )
    except Exception as exc:
        if _rate_limited(f"{type(exc).__name__} {exc}"):
            raise RateLimited(str(exc)) from exc
        raise
    finally:
        yf_log.removeHandler(capture)
    out = {}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        if not {"High", "Low", "Close"} <= set(df.columns):
            continue  # nothing came back for this ticker
        df = df.dropna(subset=["High", "Low", "Close"])
        if not df.empty:
            out[t] = df
    if any(_rate_limited(m) for m in capture.messages):
        raise RateLimited("Too many requests", partial=out)
    return out


def analyze(data: dict[str, pd.DataFrame], cfg: dict) -> tuple[list[Reading], list[str]]:
    s, a = cfg["stochastic"], cfg["alerts"]
    line = a.get("line", "k")
    ma = ma_settings(cfg)
    ma_period = ma["period"]
    readings, skipped = [], []
    for ticker, df in data.items():
        st = stochastic(df, s["period"], s["smooth_k"], s["d"]).dropna()
        if len(st) < 2:
            skipped.append(ticker)
            continue
        now, prev = st.iloc[-1], st.iloc[-2]
        sma = df["Close"].rolling(ma_period).mean()
        sma_now, sma_prev = sma.iloc[-1], sma.iloc[-2]
        price, prev_close = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        has_sma = pd.notna(sma_now) and pd.notna(sma_prev)
        sma_long, death, since, before = death_cross(sma, df["Close"].rolling(ma["long_period"]).mean())
        readings.append(
            Reading(
                ticker=ticker,
                price=price,
                prev_close=prev_close,
                k=float(now["k"]),
                d=float(now["d"]),
                zone=zone(now[line], a["overbought"], a["oversold"]),
                prev_zone=zone(prev[line], a["overbought"], a["oversold"]),
                sma=float(sma_now) if has_sma else None,
                crossed_below=bool(has_sma and prev_close >= sma_prev and price < sma_now),
                sma_long=sma_long,
                death_cross=death,
                death_cross_since=since,
                death_cross_before=before,
            )
        )
    return readings, skipped


def death_cross(short: pd.Series, long: pd.Series) -> tuple[float | None, bool, str | None, str | None]:
    """(latest long MA, whether the short MA is below it, date it went below,
    or else the earliest date we can see it was already below).

    The long average only exists once there's enough history, so a cross older
    than that has no date; "already below on <date>" is the most we can say.
    """
    both = pd.concat([short, long], axis=1).dropna()
    if both.empty:
        return None, False, None, None
    below = both.iloc[:, 0] < both.iloc[:, 1]
    long_now = float(both.iloc[-1, 1])
    if not below.iloc[-1]:
        return long_now, False, None, None
    day = lambda ts: pd.Timestamp(ts).date().isoformat()  # noqa: E731
    above_days = below[~below]
    if above_days.empty:
        return long_now, True, None, day(below.index[0])
    first_below = below.index[below.index.get_loc(above_days.index[-1]) + 1]
    return long_now, True, day(first_below), None


def ma_settings(cfg: dict) -> dict:
    return {"period": 50, "long_period": 200, **cfg.get("moving_average", {})}


def position_settings(cfg: dict, user: dict | None = None) -> dict:
    """+N% position alerts: on/off and threshold come from the page (settings.json),
    falling back to config.toml."""
    user = user or {}
    pct = cfg.get("positions", {}).get("gain_alert_pct", 40)
    return {
        "enabled": bool(user.get("position_alerts", True)),
        "gain_alert_pct": float(user.get("gain_alert_pct") or pct),
    }


def gain_line(h: dict) -> str:
    name = "Portfolio" if h["ticker"] == positions.TOTAL else h["ticker"]
    return f"{name:<9} {h['gain_pct']:+.1f}%  ({h['gain']:+,.2f})  cost ${h['cost']:,.2f} -> ${h['value']:,.2f}"


def build_alert(
    readings: dict[str, Reading],
    stock_lists: list[dict],
    cfg: dict,
    gain_hits: list[dict] | None = None,
    gain_pct: float = 40,
) -> tuple[str, str, bool] | None:
    """Title, body and urgency for one notification, or None if nothing to report.

    Each list only reports the alerts it has switched on. A stock on several lists
    is reported once per kind of alert, under the first list that wants it.
    """
    ma_period = ma_settings(cfg)["period"]
    counts: Counter = Counter()
    reported: set[tuple[str, str]] = set()
    sections, urgent = [], False

    if gain_hits:
        counts["gain"] = len(gain_hits)
        sections.append(f"Up {gain_pct:g}% or more:\n" + "\n".join(gain_line(h) for h in gain_hits))
        urgent = True

    for lst in stock_lists:
        rules = lst["alerts"]
        if not rules["enabled"]:
            continue
        lines = []
        for t in lst["tickers"]:
            r = readings.get(t)
            if r is None:
                continue
            if r.zone != "neutral" and rules[r.zone] and (t, "zone") not in reported:
                reported.add((t, "zone"))
                counts[r.zone] += 1
                lines.append(r.line())
                urgent |= r.is_new
            if r.crossed_below and rules["below_ma"] and (t, "ma") not in reported:
                reported.add((t, "ma"))
                counts["below_ma"] += 1
                lines.append(r.ma_line(ma_period))
                urgent = True
        if lines:
            sections.append(f"{lst['name']}:\n" + "\n".join(lines))

    labels = [
        ("gain", lambda n: f"{n} position{'s' if n > 1 else ''} up {gain_pct:g}%+"),
        ("overbought", lambda n: f"{n} overbought"),
        ("oversold", lambda n: f"{n} oversold"),
        ("below_ma", lambda n: f"{n} fell below {ma_period}d MA"),
    ]
    parts = [label(counts[key]) for key, label in labels if counts[key]]
    if parts:
        return "Stocks: " + ", ".join(parts), "\n\n".join(sections), urgent
    if cfg["alerts"].get("notify_when_quiet"):
        return "Stocks: all quiet", "Nothing to report.", False
    return None


def run_check(dry_run: bool = False, log=print, data: dict[str, pd.DataFrame] | None = None) -> dict:
    """One full check: get prices, work out alerts, notify. Returns what happened.

    `data` lets the app pass the prices it already has (see market.py) instead
    of fetching them again."""
    cfg = load_config()
    user = settings.load()
    stock_lists = lists.load()
    lots = positions.load()
    tickers = list(dict.fromkeys(lists.all_tickers() + [l["ticker"] for l in lots]))
    if not tickers:
        log("No stocks on any list; nothing to check.")
        return {"status": "empty"}
    if data is None:
        data = fetch(tickers, threads=False)
    all_readings, _ = analyze(data, cfg)
    missing = sorted(set(tickers) - {r.ticker for r in all_readings})
    readings = {r.ticker: r for r in all_readings}

    for r in sorted(all_readings, key=lambda r: r.k, reverse=True):
        log(r.line())
    if missing:
        log(f"[warn] no usable data for: {', '.join(missing)}")
    if not all_readings:
        log("[error] no data fetched")
        return {"status": "error", "error": "No market data"}

    pos_cfg = position_settings(cfg, user)
    gain_hits, alert_state = [], None
    if lots and pos_cfg["enabled"]:
        summary = positions.summarize(
            lots, {r.ticker: {"price": r.price, "change": r.change, "change_pct": r.change_pct}
                   for r in all_readings}
        )
        t = summary["total"]
        if t["gain_pct"] is not None:
            log(f"Positions: ${t['value']:,.2f}  total {t['gain']:+,.2f} ({t['gain_pct']:+.1f}%)")
        gain_hits, alert_state = positions.gain_alerts(
            summary, pos_cfg["gain_alert_pct"], positions.load_alert_state()
        )

    alert = build_alert(readings, stock_lists, cfg, gain_hits, pos_cfg["gain_alert_pct"])
    if alert is None:
        log("Nothing to report; no notification sent.")
        result = {"status": "quiet"}
        sent = ["(nothing to send)"]
    elif dry_run:
        log(f"[dry run] would send: {alert[0]}\n{alert[1]}")
        return {"status": "dry_run", "title": alert[0], "body": alert[1]}
    else:
        sent = notifiers.send(*alert, settings=user)
        log(f"Notified via: {', '.join(sent)}" if sent else "[warn] no notification channel configured")
        result = {"status": "sent" if sent else "no_channel", "title": alert[0], "body": alert[1], "sent": sent}
    # Remember which positions were pinged only once the ping actually went out.
    if alert_state is not None and sent:
        positions.save_alert_state(alert_state)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print only, don't notify")
    args = ap.parse_args()
    return 1 if run_check(args.dry_run)["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
