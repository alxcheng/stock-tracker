import pandas as pd

import tracker

CFG = {
    "stochastic": {"period": 14, "smooth_k": 3, "d": 3},
    "alerts": {"overbought": 80, "oversold": 20},
    "moving_average": {"period": 50, "long_period": 200},
}


def frame(closes):
    idx = pd.bdate_range("2025-06-02", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"High": c + 1, "Low": c - 1, "Close": c})


def read(closes):
    return tracker.analyze({"X": frame(closes)}, CFG)[0][0]


def test_rising_stock_has_no_death_cross():
    r = read([100 + i * 0.5 for i in range(260)])
    assert r.sma_long is not None and not r.death_cross and r.death_cross_since is None


def test_falling_stock_gets_death_cross_with_its_date():
    closes = [100 + i * 0.5 for i in range(220)] + [210 - i * 3 for i in range(60)]
    r = read(closes)
    assert r.death_cross and r.sma < r.sma_long
    df = frame(closes)
    s50, s200 = df["Close"].rolling(50).mean(), df["Close"].rolling(200).mean()
    below = (s50 < s200)[s200.notna()]
    expected = below[below & ~below.shift(1, fill_value=False)].index[-1].date().isoformat()
    assert r.death_cross_since == expected


def test_not_enough_history_means_no_200_day_average():
    r = read([100.0] * 150)
    assert r.sma_long is None and not r.death_cross


def test_cross_before_the_downloaded_history_has_no_date():
    closes = [300 - i for i in range(260)]  # falling the whole time
    r = read(closes)
    assert r.death_cross and r.death_cross_since is None
    assert r.death_cross_before == frame(closes).index[199].date().isoformat()  # first day with a 200-day MA
