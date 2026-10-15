"""Stochastic oscillator math.

%K raw = 100 * (close - lowest low over N) / (highest high over N - lowest low over N)
%K     = SMA(%K raw, smooth_k)   (smooth_k=1 gives the "fast" stochastic)
%D     = SMA(%K, d)

The common "Stochastic 14, 3, 3" (slow stochastic) is period=14, smooth_k=3, d=3.
"""

from __future__ import annotations

import pandas as pd


def stochastic(
    df: pd.DataFrame, period: int = 14, smooth_k: int = 3, d: int = 3
) -> pd.DataFrame:
    """Return a DataFrame with columns k and d, indexed like df.

    df needs High, Low and Close columns.
    """
    lowest = df["Low"].rolling(period).min()
    highest = df["High"].rolling(period).max()
    span = (highest - lowest).replace(0, float("nan"))  # flat range -> undefined
    raw_k = 100 * (df["Close"] - lowest) / span
    k = raw_k.rolling(smooth_k).mean()
    return pd.DataFrame({"k": k, "d": k.rolling(d).mean()})


def zone(k: float, overbought: float, oversold: float) -> str:
    if k >= overbought:
        return "overbought"
    if k <= oversold:
        return "oversold"
    return "neutral"
