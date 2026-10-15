"""Keeps market data fresh on the app's own schedule. The page never triggers
Yahoo requests; it only shows what's here.

During US market hours (weekdays 9:30-16:00 ET):
  - every 15 minutes: 6 months of daily bars for every stock (the indicators'
    history),
  - every 5 minutes: just the last few days of bars (the current price). They're
    merged into the history, so %K and the 50-day MA are recomputed from the
    new price and a row's price and indicators always match.
Outside market hours: one refresh after the close, then nothing until the open.
Anything missing, such as a stock just added or data from before the computer
slept, counts as due right away. If Yahoo answers "Too Many Requests", fetching
pauses for 30 minutes.
"""

from __future__ import annotations

import threading
from datetime import datetime, time, timedelta

import pandas as pd

from scheduler import ET

FULL_EVERY = timedelta(minutes=15)
PRICE_EVERY = timedelta(minutes=5)
BACKOFF = timedelta(minutes=30)
OPEN, CLOSE = time(9, 30), time(16, 0)
SETTLE = timedelta(minutes=5)  # wait a little after the close for final prices


class RateLimited(Exception):
    """Yahoo answered "Too Many Requests". `partial` holds whatever did arrive."""

    def __init__(self, message: str, partial: dict | None = None):
        super().__init__(message)
        self.partial = partial or {}


def market_open(now: datetime) -> bool:
    now = now.astimezone(ET)
    return now.weekday() < 5 and OPEN <= now.time() < CLOSE


def last_close(now: datetime) -> datetime:
    """The most recent weekday close (plus SETTLE) at or before `now`."""
    now = now.astimezone(ET)
    day = now
    while True:
        close = day.replace(hour=CLOSE.hour, minute=CLOSE.minute, second=0, microsecond=0) + SETTLE
        if day.weekday() < 5 and close <= now:
            return close
        day -= timedelta(days=1)


def due(now: datetime, fetched_at: datetime | None, every: timedelta) -> bool:
    if fetched_at is None:
        return True
    if market_open(now):
        return now - fetched_at >= every
    # Closed: one fetch after the latest close covers the whole night or weekend.
    return fetched_at < last_close(now)


def merge(history: pd.DataFrame, recent: pd.DataFrame) -> pd.DataFrame:
    """Replace the overlapping days of `history` with `recent` and add new days."""
    if history is None or history.empty:
        return recent
    return pd.concat([history[~history.index.isin(recent.index)], recent]).sort_index()


class Market:
    def __init__(self, fetch):
        """fetch(tickers, period) -> {ticker: DataFrame}; raises RateLimited."""
        self._fetch = fetch
        self.history: dict[str, pd.DataFrame] = {}
        self.full_at: dict[str, datetime] = {}   # last full attempt, per ticker
        self.price_at: dict[str, datetime] = {}  # last price attempt, per ticker
        self.updated_at: dict[str, datetime] = {}  # last time new data actually arrived
        self.failed: set[str] = set()  # tickers whose last attempt returned nothing
        self.backoff_until: datetime | None = None
        self.version = 0  # bumps whenever history changes
        self._lock = threading.Lock()

    def backing_off(self, now: datetime) -> bool:
        return self.backoff_until is not None and now < self.backoff_until

    def plan(self, tickers: list[str], now: datetime) -> tuple[list[str], list[str]]:
        """(tickers needing full history, tickers needing just a price update)."""
        full = [t for t in tickers if due(now, self.full_at.get(t), FULL_EVERY)]
        price = [t for t in tickers if t not in full and t in self.history
                 and due(now, self.price_at.get(t), PRICE_EVERY)]
        return full, price

    def overdue(self, tickers: list[str], now: datetime, grace: timedelta = timedelta(minutes=2)) -> bool:
        """Missing data, or a refresh late by more than `grace` (e.g. after sleep):
        worth fetching right away instead of waiting for the background loop."""
        if any(t not in self.full_at for t in tickers):  # never fetched, e.g. just added
            return True
        full, price = self.plan(tickers, now - grace)
        return bool(full or price)

    def refresh(self, tickers: list[str], now: datetime | None = None) -> str:
        """Fetch whatever is due. Returns "backoff", "idle", "updated" or "rate_limited"."""
        now = now or datetime.now(ET)
        with self._lock:
            if self.backing_off(now):
                return "backoff"
            full, price = self.plan(tickers, now)
            if not full and not price:
                return "idle"
            for batch, period, is_full in ((full, "6mo", True), (price, "5d", False)):
                if not batch:
                    continue
                print(f"[market] fetching {'history' if is_full else 'prices'} for {len(batch)} stock(s)", flush=True)
                try:
                    self._store(batch, self._fetch(batch, period), now, full_fetch=is_full)
                except RateLimited as exc:
                    self.backoff_until = now + BACKOFF
                    print(f"[market] Yahoo is rate limiting; pausing until {self.backoff_until:%H:%M} ET", flush=True)
                    # Keep what did arrive; the rest stays due for after the pause.
                    if exc.partial:
                        self._store(list(exc.partial), exc.partial, now, full_fetch=is_full)
                    return "rate_limited"
            return "updated"

    def _store(self, asked: list[str], got: dict, now: datetime, full_fetch: bool) -> None:
        for t in asked:
            if full_fetch:
                self.full_at[t] = now
            self.price_at[t] = now
            df = got.get(t)
            if df is None or df.empty:
                self.failed.add(t)
                continue
            self.failed.discard(t)
            self.history[t] = df if full_fetch else merge(self.history.get(t), df)
            self.updated_at[t] = now
        self.version += 1

    def data(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        return {t: self.history[t] for t in tickers if t in self.history}

    def oldest_update(self, tickers: list[str]) -> datetime | None:
        """When the stalest of these stocks last got new data (None if none have any)."""
        times = [self.updated_at[t] for t in tickers if t in self.updated_at]
        return min(times) if times else None
