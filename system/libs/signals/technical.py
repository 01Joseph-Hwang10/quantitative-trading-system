"""Technical signal computations: ADX regime and timing series T_t.

Single source of truth for the technical timing layer: the persisted
registry signals (`adx_14`, `t_t`) and the last-bar values inside
`GoldEnsembleStrategy.decide` are derived from the same functions, so
stored history and decision-time values cannot drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import talib

# Notebook class defaults (GoldEnsembleStrategy fixed parameters).
N_ADX = 14
THETA_ADX = 25
N_BB = 20
K = 1.5
N_SHORT = 20
N_LONG = 60


def compute_adx_series(ohlcv: pd.DataFrame, n_adx: int = N_ADX) -> pd.Series:
    """ADX(n_adx) of the OHLCV frame, indexed like the input."""
    adx = talib.ADX(
        ohlcv["high"].astype(float).to_numpy(),
        ohlcv["low"].astype(float).to_numpy(),
        ohlcv["close"].astype(float).to_numpy(),
        timeperiod=n_adx,
    )
    return pd.Series(adx, index=ohlcv.index, name="adx")


def compute_t_t_series(
    ohlcv: pd.DataFrame,
    *,
    n_adx: int = N_ADX,
    theta_adx: float = THETA_ADX,
    n_bb: int = N_BB,
    k: float = K,
    n_short: int = N_SHORT,
    n_long: int = N_LONG,
) -> pd.Series:
    """Technical timing series T_t: ADX regime switch between two styles.

    - Range regime (ADX < theta_adx) — Bollinger Bands mean reversion with
      BB(n_bb, k): +1 when price closes below the lower band and turns up;
      -1 when price closes above the upper band and turns down.
    - Trend regime (ADX >= theta_adx) — double moving-average trend
      following with SMA(n_short) / SMA(n_long): +1 when SMA_short > SMA_long
      and price > SMA_long; -1 when SMA_short < SMA_long and price < SMA_long.

    Bars inside the indicator warm-up (undefined ADX, Bollinger mean, or
    long SMA) are NaN.
    """
    close = ohlcv["close"].astype(float)
    adx = compute_adx_series(ohlcv, n_adx=n_adx)
    mu = pd.Series(talib.SMA(close.to_numpy(), timeperiod=n_bb), index=ohlcv.index)
    std = close.rolling(n_bb).std()
    upper = mu + k * std
    lower = mu - k * std
    ma_short = pd.Series(talib.SMA(close.to_numpy(), timeperiod=n_short), index=ohlcv.index)
    ma_long = pd.Series(talib.SMA(close.to_numpy(), timeperiod=n_long), index=ohlcv.index)

    previous = close.shift(1)
    range_regime = adx < theta_adx
    range_up = (close < lower) & (close > previous)
    range_down = (close > upper) & (close < previous)
    trend_up = (ma_short > ma_long) & (close > ma_long)
    trend_down = (ma_short < ma_long) & (close < ma_long)

    timing = pd.Series(0.0, index=ohlcv.index, name="t_t")
    timing = timing.mask(range_regime & range_up, 1.0)
    timing = timing.mask(range_regime & range_down, -1.0)
    timing = timing.mask(~range_regime & trend_up, 1.0)
    timing = timing.mask(~range_regime & trend_down, -1.0)
    # Warm-up: any undefined required indicator means the regime (and thus
    # T_t) is undefined.
    return timing.mask(adx.isna() | mu.isna() | ma_long.isna(), np.nan)
