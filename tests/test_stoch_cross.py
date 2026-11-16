import pandas as pd

import tracker

OB, OS = 80, 20


def row(k, d):
    return pd.Series({"k": k, "d": d})


def cross(prev, now):
    return tracker.stoch_cross(row(*prev), row(*now), OB, OS)


def test_bullish_cross_in_oversold_zone():
    assert cross((10, 12), (15, 13)) == "bullish"


def test_bearish_cross_in_overbought_zone():
    assert cross((90, 88), (85, 87)) == "bearish"


def test_neutral_zone_crosses_are_ignored():
    assert cross((40, 45), (50, 46)) is None
    assert cross((60, 55), (50, 54)) is None


def test_wrong_direction_for_the_zone_is_ignored():
    assert cross((15, 12), (10, 13)) is None  # bearish cross while oversold
    assert cross((85, 88), (90, 87)) is None  # bullish cross while overbought


def test_no_cross_when_k_stays_on_one_side():
    assert cross((10, 12), (11, 13)) is None
    assert cross((15, 12), (16, 13)) is None


def test_zone_is_judged_by_d_at_the_cross():
    assert cross((15, 19), (22, 20)) == "bullish"  # %D 20 is at the oversold line
    assert cross((15, 19), (25, 21)) is None  # %D has left the zone


CFG = {
    "stochastic": {"period": 14, "smooth_k": 3, "d": 3},
    "alerts": {"overbought": OB, "oversold": OS},
    "moving_average": {"period": 50, "long_period": 200},
}


def frame(closes):
    idx = pd.bdate_range("2025-06-02", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"High": c + 1, "Low": c - 1, "Close": c})


def test_analyze_flags_a_bounce_off_the_lows():
    # A long slide pins %K and %D near 0; a sharp up day lifts %K above %D.
    closes = [200 - i for i in range(60)] + [143.5]
    r = tracker.analyze({"X": frame(closes)}, CFG)[0][0]
    assert r.k > r.d and r.d <= OS
    assert r.stoch_cross == "bullish"


def test_analyze_steady_trend_has_no_cross():
    r = tracker.analyze({"X": frame([100 + i for i in range(60)])}, CFG)[0][0]
    assert r.stoch_cross is None
