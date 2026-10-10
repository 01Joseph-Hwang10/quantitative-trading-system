"""Signals: registry, compute parity with the strategy, and update semantics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from system.libs.db import signals_store
from system.libs.signals.base import Signals
from system.libs.signals.macro import compute_macro_frame
from system.libs.signals.registry import build_signal, signal_names
from system.libs.signals.technical import compute_adx_series, compute_t_t_series
from system.libs.strategy.base import MarketView
from system.libs.strategy.gold_ensemble import GoldEnsembleStrategy
from tests.conftest import make_ohlcv_frame, store_ohlcv, store_scalar
from tests.test_trader import seed_feed_world


def test_registry_build_unknown_raises():
    with pytest.raises(KeyError):
        build_signal("no_such_signal")


def test_macro_update_backfills_and_is_idempotent(signals_conn, feed_conn):
    seed_feed_world(feed_conn)
    first = build_signal("d_t_10").update(signals_conn, feed_conn)
    assert first > 0
    second = build_signal("d_t_10").update(signals_conn, feed_conn)
    assert second == 0

    rows = signals_store.read_table(signals_conn, "d_t_10")
    meta = signals_store.get_signal_meta(signals_conn, "d_t_10")
    assert meta["row_count"] == len(rows)
    assert meta["source"] == "macro"


def test_stored_d_t_matches_compute_macro_frame(signals_conn, feed_conn):
    seed_feed_world(feed_conn)
    build_signal("d_t_10").update(signals_conn, feed_conn)

    # Recompute the wide macro frame the way the trader does and compare.
    series = []
    for name in ("macro_tnx", "macro_dxy", "macro_usdkrw"):
        from system.libs.feeds.registry import load_feed

        series.append(load_feed(feed_conn, name).to_series().rename(name.split("_", 1)[1].upper()))
    macro = compute_macro_frame(*series)

    stored = signals_store.read_table(signals_conn, "d_t_10")
    expected = macro["D_t_10"].dropna()
    assert len(stored) == len(expected)
    stored_frame = pd.DataFrame([dict(row) for row in stored])
    stored_frame["date"] = pd.to_datetime(stored_frame["date"]).dt.normalize()
    np.testing.assert_allclose(
        stored_frame.set_index("date")["value"].to_numpy(),
        expected.to_numpy(),
        atol=1e-12,
    )


def test_technical_update_stores_adx_and_t_t(signals_conn, feed_conn):
    seed_feed_world(feed_conn)
    assert build_signal("adx_14").update(signals_conn, feed_conn) > 0
    assert build_signal("t_t").update(signals_conn, feed_conn) > 0

    meta = signals_store.get_signal_meta(signals_conn, "adx_14")
    assert meta["source"] == "technical"
    # Steady uptrend fixture: trend regime, T_t = +1 after warm-up.
    t_t_rows = [dict(row) for row in signals_store.read_table(signals_conn, "t_t")]
    assert {row["value"] for row in t_t_rows} == {1.0}
    # Warm-up NaN heads are never stored.
    adx_rows = [dict(row) for row in signals_store.read_table(signals_conn, "adx_14")]
    assert all(row["value"] == row["value"] for row in adx_rows)


def test_update_with_missing_inputs_stores_nothing(signals_conn, feed_conn):
    assert build_signal("d_t_10").update(signals_conn, feed_conn) == 0
    assert signals_store.get_signal_meta(signals_conn, "d_t_10") is None


def test_load_hydrates_from_store(signals_conn, feed_conn):
    seed_feed_world(feed_conn)
    build_signal("s_fx").update(signals_conn, feed_conn)
    signal = build_signal("s_fx").load(signals_conn)
    assert isinstance(signal, Signals)
    assert len(signal) > 0
    assert signal.name == "s_fx"
    assert list(signal.columns) == ["value"]


def test_signal_metadata_survives_pandas_ops(signals_conn, feed_conn):
    seed_feed_world(feed_conn)
    signal = build_signal("s_fx").update(signals_conn, feed_conn)
    assert signal >= 0
    hydrated = build_signal("s_fx").load(signals_conn)
    tail = hydrated.tail(10)  # pandas op must keep the custom attributes
    assert tail.signal_name == "s_fx"
    assert tail.signal_source == "macro"


def test_registry_covers_all_signal_names():
    assert signal_names() == [
        "s_tnx",
        "s_dxy",
        "s_fx",
        "d_t_10",
        "d_t_20",
        "d_t_40",
        "d_t_60",
        "adx_14",
        "t_t",
    ]


def test_component_scores_sum_to_d_t():
    """s_tnx + s_dxy + s_fx at N_MACRO must equal D_t_10 (definition, not tuning)."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2025-06-02", periods=150)
    tnx = pd.Series(4.2 + rng.normal(0, 0.05, len(dates)).cumsum() * 0.01, index=dates)
    dxy = pd.Series(104.0 + rng.normal(0, 0.3, len(dates)).cumsum() * 0.02, index=dates)
    fx = pd.Series(1400.0 + rng.normal(0, 5.0, len(dates)).cumsum() * 0.5, index=dates)

    from system.libs.signals.macro import compute_s_dxy, compute_s_fx, compute_s_tnx

    summed = (compute_s_tnx(tnx, dxy, fx) + compute_s_dxy(tnx, dxy, fx) + compute_s_fx(tnx, dxy, fx)) / 3
    expected = compute_macro_frame(tnx, dxy, fx)["D_t_10"].dropna()
    np.testing.assert_allclose(summed.dropna().to_numpy(), expected.to_numpy(), atol=1e-12)


def test_t_t_series_matches_decide_last_bar():
    """The persisted timing series and the strategy's decision-time T_t agree."""
    ohlcv = make_ohlcv_frame(days=120)
    strategy = GoldEnsembleStrategy()
    macro = compute_macro_frame(
        pd.Series(4.0, index=ohlcv.index),
        pd.Series(104.0, index=ohlcv.index),
        pd.Series(1400.0, index=ohlcv.index),
    )
    view = MarketView(symbol="0072R0", ohlcv=ohlcv, signals=macro)
    _, indicators = strategy.decide(view)
    timing_last = compute_t_t_series(ohlcv).iloc[-1]
    assert indicators["t_t"] == int(timing_last)


def test_adx_series_matches_talib():
    import talib

    ohlcv = make_ohlcv_frame(days=120)
    adx = compute_adx_series(ohlcv)
    expected = talib.ADX(ohlcv["high"].to_numpy(), ohlcv["low"].to_numpy(), ohlcv["close"].to_numpy(), timeperiod=14)
    np.testing.assert_allclose(adx.to_numpy(), expected)
