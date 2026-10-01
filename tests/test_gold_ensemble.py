"""GoldEnsembleStrategy: macro frame parity, regimes, entries, exits."""

from __future__ import annotations

import numpy as np
import pandas as pd

from system.libs.strategy.base import MarketView, Signal
from system.libs.strategy.gold_ensemble import GoldEnsembleStrategy, compute_macro_frame


def test_compute_macro_frame_parity():
    """D_t values must match an independent re-implementation of cell 4."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2025-01-01", periods=250)  # enough burn-in for D_t_60
    tnx = pd.Series(4.2 + rng.normal(0, 0.05, len(dates)).cumsum() * 0.01, index=dates)
    dxy = pd.Series(104.0 + rng.normal(0, 0.3, len(dates)).cumsum() * 0.02, index=dates)
    fx = pd.Series(1400.0 + rng.normal(0, 5.0, len(dates)).cumsum() * 0.5, index=dates)

    macro = compute_macro_frame(tnx, dxy, fx)
    for window in (10, 20, 40, 60):
        column = f"D_t_{window}"
        assert column in macro.columns

        # Independent reimplementation of the notebook formula.
        tnx_delayed = tnx.reindex(macro.index).ffill().shift(1)
        dxy_delayed = dxy.reindex(macro.index).ffill().shift(1)
        fx_delayed = fx.reindex(macro.index).ffill().shift(1)
        expected = (
            -np.tanh(tnx_delayed.pct_change(window) / (tnx_delayed.pct_change(window).rolling(window).std() + 1e-9))
            - np.tanh(dxy_delayed.pct_change(window) / (dxy_delayed.pct_change(window).rolling(window).std() + 1e-9))
            + np.tanh(fx_delayed.pct_change(window) / (fx_delayed.pct_change(window).rolling(window).std() + 1e-9))
        ) / 3
        actual = macro[column].dropna()
        assert len(actual) > 0, f"{column} is entirely NaN (fixture too short)"
        np.testing.assert_allclose(actual.to_numpy(), expected.reindex(actual.index).to_numpy(), atol=1e-12)
        # Tanh-bounded scores mean |D_t| <= 1.
        assert actual.abs().max() <= 1.0


def _view(ohlcv, macro, quantity=0, entry_price=None) -> MarketView:
    return MarketView(
        symbol="0072R0",
        ohlcv=ohlcv,
        macro=macro,
        position_quantity=quantity,
        position_entry_price=entry_price,
    )


def _trending_up_ohlcv(days: int = 120, base_price: float = 10_000.0) -> pd.DataFrame:
    """Steady uptrend: high ADX (trend regime), MAs stacked upward."""
    dates = pd.bdate_range(end=pd.Timestamp("2026-09-01"), periods=days)
    prices = base_price + 20.0 * np.arange(days)
    return pd.DataFrame(
        {"open": prices, "high": prices + 1, "low": prices - 1, "close": prices, "volume": 100},
        index=dates,
    )


def _flat_macro(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Constant macro series → D_t = 0 everywhere (no macro gate)."""
    return compute_macro_frame(
        pd.Series(4.0, index=ohlcv.index),
        pd.Series(104.0, index=ohlcv.index),
        pd.Series(1400.0, index=ohlcv.index),
    )


def test_warmup_hold_when_history_too_short():
    strategy = GoldEnsembleStrategy()
    ohlcv = _trending_up_ohlcv(days=30)
    signal, indicators = strategy.decide(_view(ohlcv, _flat_macro(ohlcv)))
    assert signal is Signal.HOLD
    assert indicators["reason"] == "warmup"


def test_trend_regime_timing_signal_with_flat_macro():
    """Uptrend → T_t=+1 in trend regime, but D_t=0 blocks entry while flat."""
    strategy = GoldEnsembleStrategy()
    ohlcv = _trending_up_ohlcv(days=120)
    signal, indicators = strategy.decide(_view(ohlcv, _flat_macro(ohlcv)))
    assert signal is Signal.HOLD
    assert indicators["regime"] == "trend"
    assert indicators["t_t"] == 1


def test_entry_requires_macro_gate_and_timing():
    """T_t=+1 uptrend + forced favorable macro → BUY while flat."""
    strategy = GoldEnsembleStrategy()
    ohlcv = _trending_up_ohlcv(days=120)
    macro = _flat_macro(ohlcv)
    macro["D_t_10"] = 0.9  # favorable macro score (above theta_entry=0.3)
    signal, _ = strategy.decide(_view(ohlcv, macro))
    assert signal is Signal.BUY


def test_stop_loss_exit_uses_entry_price():
    """Holding with entry far above market → stop-loss exit (cond5)."""
    strategy = GoldEnsembleStrategy()
    ohlcv = _trending_up_ohlcv(days=120)
    macro = _flat_macro(ohlcv)

    # Entry ~14% above market (> theta_stop=5%) → cond5 triggers.
    last_price = float(ohlcv["close"].iloc[-1])
    signal, indicators = strategy.decide(_view(ohlcv, macro, quantity=10, entry_price=last_price * 1.14))
    assert signal is Signal.SELL
    assert indicators["exit_reasons"] == ["stop_loss"]

    # Entry well below market → all exit conditions false → hold.
    signal, indicators = strategy.decide(_view(ohlcv, macro, quantity=10, entry_price=last_price * 0.9))
    assert signal is Signal.HOLD


def test_strategy_never_touches_broker_or_network():
    """Sanity: strategy module imports no broker/network dependencies."""
    import system.libs.strategy.gold_ensemble as module

    source = open(module.__file__).read()
    assert "tossinvest" not in source
    assert "yfinance" not in source
    assert "BrokerClient" not in source
