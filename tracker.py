"""Check the watchlist and positions, and ping on anything worth knowing.

The web app runs this on a schedule (see scheduler.py). By hand:
  python tracker.py              # check and notify
  python tracker.py --dry-run    # check and print only
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

import notifiers
import positions
import settings
from paths import CODE, DATA
from stoch import stochastic, zone

WATCHLIST = DATA / "watchlist.json"


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


def load_watchlist() -> list[str]:
    return json.loads(WATCHLIST.read_text()) if WATCHLIST.exists() else []


def save_watchlist(tickers: list[str]) -> None:
    tmp = WATCHLIST.with_suffix(".tmp")
    tmp.write_text(json.dumps(tickers) + "\n")
    tmp.replace(WATCHLIST)


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
            f"{self.ticker:<6} ${self.price:,.2f}  {period}d MA ${self.sma:,.2f}"
            f"  ({self.sma_pct:+.1f}%)"
        )

    def line(self) -> str:
        new = "  NEW" if self.is_new else ""
        return (
            f"{self.ticker:<6} {self.zone.upper():<10} "
            f"%K {self.k:5.1f}  %D {self.d:5.1f}  ${self.price:,.2f}{new}"
        )


def fetch(tickers: list[str], threads: bool = True) -> dict[str, pd.DataFrame]:
    """Daily OHLC for each ticker. During market hours the last row is today's
    in-progress bar, so the reading reflects the latest price."""
    raw = yf.download(
        tickers,
        period="6mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=threads,
    )
    out = {}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna(subset=["High", "Low", "Close"])
        if not df.empty:
            out[t] = df
    return out


def analyze(data: dict[str, pd.DataFrame], cfg: dict) -> tuple[list[Reading], list[str]]:
    s, a = cfg["stochastic"], cfg["alerts"]
    line = a.get("line", "k")
    ma_period = ma_settings(cfg)["period"]
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
            )
        )
    return readings, skipped


def ma_settings(cfg: dict) -> dict:
    return {"period": 50, "alert_on_cross_below": True, **cfg.get("moving_average", {})}


def position_settings(cfg: dict) -> dict:
    return {"gain_alert_pct": 40, **cfg.get("positions", {})}


def gain_line(h: dict) -> str:
    name = "Portfolio" if h["ticker"] == positions.TOTAL else h["ticker"]
    return f"{name:<9} {h['gain_pct']:+.1f}%  ({h['gain']:+,.2f})  cost ${h['cost']:,.2f} -> ${h['value']:,.2f}"


def build_alert(
    readings: list[Reading], cfg: dict, gain_hits: list[dict] | None = None
) -> tuple[str, str, bool] | None:
    """Title, body and urgency for one notification, or None if nothing to report."""
    ma = ma_settings(cfg)
    gain_hits = gain_hits or []
    flagged = sorted((r for r in readings if r.zone != "neutral"), key=lambda r: r.k, reverse=True)
    crossed = [r for r in readings if r.crossed_below] if ma["alert_on_cross_below"] else []

    parts, sections = [], []
    if n := sum(r.zone == "overbought" for r in flagged):
        parts.append(f"{n} overbought")
    if n := sum(r.zone == "oversold" for r in flagged):
        parts.append(f"{n} oversold")
    if flagged:
        sections.append("\n".join(r.line() for r in flagged))
    if crossed:
        parts.append(f"{len(crossed)} fell below {ma['period']}d MA")
        sections.append(
            f"Fell below {ma['period']}-day MA:\n"
            + "\n".join(r.ma_line(ma["period"]) for r in sorted(crossed, key=lambda r: r.sma_pct))
        )
    if gain_hits:
        pct = position_settings(cfg)["gain_alert_pct"]
        parts.insert(0, f"{len(gain_hits)} position{'s' if len(gain_hits) > 1 else ''} up {pct:g}%+")
        sections.insert(0, f"Up {pct:g}% or more:\n" + "\n".join(gain_line(h) for h in gain_hits))
    if parts:
        urgent = bool(crossed or gain_hits) or any(r.is_new for r in flagged)
        return "Stocks: " + ", ".join(parts), "\n\n".join(sections), urgent
    if cfg["alerts"].get("notify_when_quiet"):
        return "Stocks: all quiet", "Nothing overbought, oversold or newly below the MA.", False
    return None


def run_check(dry_run: bool = False, log=print) -> dict:
    """One full check: fetch prices, work out alerts, notify. Returns what happened."""
    cfg = load_config()
    watchlist = load_watchlist()
    lots = positions.load()
    tickers = list(dict.fromkeys(watchlist + [l["ticker"] for l in lots]))
    if not tickers:
        log("Watchlist is empty; nothing to check.")
        return {"status": "empty"}
    all_readings, _ = analyze(fetch(tickers, threads=False), cfg)
    missing = sorted(set(tickers) - {r.ticker for r in all_readings})
    # Stochastic/MA alerts cover the watchlist; held stocks off the list only count for gains.
    readings = [r for r in all_readings if r.ticker in watchlist]

    for r in sorted(readings, key=lambda r: r.k, reverse=True):
        log(r.line())
    if missing:
        log(f"[warn] no usable data for: {', '.join(missing)}")
    if not all_readings:
        log("[error] no data fetched")
        return {"status": "error", "error": "No market data"}

    gain_hits, alert_state = [], None
    if lots:
        summary = positions.summarize(
            lots, {r.ticker: {"price": r.price, "change": r.change, "change_pct": r.change_pct}
                   for r in all_readings}
        )
        t = summary["total"]
        if t["gain_pct"] is not None:
            log(f"Positions: ${t['value']:,.2f}  total {t['gain']:+,.2f} ({t['gain_pct']:+.1f}%)")
        gain_hits, alert_state = positions.gain_alerts(
            summary, position_settings(cfg)["gain_alert_pct"], positions.load_alert_state()
        )

    alert = build_alert(readings, cfg, gain_hits)
    if alert is None:
        log("Nothing to report; no notification sent.")
        result = {"status": "quiet"}
        sent = ["(nothing to send)"]
    elif dry_run:
        log(f"[dry run] would send: {alert[0]}\n{alert[1]}")
        return {"status": "dry_run", "title": alert[0], "body": alert[1]}
    else:
        sent = notifiers.send(*alert, settings=settings.load())
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
