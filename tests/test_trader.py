"""Trader cycle: signals → mock execution → metadata.db logging."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from system.apps.trader.broker.mock import SEED_CASH
from system.apps.trader.trader import Trader
from system.libs.db import metadata, signals_store
from system.libs.feeds.registry import TRADING_STOCK_FEED, TRADING_STOCK_SYMBOL
from system.libs.strategy.base import Signal
from system.libs.strategy.gold_ensemble import GoldEnsembleStrategy
from tests.conftest import (
    FixedPriceMockBroker,
    make_ohlcv_frame,
    store_ohlcv,
    store_scalar,
)


def seed_feed_world(feed_conn, days=90):
    """Store trading-stock OHLCV + macro feeds so a cycle can decide."""
    store_ohlcv(feed_conn, TRADING_STOCK_FEED, make_ohlcv_frame(days=days))
    dates = pd.bdate_range(end=date.today(), periods=days)
    store_scalar(feed_conn, "macro_tnx", pd.DataFrame({"value": 4.0}, index=dates))
    store_scalar(feed_conn, "macro_dxy", pd.DataFrame({"value": 104.0}, index=dates))
    store_scalar(feed_conn, "macro_usdkrw", pd.DataFrame({"value": 1400.0}, index=dates))


class StubStrategy(GoldEnsembleStrategy):
    def __init__(self, forced_signal: Signal):
        self.forced_signal = forced_signal

    def decide(self, view):
        return self.forced_signal, {"reason": "stub"}


def test_cycle_hold_skip_when_disabled(trader, metadata_conn):
    metadata.set_state(metadata_conn, "trader_enabled", "false")
    signal = trader.run_cycle()
    assert signal is Signal.HOLD
    decisions = metadata.list_decisions(metadata_conn)
    assert decisions[0]["reason"] == "disabled"
    assert decisions[0]["executed"] == 0


def test_cycle_hold_skip_when_market_closed(mock_broker, feed_conn, signals_conn, metadata_conn):
    mock_broker.is_market_open = lambda: False
    trader = Trader(broker=mock_broker, feed_conn=feed_conn, signals_conn=signals_conn, metadata_conn=metadata_conn)
    signal = trader.run_cycle()
    assert signal is Signal.HOLD
    assert metadata.list_decisions(metadata_conn)[0]["reason"] == "market_closed"


def test_cycle_logs_decision_and_snapshot(trader, feed_conn, metadata_conn, mock_broker):
    seed_feed_world(feed_conn)
    signal = trader.run_cycle()
    assert signal is Signal.HOLD  # flat macro (D_t=0) → no entry

    decisions = metadata.list_decisions(metadata_conn)
    assert decisions[0]["symbol"] == TRADING_STOCK_SYMBOL
    assert decisions[0]["signal"] == "HOLD"
    assert "adx" in decisions[0]["indicators_json"]

    snapshot = metadata.latest_snapshot(metadata_conn)
    assert snapshot is not None
    assert snapshot["cash"] == pytest.approx(SEED_CASH)
    assert snapshot["positions_json"] == "[]"


def test_buy_and_sell_flow_end_to_end(trader, feed_conn, metadata_conn, mock_broker):
    seed_feed_world(feed_conn)

    # Force a BUY: patch the strategy to always signal BUY.
    trader.strategy = StubStrategy(Signal.BUY)
    trader.run_cycle()
    account = mock_broker.get_account()
    assert account.position(TRADING_STOCK_SYMBOL) is not None
    trades = metadata.list_trades(metadata_conn)
    assert trades[0]["side"] == "BUY" and trades[0]["status"] == "FILLED"
    # Whole units only: floor(cash / price) = floor(20,000,000 / 10,000) = 2,000.
    assert trades[0]["quantity"] == 2_000

    # Then a SELL: the entire position.
    trader.strategy = StubStrategy(Signal.SELL)
    trader.run_cycle()
    account = mock_broker.get_account()
    assert account.position(TRADING_STOCK_SYMBOL) is None
    sells = [row for row in metadata.list_trades(metadata_conn) if row["side"] == "SELL"]
    assert sells and sells[0]["quantity"] == 2_000
    assert sells[0]["status"] == "FILLED"


def test_insufficient_cash_is_rejected(feed_conn, signals_conn, metadata_conn, tmp_path):
    broke_broker = FixedPriceMockBroker(tmp_path / "positions.json", price=10_000.0)
    broke_broker._state["cash"] = 5_000.0  # less than one share
    broke_broker._save_state()
    trader = Trader(broker=broke_broker, feed_conn=feed_conn, signals_conn=signals_conn, metadata_conn=metadata_conn)
    seed_feed_world(feed_conn)
    trader.strategy = StubStrategy(Signal.BUY)
    trader.run_cycle()
    trades = metadata.list_trades(metadata_conn)
    assert trades[0]["status"] == "REJECTED"
    assert trades[0]["note"] == "insufficient_cash"


def test_update_feeds_is_idempotent(trader, feed_conn):
    seed_feed_world(feed_conn)
    first = trader.update_feeds()
    second = trader.update_feeds()
    assert all(count == 0 for count in first.values())
    assert all(count == 0 for count in second.values())


def test_run_cycle_updates_signals(signals_conn, trader, feed_conn):
    seed_feed_world(feed_conn)
    trader.run_cycle()
    stored = {row["name"] for row in signals_store.list_signals(signals_conn)}
    assert {"s_tnx", "s_dxy", "s_fx", "d_t_10", "adx_14", "t_t"} <= stored
    # Idempotent: a second cycle stores nothing new.
    counts = trader.update_signals()
    assert all(count == 0 for count in counts.values())
