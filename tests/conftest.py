"""Shared pytest fixtures: temp dbs, seeded feeds, mock broker, trader."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from system.apps.trader.broker.base import AccountState, OrderResult, Position
from system.apps.trader.broker.mock import MockBroker
from system.apps.trader.trader import Trader
from system.config.settings import Settings
from system.libs.db import feed_store, metadata


class FixedPriceMockBroker(MockBroker):
    """MockBroker with deterministic prices (no feed.db dependency)."""

    def __init__(self, state_path: Path, price: float = 10_000.0):
        super().__init__(state_path, price_lookup=lambda symbol: price)
        self.price = price

    def is_market_open(self) -> bool:
        return True


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    instance = Settings(data_dir=tmp_path / "data", broker="mock", _env_file=None)
    instance.ensure_data_dir()
    return instance


@pytest.fixture
def metadata_conn(settings: Settings) -> sqlite3.Connection:
    conn = metadata.connect(settings.metadata_db_path)
    metadata.seed_state(conn, {"trader_enabled": "true"})
    yield conn
    conn.close()


@pytest.fixture
def feed_conn(settings: Settings) -> sqlite3.Connection:
    conn = feed_store.connect(settings.feed_db_path)
    yield conn
    conn.close()


def make_ohlcv_frame(days: int = 120, base_price: float = 10_000.0) -> pd.DataFrame:
    """Deterministic upward-drifting OHLCV frame ending today."""
    dates = pd.bdate_range(end=date.today(), periods=days)
    prices = [base_price + index * 10 for index in range(days)]
    frame = pd.DataFrame(
        {
            "open": prices,
            "high": [price * 1.01 for price in prices],
            "low": [price * 0.99 for price in prices],
            "close": prices,
            "volume": [1_000] * days,
        },
        index=dates,
    )
    return frame


def store_ohlcv(conn: sqlite3.Connection, name: str, frame: pd.DataFrame) -> None:
    rows = [
        {
            "date": index.date().isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for index, row in frame.iterrows()
    ]
    feed_store.upsert_rows(
        conn,
        name,
        "ohlcv",
        rows,
        source="test",
        source_params={"ticker": "TEST.KS"},
        start_date=frame.index[0].date(),
        now=datetime(2026, 1, 1),
    )


def store_scalar(conn: sqlite3.Connection, name: str, frame: pd.DataFrame) -> None:
    rows = [{"date": index.date().isoformat(), "value": float(row["value"])} for index, row in frame.iterrows()]
    feed_store.upsert_rows(
        conn,
        name,
        "scalar",
        rows,
        source="test",
        source_params={"ticker": "TEST"},
        start_date=frame.index[0].date(),
        now=datetime(2026, 1, 1),
    )


@pytest.fixture
def mock_broker(tmp_path: Path) -> FixedPriceMockBroker:
    return FixedPriceMockBroker(tmp_path / "positions.json")


@pytest.fixture
def trader(mock_broker, feed_conn, metadata_conn) -> Trader:
    return Trader(broker=mock_broker, feed_conn=feed_conn, metadata_conn=metadata_conn)
