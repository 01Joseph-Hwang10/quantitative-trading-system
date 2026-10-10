"""Macro signal computations (notebook cell 4): component scores and D_t.

Single source of truth for the macro gate inputs: the persisted registry
signals (`s_tnx`, `s_dxy`, `s_fx`, `d_t_{n}`) and the wide frame handed to
strategies (`compute_macro_frame`) are derived from the same functions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from system.libs.signals.base import Signals

# Windows the notebook computed D_t for (the strategy uses n_macro = 10).
_MACRO_WINDOWS = [10, 20, 40, 60]
_EPSILON = 1e-9

# Active macro gate window (GoldEnsembleStrategy.n_macro): the registry
# stores the component scores at this window so they sum to D_t_10.
N_MACRO = 10


def _unified_frame(tnx: pd.Series, dxy: pd.Series, fx: pd.Series) -> pd.DataFrame:
    """Raw + 1-day-delayed macro series on the unified date index.

    A 1-day lag (shift(1)) prevents look-ahead bias.
    """
    unified_index = tnx.index.union(dxy.index).union(fx.index).sort_values()
    frame = pd.DataFrame(index=unified_index)
    frame["TNX"] = tnx.reindex(unified_index).ffill()
    frame["DXY"] = dxy.reindex(unified_index).ffill()
    frame["FX"] = fx.reindex(unified_index).ffill()
    frame["TNX_delayed"] = frame["TNX"].shift(1)
    frame["DXY_delayed"] = frame["DXY"].shift(1)
    frame["FX_delayed"] = frame["FX"].shift(1)
    return frame


def component_score(series: pd.Series, *, sign: int, window: int) -> pd.Series:
    """Tanh-normalized short-term reversal score: `sign * tanh(r / sigma)`.

    `r` is the return of `series` over the lookback window, normalized by
    its rolling volatility and squashed with `tanh`.
    """
    returns = series.pct_change(window)
    sigma = returns.rolling(window).std()
    return sign * np.tanh(returns / (sigma + _EPSILON))


def compute_d_t(tnx: pd.Series, dxy: pd.Series, fx: pd.Series, window: int) -> pd.Series:
    """Macro direction score: mean of the three delayed component scores."""
    frame = _unified_frame(tnx, dxy, fx)
    score = (
        component_score(frame["TNX_delayed"], sign=-1, window=window)  # rising yields hurt gold
        + component_score(frame["DXY_delayed"], sign=-1, window=window)  # stronger dollar hurts gold
        + component_score(frame["FX_delayed"], sign=+1, window=window)  # weaker KRW lifts KRW gold
    ) / 3
    score.name = f"D_t_{window}"
    return score


def compute_s_tnx(tnx: pd.Series, dxy: pd.Series, fx: pd.Series) -> pd.Series:
    """TNX component score of `D_t_N_MACRO` (rising yields hurt gold)."""
    return component_score(_unified_frame(tnx, dxy, fx)["TNX_delayed"], sign=-1, window=N_MACRO)


def compute_s_dxy(tnx: pd.Series, dxy: pd.Series, fx: pd.Series) -> pd.Series:
    """DXY component score of `D_t_N_MACRO` (stronger dollar hurts gold)."""
    return component_score(_unified_frame(tnx, dxy, fx)["DXY_delayed"], sign=-1, window=N_MACRO)


def compute_s_fx(tnx: pd.Series, dxy: pd.Series, fx: pd.Series) -> pd.Series:
    """FX component score of `D_t_N_MACRO` (weaker KRW lifts KRW gold)."""
    return component_score(_unified_frame(tnx, dxy, fx)["FX_delayed"], sign=+1, window=N_MACRO)


def compute_macro_frame(tnx: pd.Series, dxy: pd.Series, fx: pd.Series) -> Signals:
    """Replicate notebook cell 4 as a wide `Signals` frame.

    Columns: raw + delayed macro series and `D_t_{n}` for each window in
    `_MACRO_WINDOWS`. The frame is passed to strategies via
    `MarketView.signals`; only the registry signals are persisted to
    signals.db.
    """
    frame = _unified_frame(tnx, dxy, fx)
    for window in _MACRO_WINDOWS:
        frame[f"D_t_{window}"] = compute_d_t(tnx, dxy, fx, window)
    return Signals(frame, name="macro", source="macro")
