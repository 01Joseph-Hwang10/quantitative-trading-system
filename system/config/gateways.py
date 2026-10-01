"""Third-party gateways and resource factories (broker, database connections)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from system.apps.trader.broker.base import BrokerClient
from system.apps.trader.broker.mock import MockBroker
from system.apps.trader.broker.tossinvest import TossBroker
from system.config.settings import Settings
from system.libs.db import feed_store, metadata
from system.libs.feeds import registry


@dataclass
class Connections:
    metadata: sqlite3.Connection
    feed: sqlite3.Connection

    def close(self) -> None:
        self.metadata.close()
        self.feed.close()


@contextmanager
def open_connections(settings: Settings):
    """Open metadata.db and feed.db (creating schemas as needed)."""
    settings.ensure_data_dir()
    connections = Connections(
        metadata=metadata.connect(settings.metadata_db_path),
        feed=feed_store.connect(settings.feed_db_path),
    )
    try:
        metadata.seed_state(connections.metadata, {"trader_enabled": str(settings.trader_enabled).lower()})
        yield connections
    finally:
        connections.close()


def build_broker(settings: Settings, feed_conn: sqlite3.Connection | None = None) -> BrokerClient:
    """Instantiate the configured BrokerClient (mock by default — never live)."""
    if settings.broker == "toss":
        return TossBroker(settings)
    if settings.broker == "mock":
        return _build_mock_broker(settings, feed_conn)
    raise ValueError(f"Unknown broker mode: {settings.broker!r} (expected 'mock' or 'toss')")


def _build_mock_broker(settings: Settings, feed_conn: sqlite3.Connection | None) -> MockBroker:
    """Mock broker whose fills use the latest stored close from feed.db.

    `price_lookup` accepts any symbol form (broker KR code or yfinance ticker)
    and maps it to a registered feed; unknown symbols price at None.
    """

    def price_lookup(symbol: str) -> float | None:
        connection = feed_conn
        owned = False
        if connection is None:
            if not settings.feed_db_path.exists():
                return None
            connection = feed_store.connect(settings.feed_db_path)
            owned = True
        try:
            feed_name = registry.feed_name_for_symbol(symbol)
            if feed_name is None:
                return None
            return feed_store.last_value(connection, feed_name)
        finally:
            if owned:
                connection.close()

    state_path = Path(settings.data_dir) / "mock_positions.json"
    return MockBroker(state_path, price_lookup, timezone_name=settings.timezone)
