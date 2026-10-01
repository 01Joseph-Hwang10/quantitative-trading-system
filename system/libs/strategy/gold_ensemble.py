"""GoldEnsembleStrategy — faithful port of notebooks/strategy_1.ipynb.

Macro gate D_t (TNX/DXY/FX tanh scores) × technical timing T_t (ADX regime
switch between Bollinger mean-reversion and SMA trend-following), exactly as
in notebook cells 4 and 6.

Hyperparameters are the static, tuned values from the notebook's optimization
output (Sharpe 1.2181 / Return 31.59% on the training range):
n_macro=10, k=1.5, theta_entry=0.3.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import talib

from system.libs.strategy.base import MarketView, Signal

_MACRO_WINDOWS = [10, 20, 40, 60]
_EPSILON = 1e-9


def compute_macro_frame(tnx: pd.Series, dxy: pd.Series, fx: pd.Series) -> pd.DataFrame:
    """Replicate notebook cell 4: delayed macro series and D_t_{n} columns.

    A 1-day lag (shift(1)) prevents look-ahead bias; D_t_n averages three
    tanh-normalized reversal/continuation scores:
      s_tnx = -tanh(r_tnx / sigma_tnx)
      s_dxy = -tanh(r_dxy / sigma_dxy)
      s_fx  = +tanh(r_fx / sigma_fx)
    """
    unified_index = tnx.index.union(dxy.index).union(fx.index).sort_values()
    macro = pd.DataFrame(index=unified_index)
    macro["TNX"] = tnx.reindex(unified_index).ffill()
    macro["DXY"] = dxy.reindex(unified_index).ffill()
    macro["FX"] = fx.reindex(unified_index).ffill()

    macro["TNX_delayed"] = macro["TNX"].shift(1)
    macro["DXY_delayed"] = macro["DXY"].shift(1)
    macro["FX_delayed"] = macro["FX"].shift(1)

    for window in _MACRO_WINDOWS:
        return_tnx = macro["TNX_delayed"].pct_change(window)
        return_dxy = macro["DXY_delayed"].pct_change(window)
        return_fx = macro["FX_delayed"].pct_change(window)
        sigma_tnx = return_tnx.rolling(window).std()
        sigma_dxy = return_dxy.rolling(window).std()
        sigma_fx = return_fx.rolling(window).std()
        score_tnx = -np.tanh(return_tnx / (sigma_tnx + _EPSILON))
        score_dxy = -np.tanh(return_dxy / (sigma_dxy + _EPSILON))
        score_fx = np.tanh(return_fx / (sigma_fx + _EPSILON))
        macro[f"D_t_{window}"] = (score_tnx + score_dxy + score_fx) / 3
    return macro


class GoldEnsembleStrategy:
    """Static-parameter port of the notebook's GoldEnsembleStrategy."""

    name = "gold_ensemble"

    # Tuned parameters (notebook optimization output).
    n_macro = 10
    k = 1.5
    theta_entry = 0.3

    # Fixed parameters (notebook class defaults).
    n_adx = 14
    theta_adx = 25
    n_bb = 20
    n_short = 20
    n_long = 60
    theta_exit = -0.2
    theta_stop = 0.05

    def decide(self, view: MarketView) -> tuple[Signal, dict[str, Any]]:
        close = view.ohlcv["close"].astype(float)
        high = view.ohlcv["high"].astype(float)
        low = view.ohlcv["low"].astype(float)

        # Warmup guard: indicators need history; the notebook returns (no
        # decision) until ADX / SMA20 / SMA60 / D_t are all defined.
        if len(close) < 2 or len(close) < max(self.n_bb, self.n_long, self.n_adx):
            return Signal.HOLD, {"reason": "warmup"}

        adx = talib.ADX(high.to_numpy(), low.to_numpy(), close.to_numpy(), timeperiod=self.n_adx)
        mu = talib.SMA(close.to_numpy(), timeperiod=self.n_bb)
        std = close.rolling(self.n_bb).std().to_numpy()
        upper = mu + self.k * std
        lower = mu - self.k * std
        ma_short = talib.SMA(close.to_numpy(), timeperiod=self.n_short)
        ma_long = talib.SMA(close.to_numpy(), timeperiod=self.n_long)

        d_t_column = f"D_t_{self.n_macro}"
        if d_t_column not in view.macro.columns:
            raise KeyError(f"Macro frame lacks {d_t_column!r} (reindex the view's macro frame)")
        d_t = view.macro[d_t_column].reindex(_normalize_index(view.ohlcv.index)).ffill().to_numpy()

        price = float(close.iloc[-1])
        price_prev = float(close.iloc[-2])
        adx_last = float(adx[-1])
        mu_last = float(mu[-1])
        ma_long_last = float(ma_long[-1])
        ma_short_last = float(ma_short[-1])
        upper_last = float(upper[-1])
        lower_last = float(lower[-1])
        d_t_last = float(d_t[-1]) if len(d_t) else float("nan")

        indicators: dict[str, Any] = {
            "adx": adx_last,
            "d_t": d_t_last,
            "t_t": 0,
            "regime": "range" if adx_last < self.theta_adx else "trend",
            "close": price,
            "lower": lower_last,
            "upper": upper_last,
            "mu": mu_last,
            "ma_short": ma_short_last,
            "ma_long": ma_long_last,
        }

        if np.isnan(adx_last) or np.isnan(mu_last) or np.isnan(ma_long_last) or np.isnan(d_t_last):
            indicators["reason"] = "warmup"
            return Signal.HOLD, indicators

        # 1. Technical timing signal T_t (notebook next()).
        timing = 0
        if adx_last < self.theta_adx:
            # Range regime: Bollinger Bands mean reversion.
            if price < lower_last and price > price_prev:
                timing = 1
            elif price > upper_last and price < price_prev:
                timing = -1
        else:
            # Trend regime: double moving average trend following.
            if ma_short_last > ma_long_last and price > ma_long_last:
                timing = 1
            elif ma_short_last < ma_long_last and price < ma_long_last:
                timing = -1
        indicators["t_t"] = timing

        # 2. Position management (whole positions only).
        is_long = view.position_quantity > 0
        if not is_long:
            if d_t_last > self.theta_entry and timing == 1:
                return Signal.BUY, indicators
            return Signal.HOLD, indicators

        # Exit conditions: OR of the 5 notebook triggers.
        cond1 = d_t_last < self.theta_exit
        cond2 = timing == -1
        cond3 = (adx_last >= self.theta_adx) and (price < ma_long_last or ma_short_last < ma_long_last)
        cond4 = (adx_last < self.theta_adx) and (price >= mu_last)
        entry_price = view.position_entry_price
        cond5 = entry_price is not None and (price / entry_price) - 1 < -self.theta_stop

        exit_reasons = [
            name
            for name, triggered in (
                ("macro_exit", cond1),
                ("timing_exit", cond2),
                ("trend_break", cond3),
                ("range_reversion_target", cond4),
                ("stop_loss", cond5),
            )
            if triggered
        ]
        if exit_reasons:
            indicators["exit_reasons"] = exit_reasons
            return Signal.SELL, indicators
        return Signal.HOLD, indicators


def _normalize_index(index: pd.Index) -> pd.Index:
    """Macro frames are daily-date-indexed; views may carry tz-aware stamps."""
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None:
            return index.tz_localize(None).normalize()
        return index.normalize()
    return index
