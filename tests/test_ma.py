import pandas as pd
import pytest

import tracker

CFG = {
    "stochastic": {"period": 14, "smooth_k": 3, "d": 3},
    "alerts": {"overbought": 101, "oversold": -1},  # keep stochastic alerts out of the way
    "moving_average": {"period": 50},
}


def bars(closes):
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"High": c + 1, "Low": c - 1, "Close": c})


def reading(closes, cfg=CFG):
    readings, _ = tracker.analyze({"X": bars(closes)}, cfg)
    return readings[0]


def test_sma_and_distance():
    r = reading([100.0] * 59 + [90.0])
    assert r.sma == pytest.approx((100 * 49 + 90) / 50)
    assert r.below_sma
    assert r.sma_pct == pytest.approx(100 * (90 / r.sma - 1))


def test_cross_below_detected_only_on_the_cross():
    crossed = reading([100.0] * 59 + [90.0])
    assert crossed.crossed_below
    still_below = reading([100.0] * 58 + [90.0, 89.0])
    assert still_below.below_sma and not still_below.crossed_below


def test_not_enough_history_means_no_ma():
    r = reading([100.0] * 30)
    assert r.sma is None and not r.below_sma and not r.crossed_below


def lst(name, tickers, **alerts):
    return {"id": name, "name": name, "tickers": tickers,
            "alerts": {"enabled": True, "overbought": True, "oversold": True, "below_ma": True, **alerts}}


def test_alert_includes_ma_cross():
    r = reading([100.0] * 59 + [90.0])
    title, body, urgent = tracker.build_alert({"X": r}, [lst("Tech", ["X"])], CFG)
    assert "fell below 50d MA" in title
    assert body.startswith("Tech:") and "FELL BELOW 50D MA" in body and urgent


def test_ma_alert_off_for_a_list():
    r = reading([100.0] * 59 + [90.0])
    assert tracker.build_alert({"X": r}, [lst("Tech", ["X"], below_ma=False)], CFG) is None
    assert tracker.build_alert({"X": r}, [lst("Tech", ["X"], enabled=False)], CFG) is None
