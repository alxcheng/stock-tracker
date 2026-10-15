import pytest

import positions

LOTS = [
    {"id": "a", "ticker": "AAPL", "shares": 10, "price": 100.0},
    {"id": "b", "ticker": "AAPL", "shares": 10, "price": 200.0},
    {"id": "c", "ticker": "TSLA", "shares": 2, "price": 300.0},
]
PRICES = {"AAPL": {"price": 210.0, "change": 5.0, "change_pct": 2.4},
          "TSLA": {"price": 270.0, "change": -3.0, "change_pct": -1.1}}


def test_holdings_combine_lots_at_average_cost():
    s = positions.summarize(LOTS, PRICES)
    aapl = next(h for h in s["holdings"] if h["ticker"] == "AAPL")
    assert aapl["shares"] == 20 and aapl["avg_cost"] == pytest.approx(150)
    assert aapl["value"] == pytest.approx(4200)
    assert aapl["gain"] == pytest.approx(1200)
    assert aapl["gain_pct"] == pytest.approx(40)
    assert aapl["day_gain"] == pytest.approx(100)


def test_lot_and_total_gain():
    s = positions.summarize(LOTS, PRICES)
    lot_a = next(l for l in s["lots"] if l["id"] == "a")
    assert lot_a["gain_pct"] == pytest.approx(110)
    t = s["total"]
    assert t["cost"] == pytest.approx(3600) and t["value"] == pytest.approx(4740)
    assert t["gain"] == pytest.approx(1140)
    assert t["gain_pct"] == pytest.approx(100 * (4740 / 3600 - 1))
    assert t["day_gain"] == pytest.approx(100 - 6)


def test_missing_price_is_excluded_from_totals():
    s = positions.summarize(LOTS, {"AAPL": PRICES["AAPL"]})
    assert s["total"]["missing"] == ["TSLA"]
    assert s["total"]["cost"] == pytest.approx(3000)


def test_gain_alert_fires_once_then_rearms():
    s = positions.summarize(LOTS, PRICES)  # AAPL exactly +40%, total +31.7%
    hits, state = positions.gain_alerts(s, 40, [])
    assert [h["ticker"] for h in hits] == ["AAPL"] and state == ["AAPL"]
    hits, state = positions.gain_alerts(s, 40, state)
    assert hits == [] and state == ["AAPL"]  # no repeat while still above
    lower = positions.summarize(LOTS, {**PRICES, "AAPL": {**PRICES["AAPL"], "price": 180.0}})
    hits, state = positions.gain_alerts(lower, 40, state)
    assert hits == [] and state == []  # fell below: re-armed
    hits, _ = positions.gain_alerts(s, 40, state)
    assert [h["ticker"] for h in hits] == ["AAPL"]


def test_total_alert_and_missing_data_keeps_state():
    rich = {"AAPL": {"price": 400.0, "change": 0, "change_pct": 0},
            "TSLA": {"price": 500.0, "change": 0, "change_pct": 0}}
    hits, state = positions.gain_alerts(positions.summarize(LOTS, rich), 40, [])
    assert {h["ticker"] for h in hits} == {"AAPL", "TSLA", positions.TOTAL}
    gap = positions.summarize(LOTS, {"AAPL": rich["AAPL"]})
    hits, state = positions.gain_alerts(gap, 40, state)
    assert hits == [] and "TSLA" in state


def test_add_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(positions, "POSITIONS", tmp_path / "p.json")
    a = positions.add("AAPL", 1, 10.0)
    positions.add("AAPL", 2, 20.0)
    positions.add("MSFT", 3, 30.0)
    assert positions.tickers() == ["AAPL", "MSFT"]
    assert positions.remove(lot_id=a["id"]) == 1
    assert positions.remove(ticker="AAPL") == 1
    assert [l["ticker"] for l in positions.load()] == ["MSFT"]
