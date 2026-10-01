"""Monitor support functions: authorization and performance metrics."""

from __future__ import annotations

from datetime import datetime, timezone

from system.apps.monitor.support import compute_performance, is_authorized
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


def test_compute_performance_on_seeded_history(metadata_conn, feed_conn, tmp_path):
    broker = FixedPriceMockBroker(tmp_path / "positions.json", price=10_000.0)
    trader = Trader(broker=broker, feed_conn=feed_conn, metadata_conn=metadata_conn)
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


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
