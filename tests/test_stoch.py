import pandas as pd
import pytest

from stoch import stochastic, zone


def frame(highs, lows, closes):
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


def test_fast_k_matches_formula():
    # 14 bars: lows 1..14, highs 11..24, last close 20
    lows = list(range(1, 15))
    highs = [l + 10 for l in lows]
    closes = [l + 5 for l in lows[:-1]] + [20]
    st = stochastic(frame(highs, lows, closes), period=14, smooth_k=1, d=1)
    # lowest low 1, highest high 24 -> (20 - 1) / (24 - 1) * 100
    assert st["k"].iloc[-1] == pytest.approx(19 / 23 * 100)
    assert st["k"].iloc[:-1].isna().all()


def test_slow_k_and_d_are_moving_averages():
    n = 30
    df = frame([float(i % 7 + 10) for i in range(n)], [float(i % 5) for i in range(n)],
               [float(i % 6 + 3) for i in range(n)])
    fast = stochastic(df, 14, 1, 1)["k"]
    slow = stochastic(df, 14, 3, 3)
    assert slow["k"].iloc[-1] == pytest.approx(fast.iloc[-3:].mean())
    assert slow["d"].iloc[-1] == pytest.approx(slow["k"].iloc[-3:].mean())


def test_close_at_high_is_100_and_at_low_is_0():
    df = frame([10.0] * 14, [0.0] * 14, [5.0] * 13 + [10.0])
    assert stochastic(df, 14, 1, 1)["k"].iloc[-1] == pytest.approx(100)
    df = frame([10.0] * 14, [0.0] * 14, [5.0] * 13 + [0.0])
    assert stochastic(df, 14, 1, 1)["k"].iloc[-1] == pytest.approx(0)


def test_zone_thresholds_inclusive():
    assert zone(80, 80, 20) == "overbought"
    assert zone(20, 80, 20) == "oversold"
    assert zone(50, 80, 20) == "neutral"
