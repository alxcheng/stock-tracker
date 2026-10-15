"""Positions you hold and their gain/loss.

positions.json is a list of lots: {"id", "ticker", "shares", "price", "added"}.
Lots of the same ticker are combined into one holding at their average cost.
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import date
from pathlib import Path

from paths import DATA

POSITIONS = DATA / "positions.json"
ALERT_STATE = DATA / ".gain_alert_state.json"
TOTAL = "TOTAL"  # alert-state key for the whole portfolio

_lock = threading.Lock()


def _read(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _write(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    tmp.replace(path)


def load() -> list[dict]:
    return _read(POSITIONS, [])


def tickers() -> list[str]:
    return list(dict.fromkeys(lot["ticker"] for lot in load()))


def add(ticker: str, shares: float, price: float) -> dict:
    lot = {
        "id": secrets.token_hex(4),
        "ticker": ticker,
        "shares": shares,
        "price": price,
        "added": date.today().isoformat(),
    }
    with _lock:
        _write(POSITIONS, load() + [lot])
    return lot


def remove(lot_id: str | None = None, ticker: str | None = None) -> int:
    """Remove one lot by id, or every lot of a ticker. Returns how many were removed."""
    with _lock:
        lots = load()
        keep = [l for l in lots if l["id"] != lot_id and l["ticker"] != ticker]
        _write(POSITIONS, keep)
    return len(lots) - len(keep)


def _gain(cost: float, value: float | None) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    return value - cost, (100 * (value / cost - 1) if cost else None)


def summarize(lots: list[dict], prices: dict[str, dict]) -> dict:
    """Combine lots with current prices.

    prices maps ticker -> {"price": float, "change": float} (change = today's $ move per share).
    """
    holdings: dict[str, dict] = {}
    out_lots = []
    for lot in lots:
        p = prices.get(lot["ticker"])
        cost = lot["shares"] * lot["price"]
        value = lot["shares"] * p["price"] if p else None
        gain, gain_pct = _gain(cost, value)
        out_lots.append({**lot, "cost": cost, "value": value, "gain": gain, "gain_pct": gain_pct})

        h = holdings.setdefault(
            lot["ticker"],
            {"ticker": lot["ticker"], "shares": 0.0, "cost": 0.0, "lots": 0,
             "price": p["price"] if p else None, "day_change_pct": p.get("change_pct") if p else None,
             "day_gain": 0.0 if p else None},
        )
        h["shares"] += lot["shares"]
        h["cost"] += cost
        h["lots"] += 1
        if p:
            h["day_gain"] += lot["shares"] * p["change"]

    for h in holdings.values():
        h["avg_cost"] = h["cost"] / h["shares"] if h["shares"] else 0.0
        h["value"] = h["shares"] * h["price"] if h["price"] is not None else None
        h["gain"], h["gain_pct"] = _gain(h["cost"], h["value"])

    priced = [h for h in holdings.values() if h["value"] is not None]
    cost = sum(h["cost"] for h in priced)
    value = sum(h["value"] for h in priced)
    gain, gain_pct = _gain(cost, value) if priced else (None, None)
    total = {
        "cost": cost,
        "value": value if priced else None,
        "gain": gain,
        "gain_pct": gain_pct,
        "day_gain": sum(h["day_gain"] for h in priced) if priced else None,
        "missing": [h["ticker"] for h in holdings.values() if h["value"] is None],
    }
    return {"lots": out_lots, "holdings": list(holdings.values()), "total": total}


def gain_alerts(summary: dict, threshold: float, alerted: list[str]) -> tuple[list[dict], list[str]]:
    """Holdings (and the portfolio total) that just reached +threshold%.

    `alerted` holds the keys already pinged; a key is dropped once it falls back
    below the threshold, so a later climb back above alerts again.
    Returns (new hits, updated alerted list).
    """
    candidates = [(h["ticker"], h) for h in summary["holdings"]]
    if len(summary["holdings"]) > 1:
        candidates.append((TOTAL, {"ticker": TOTAL, **summary["total"]}))
    # Small tolerance so exactly +40% counts despite float rounding (e.g. 39.9999999).
    above = {key: h for key, h in candidates
             if h["gain_pct"] is not None and h["gain_pct"] >= threshold - 1e-9}
    # Keep keys we have no price for this run, so a data gap doesn't re-trigger alerts.
    unknown = {key for key, h in candidates if h["gain_pct"] is None}
    hits = [h for key, h in above.items() if key not in alerted]
    return hits, sorted(set(above) | (set(alerted) & unknown))


def load_alert_state() -> list[str]:
    return _read(ALERT_STATE, [])


def save_alert_state(keys: list[str]) -> None:
    _write(ALERT_STATE, keys)
