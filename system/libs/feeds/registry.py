"""Registry of all known data feeds (name → class + source params)."""

from __future__ import annotations

import sqlite3

from system.libs.feeds.base import DataFeed
from system.libs.feeds.macro import ScalarFeed
from system.libs.feeds.yfinance import OhlcvFeed

# Trading stock: TIGER KRX 금현물. The tossinvest broker uses the bare 6-digit
# KR code ("0072R0"); yfinance uses the ".KS" suffix.
TRADING_STOCK_FEED = "ohlcv_0072R0KS"
TRADING_STOCK_SYMBOL = "0072R0"
PROXY_GOLD_FEED = "ohlcv_411060KS"

FEED_DEFINITIONS: dict[str, tuple[type[DataFeed], dict[str, str]]] = {
    TRADING_STOCK_FEED: (OhlcvFeed, {"ticker": "0072R0.KS"}),
    PROXY_GOLD_FEED: (OhlcvFeed, {"ticker": "411060.KS"}),
    "macro_tnx": (ScalarFeed, {"ticker": "^TNX"}),
    "macro_dxy": (ScalarFeed, {"ticker": "DX-Y.NYB"}),
    "macro_usdkrw": (ScalarFeed, {"ticker": "USDKRW=X"}),
}


def feed_names() -> list[str]:
    return list(FEED_DEFINITIONS)


def feed_name_for_symbol(symbol: str) -> str | None:
    """Reverse lookup: broker/yfinance symbol → feed name (None if unknown)."""
    for name, (_, source_params) in FEED_DEFINITIONS.items():
        if source_params.get("ticker") == symbol:
            return name
    # Bare KR broker symbol (e.g. "0072R0") matches the ohlcv feed prefix.
    candidate = f"ohlcv_{symbol.lower()}"
    for name in FEED_DEFINITIONS:
        if name.lower() == candidate:
            return name
    return None


def build_feed(name: str) -> DataFeed:
    """Construct the (empty) feed instance for a registered feed name."""
    if name not in FEED_DEFINITIONS:
        raise KeyError(f"Unknown data feed: {name!r}")
    feed_class, source_params = FEED_DEFINITIONS[name]
    return feed_class(name=name, source_params=source_params)


def load_feed(conn: sqlite3.Connection, name: str) -> DataFeed:
    """Build the feed and hydrate it from feed.db."""
    return build_feed(name).load(conn)
