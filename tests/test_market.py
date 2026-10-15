import logging
from datetime import datetime, timedelta

import pandas as pd
import pytest

import market
import tracker
from market import Market, RateLimited
from scheduler import ET


def at(*args):
    return datetime(*args, tzinfo=ET)


THU_NOON = at(2026, 10, 15, 12, 0)


def bars(days, start="2026-04-01", close=100.0):
    idx = pd.date_range(start, periods=days, freq="B")
    c = pd.Series([close + i for i in range(days)], index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c})


class FakeYahoo:
    def __init__(self):
        self.calls = []
        self.limit = False

    def __call__(self, tickers, period):
        self.calls.append((tuple(tickers), period))
        if self.limit:
            raise RateLimited("Too many requests")
        days = 130 if period == "6mo" else 5
        return {t: bars(days, start="2026-04-20" if period == "6mo" else "2026-10-09", close=200.0) for t in tickers}


def test_market_hours_and_last_close():
    assert market.market_open(THU_NOON)
    assert not market.market_open(at(2026, 10, 15, 9, 0))
    assert not market.market_open(at(2026, 10, 17, 12, 0))  # Saturday
    assert market.last_close(at(2026, 10, 15, 12, 0)) == at(2026, 10, 14, 16, 5)
    assert market.last_close(at(2026, 10, 15, 18, 0)) == at(2026, 10, 15, 16, 5)
    assert market.last_close(at(2026, 10, 19, 8, 0)) == at(2026, 10, 16, 16, 5)  # Monday morning -> Friday


def test_schedule_full_every_15_price_every_5():
    yahoo = FakeYahoo()
    m = Market(yahoo)
    assert m.refresh(["A", "B"], THU_NOON) == "updated"
    assert yahoo.calls == [(("A", "B"), "6mo")]
    assert m.refresh(["A", "B"], THU_NOON + timedelta(minutes=3)) == "idle"
    m.refresh(["A", "B"], THU_NOON + timedelta(minutes=5))
    assert yahoo.calls[-1] == (("A", "B"), "5d")
    m.refresh(["A", "B"], THU_NOON + timedelta(minutes=10))
    assert yahoo.calls[-1] == (("A", "B"), "5d")
    m.refresh(["A", "B"], THU_NOON + timedelta(minutes=15))
    assert yahoo.calls[-1] == (("A", "B"), "6mo")
    assert len(yahoo.calls) == 4


def test_new_stock_fetched_right_away_without_refetching_others():
    yahoo = FakeYahoo()
    m = Market(yahoo)
    m.refresh(["A"], THU_NOON)
    m.refresh(["A", "NEW"], THU_NOON + timedelta(minutes=1))
    assert yahoo.calls[-1] == (("NEW",), "6mo")
    assert m.overdue(["A", "NEW"], THU_NOON + timedelta(minutes=1)) is False


def test_closed_market_fetches_once_after_close_then_holds():
    yahoo = FakeYahoo()
    m = Market(yahoo)
    m.refresh(["A"], at(2026, 10, 16, 12, 0))  # Friday midday
    evening = at(2026, 10, 16, 20, 0)
    assert m.overdue(["A"], evening)  # data is from before Friday's close
    m.refresh(["A"], evening)
    n = len(yahoo.calls)
    for hours in (1, 5, 30, 55):  # Friday night through Monday 3am
        assert m.refresh(["A"], evening + timedelta(hours=hours)) == "idle"
    assert len(yahoo.calls) == n
    assert m.refresh(["A"], at(2026, 10, 19, 9, 31)) == "updated"  # Monday open


def test_price_update_merges_into_history():
    m = Market(FakeYahoo())
    m.refresh(["A"], THU_NOON)
    full_len = len(m.history["A"])
    m.refresh(["A"], THU_NOON + timedelta(minutes=5))
    merged = m.history["A"]
    assert len(merged) >= full_len and merged.index.is_monotonic_increasing and not merged.index.duplicated().any()


def test_rate_limit_pauses_30_minutes_and_keeps_old_data():
    yahoo = FakeYahoo()
    m = Market(yahoo)
    m.refresh(["A"], THU_NOON)
    yahoo.limit = True
    assert m.refresh(["A"], THU_NOON + timedelta(minutes=5)) == "rate_limited"
    assert "A" in m.history
    n = len(yahoo.calls)
    for minutes in (6, 20, 34):
        assert m.refresh(["A"], THU_NOON + timedelta(minutes=minutes)) == "backoff"
    assert len(yahoo.calls) == n
    yahoo.limit = False
    assert m.refresh(["A"], THU_NOON + timedelta(minutes=36)) == "updated"


def test_failed_ticker_waits_for_next_interval():
    def fetch(tickers, period):
        return {}  # e.g. a delisted symbol
    calls = []
    m = Market(lambda t, p: calls.append(p) or fetch(t, p))
    m.refresh(["GONE"], THU_NOON)
    for minutes in (1, 5, 10):
        m.refresh(["GONE"], THU_NOON + timedelta(minutes=minutes))
    assert calls == ["6mo"]


def test_fetch_detects_rate_limit_from_yfinance_log(monkeypatch):
    def fake_download(tickers, **kw):
        logging.getLogger("yfinance").error("['AAPL']: YFRateLimitError('Too Many Requests. Rate limited. Try after a while.')")
        return pd.DataFrame()
    monkeypatch.setattr(tracker.yf, "download", fake_download)
    with pytest.raises(RateLimited):
        tracker.fetch(["AAPL"])
