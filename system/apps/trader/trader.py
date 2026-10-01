"""Trader: one decision cycle (data feeds → strategy → broker → logs)."""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from system.apps.trader.broker.base import BrokerClient
from system.libs.db import metadata
from system.libs.feeds.base import DataFeed
from system.libs.feeds.registry import (
    TRADING_STOCK_FEED,
    TRADING_STOCK_SYMBOL,
    load_feed,
)
from system.libs.strategy.base import MarketView, Signal
from system.libs.strategy.gold_ensemble import GoldEnsembleStrategy, compute_macro_frame

logger = logging.getLogger(__name__)

MACRO_FEEDS = ["macro_tnx", "macro_dxy", "macro_usdkrw"]


def now_kst(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


class Trader:
    def __init__(
        self,
        *,
        broker: BrokerClient,
        feed_conn: sqlite3.Connection,
        metadata_conn: sqlite3.Connection,
        timezone_name: str = "Asia/Seoul",
    ) -> None:
        self.broker = broker
        self.feed_conn = feed_conn
        self.metadata_conn = metadata_conn
        self.timezone = ZoneInfo(timezone_name)
        self.strategy = GoldEnsembleStrategy()

    # ── one cycle ──────────────────────────────────────────────────────────
    def run_cycle(self) -> Signal:
        ts = now_kst(str(self.timezone)).isoformat()

        enabled = metadata.get_state(self.metadata_conn, "trader_enabled", "true") == "true"
        if not enabled:
            self._record_decision(ts, Signal.HOLD, "disabled", {}, executed=False)
            logger.info("Cycle skipped: trader disabled by app_state")
            return Signal.HOLD

        if not self.broker.is_market_open():
            self._record_decision(ts, Signal.HOLD, "market_closed", {}, executed=False)
            logger.info("Cycle skipped: market closed")
            return Signal.HOLD

        # 1. Update data feeds (idempotent) and hydrate them.
        self.update_feeds()
        trading_feed = load_feed(self.feed_conn, TRADING_STOCK_FEED)
        macro_frame = self.build_macro_frame()

        # 2. Account state (position quantity + entry price come from the broker).
        account = self.broker.get_account()
        position = account.position(TRADING_STOCK_SYMBOL)

        # 3. Same-day provisional bar from the broker (yfinance has no live bar).
        view_frame = self.apply_snapshot(trading_feed, TRADING_STOCK_SYMBOL)

        # 4. Decide.
        view = MarketView(
            symbol=TRADING_STOCK_SYMBOL,
            ohlcv=view_frame,
            macro=macro_frame,
            position_quantity=position.quantity if position else 0,
            position_entry_price=position.avg_price if position else None,
        )
        signal, indicators = self.strategy.decide(view)
        logger.info("Decision: %s (indicators=%s)", signal.value, indicators)
        self._record_decision(ts, signal, indicators.pop("reason", "strategy"), indicators)

        # 5. Execute (whole positions only).
        executed = self.execute(signal, TRADING_STOCK_SYMBOL, account)

        # 6. Post-trade account snapshot.
        self.record_snapshot()
        return signal

    # ── steps ──────────────────────────────────────────────────────────────
    def update_feeds(self, names: list[str] | None = None) -> dict[str, int]:
        updated: dict[str, int] = {}
        for name in names or [TRADING_STOCK_FEED, *MACRO_FEEDS]:
            feed = load_feed(self.feed_conn, name)
            updated[name] = feed.update(self.feed_conn)
        return updated

    def build_macro_frame(self) -> pd.DataFrame:
        macro_series = []
        for name in MACRO_FEEDS:
            feed = load_feed(self.feed_conn, name)
            macro_series.append(feed.to_series().rename(name.split("_", 1)[1].upper()))
        return compute_macro_frame(*macro_series)

    def apply_snapshot(self, feed: DataFeed, symbol: str) -> pd.DataFrame:
        last_date = feed.START_DATE if len(feed) == 0 else feed.index[-1].date()
        today = date.today()
        if last_date >= today:
            return feed
        snapshot_row = feed.snapshot(self.broker)
        if snapshot_row is None:
            return feed
        logger.info("Appended same-day snapshot row for %s (%s)", symbol, today)
        return feed.with_row(snapshot_row)

    def execute(self, signal: Signal, symbol: str, account) -> bool:
        ts = now_kst(str(self.timezone)).isoformat()
        position = account.position(symbol)

        if signal is Signal.BUY and position is None:
            price = self.broker.get_last_price(symbol)
            if price is None or price <= 0:
                self._record_trade(ts, symbol, "BUY", 0, 0.0, None, "REJECTED", "no_price")
                return False
            quantity = math.floor(account.cash / price)  # whole positions only
            if quantity < 1:
                self._record_trade(ts, symbol, "BUY", 0, price, None, "REJECTED", "insufficient_cash")
                return False
            result = self.broker.buy(symbol, quantity)
            self._record_trade(
                ts, symbol, "BUY", result.quantity, result.price, result.order_id, result.status, result.note
            )
            return result.status == "FILLED"

        if signal is Signal.SELL and position is not None and position.quantity > 0:
            result = self.broker.sell(symbol, position.quantity)  # sell entire position
            self._record_trade(
                ts, symbol, "SELL", result.quantity, result.price, result.order_id, result.status, result.note
            )
            return result.status == "FILLED"

        return False

    def record_snapshot(self) -> None:
        account = self.broker.get_account()
        ts = now_kst(str(self.timezone)).isoformat()
        metadata.record_snapshot(
            self.metadata_conn,
            ts=ts,
            cash=account.cash,
            market_value=account.market_value,
            total=account.total,
            positions=[
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "avg_price": position.avg_price,
                    "last_price": position.last_price,
                }
                for position in account.positions
            ],
        )

    # ── log writers ────────────────────────────────────────────────────────
    def _record_decision(
        self, ts: str, signal: Signal, reason: str, indicators: dict, executed: bool | None = None
    ) -> None:
        metadata.record_decision(
            self.metadata_conn,
            ts=ts,
            symbol=TRADING_STOCK_SYMBOL,
            signal=signal.value,
            reason=reason,
            indicators=indicators,
            executed=bool(executed),
        )

    def _record_trade(
        self,
        ts: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        order_id: str | None,
        status: str,
        note: str | None,
    ) -> None:
        metadata.record_trade(
            self.metadata_conn,
            ts=ts,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_id=order_id,
            status=status,
            strategy=self.strategy.name,
            note=note,
        )
