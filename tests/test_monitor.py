"""Monitor support functions: authorization and performance metrics."""

from __future__ import annotations

from system.apps.monitor.support import (
    compute_performance,
    decisions_frame,
    is_authorized,
)
from system.apps.trader.trader import Trader
from system.config.settings import Settings
from system.libs.db import metadata
from system.libs.strategy.base import Signal
from tests.conftest import FixedPriceMockBroker, store_ohlcv
from tests.test_trader import StubStrategy, seed_feed_world


def test_is_authorized_parses_allowlist(tmp_path):
    settings = Settings(authorized_users="a@x.com, b@y.com ,", data_dir=tmp_path, _env_file=None)
    assert is_authorized("a@x.com", settings)
    assert is_authorized("b@y.com", settings)
    assert not is_authorized("c@z.com", settings)
    assert not is_authorized(None, settings)


def test_compute_performance_on_seeded_history(metadata_conn, feed_conn, signals_conn, tmp_path):
    broker = FixedPriceMockBroker(tmp_path / "positions.json", price=10_000.0)
    trader = Trader(broker=broker, feed_conn=feed_conn, signals_conn=signals_conn, metadata_conn=metadata_conn)
    seed_feed_world(feed_conn)

    trader.strategy = StubStrategy(Signal.BUY)
    trader.run_cycle()
    broker.price_lookup = lambda symbol: 11_000.0  # price rises 10%
    trader.strategy = StubStrategy(Signal.SELL)
    trader.run_cycle()

    performance = compute_performance(metadata_conn)
    assert performance["trade_count"] == 2
    assert performance["win_rate"] == 1.0  # one winning round trip
    assert performance["total_pnl"] == pytest_approx(2_000_000.0)
    assert performance["profit_factor"] is None  # no losses → undefined (infinite)
    assert performance["max_drawdown_pct"] == 0.0  # equity never declined
    assert performance["max_drawdown_amount"] == 0.0
    assert performance["max_drawdown_duration_days"] == 0
    assert not performance["max_drawdown_ongoing"]


def test_compute_performance_drawdown_and_duration(metadata_conn):
    # Equity: 100 → 120 (peak) → 90 (trough) → 110 → 130 (recovery + new peak).
    totals = [100.0, 120.0, 90.0, 110.0, 130.0]
    for index, total in enumerate(totals):
        metadata.record_snapshot(
            metadata_conn,
            ts=f"2026-01-0{index + 1}T00:00:00+09:00",
            cash=total,
            market_value=0.0,
            total=total,
            positions=[],
        )

    performance = compute_performance(metadata_conn)
    assert performance["total_pnl"] == pytest_approx(30.0)
    assert performance["total_return_pct"] == pytest_approx(0.3)
    assert performance["max_drawdown_pct"] == pytest_approx(90.0 / 120.0 - 1)
    assert performance["max_drawdown_amount"] == pytest_approx(-30.0)
    # Peak on Jan 2 (120) recovered on Jan 5 (130).
    assert performance["max_drawdown_duration_days"] == 3
    assert not performance["max_drawdown_ongoing"]
    assert isinstance(performance["sharpe_ratio"], float)
    assert isinstance(performance["volatility"], float)
    assert len(performance["drawdown_curve"]) == 4  # one point per return


def test_compute_performance_timespan_filter(metadata_conn):
    totals = [100.0, 120.0, 90.0, 110.0, 130.0]
    for index, total in enumerate(totals):
        metadata.record_snapshot(
            metadata_conn,
            ts=f"2026-01-0{index + 1}T00:00:00+09:00",
            cash=total,
            market_value=0.0,
            total=total,
            positions=[],
        )

    # Window starting after the drawdown: monotonic recovery, no drawdown.
    performance = compute_performance(metadata_conn, start_ts="2026-01-03T00:00:00+09:00")
    assert [total for _, total in performance["equity_curve"]] == [90.0, 110.0, 130.0]
    assert performance["total_pnl"] == pytest_approx(40.0)
    assert performance["max_drawdown_pct"] == 0.0
    assert performance["max_drawdown_duration_days"] == 0


def test_compute_performance_sharpe_undefined(metadata_conn):
    # Fewer than 3 snapshots → no daily-return statistics.
    for index, total in enumerate([100.0, 110.0]):
        metadata.record_snapshot(
            metadata_conn,
            ts=f"2026-01-0{index + 1}T00:00:00+09:00",
            cash=total,
            market_value=0.0,
            total=total,
            positions=[],
        )
    performance = compute_performance(metadata_conn)
    assert performance["sharpe_ratio"] is None
    assert performance["volatility"] is None

    # Flat equity → zero std → undefined Sharpe.
    for index, total in enumerate([100.0, 100.0, 100.0]):
        metadata.record_snapshot(
            metadata_conn,
            ts=f"2026-02-0{index + 1}T00:00:00+09:00",
            cash=total,
            market_value=0.0,
            total=total,
            positions=[],
        )
    performance = compute_performance(metadata_conn, start_ts="2026-02-01T00:00:00+09:00")
    assert performance["sharpe_ratio"] is None
    assert performance["volatility"] == 0.0


def test_compute_performance_ongoing_drawdown(metadata_conn):
    # Still underwater at the end of the window: 100 → 150 → 120.
    for index, total in enumerate([100.0, 150.0, 120.0]):
        metadata.record_snapshot(
            metadata_conn,
            ts=f"2026-03-0{index + 1}T00:00:00+09:00",
            cash=total,
            market_value=0.0,
            total=total,
            positions=[],
        )
    performance = compute_performance(metadata_conn)
    assert performance["max_drawdown_duration_days"] == 1  # Mar 2 peak → Mar 3
    assert performance["max_drawdown_ongoing"]


def test_decisions_frame_maps_rows_newest_first(metadata_conn):
    metadata.record_decision(
        metadata_conn,
        ts="2026-10-06T15:00:00+09:00",
        symbol="0072R0",
        signal="HOLD",
        reason="strategy",
        indicators={"adx": 26.6, "close": 11890.0},
    )
    metadata.record_decision(
        metadata_conn,
        ts="2026-10-07T15:00:00+09:00",
        symbol="0072R0",
        signal="BUY",
        reason="strategy",
        indicators={"close": 11900.0},
        executed=True,
    )

    frame = decisions_frame([dict(row) for row in metadata.list_decisions(metadata_conn)])

    assert list(frame.columns) == ["ts", "symbol", "signal", "reason", "executed", "indicators"]
    assert frame["signal"].tolist() == ["BUY", "HOLD"]  # newest first
    assert frame["executed"].tolist() == [True, False]  # bool, not 0/1
    assert frame.iloc[0]["indicators"] == '{"close":11900.0}'  # compacted
    assert "adx" in frame.iloc[1]["indicators"]


def test_decisions_frame_empty(metadata_conn):
    assert decisions_frame([]).empty


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
