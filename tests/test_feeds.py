"""DataFeed: idempotent updates, load-from-db, same-day snapshot hook."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from system.apps.trader.broker.base import DayBar
from system.libs.db import feed_store
from system.libs.feeds.macro import ScalarFeed
from system.libs.feeds.registry import build_feed, load_feed
from system.libs.feeds.yfinance import OhlcvFeed
from tests.conftest import FixedPriceMockBroker, make_ohlcv_frame, store_ohlcv, store_scalar


class StubOhlcvFeed(OhlcvFeed):
    """OhlcvFeed whose upstream fetch is stubbed (no network)."""

    upstream_rows: list[dict] = []

    def fetch_rows(self, start, end):
        return [
            row
            for row in self.upstream_rows
            if date.fromisoformat(str(row["date"])[:10]) >= start and date.fromisoformat(str(row["date"])[:10]) <= end
        ]


def test_load_empty_feed_returns_empty_frame(feed_conn):
    feed = build_feed("ohlcv_0072R0KS").load(feed_conn)
    assert isinstance(feed, pd.DataFrame)
    assert len(feed) == 0
    assert feed.START_DATE is None
    assert feed.feed_source_params["ticker"] == "0072R0.KS"


def test_update_fetches_only_missing_range(feed_conn, monkeypatch):
    stored = make_ohlcv_frame(days=30)
    stored = stored.iloc[:25]  # db has 25 days; upstream has 30
    store_ohlcv(feed_conn, "ohlcv_0072R0KS", stored)

    feed = StubOhlcvFeed(name="ohlcv_0072R0KS", source_params={"ticker": "0072R0.KS"})
    full = make_ohlcv_frame(days=30)
    feed.upstream_rows = [
        {
            "date": index.date().isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for index, row in full.iterrows()
    ]

    inserted = feed.update(feed_conn)
    assert inserted == 5
    assert feed_store.get_feed_meta(feed_conn, "ohlcv_0072R0KS")["row_count"] == 30

    # Second update: nothing new upstream → nothing stored.
    assert feed.update(feed_conn) == 0


def test_load_hydrates_stored_rows(feed_conn):
    store_ohlcv(feed_conn, "ohlcv_411060KS", make_ohlcv_frame(days=10))
    feed = load_feed(feed_conn, "ohlcv_411060KS")
    assert len(feed) == 10
    assert feed.START_DATE is not None
    assert list(feed.columns) == ["open", "high", "low", "close", "volume"]


def test_scalar_feed_to_series(feed_conn):
    dates = pd.bdate_range(end=date.today(), periods=5)
    frame = pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=dates)
    rows = [{"date": index.date().isoformat(), "value": row["value"]} for index, row in frame.iterrows()]
    feed_store.upsert_rows(
        feed_conn,
        "macro_tnx",
        "scalar",
        rows,
        source="test",
        source_params={},
        start_date=dates[0].date(),
        now=feed_now(),
    )
    feed = load_feed(feed_conn, "macro_tnx")
    assert feed.to_series().iloc[-1] == 5.0


def feed_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def test_snapshot_returns_none_without_price(feed_conn, mock_broker):
    mock_broker.price_lookup = lambda symbol: None
    feed = build_feed("ohlcv_0072R0KS").load(feed_conn)
    assert feed.snapshot(mock_broker) is None


def test_snapshot_prefers_broker_day_bar(feed_conn):
    """A real intraday DayBar beats the flat last-price fallback bar."""

    class DayBarBroker(FixedPriceMockBroker):
        def get_day_bar(self, symbol):
            return DayBar(
                date=date.today(),
                open=12_200.0,
                high=12_350.0,
                low=12_100.0,
                close=12_300.0,
                volume=850_000,
            )

    broker = DayBarBroker(Path("/tmp/unused-positions.json"))
    feed = build_feed("ohlcv_0072R0KS").load(feed_conn)
    row = feed.snapshot(broker)
    assert row is not None
    assert row["open"] == 12_200.0
    assert row["high"] == 12_350.0
    assert row["low"] == 12_100.0
    assert row["close"] == 12_300.0
    assert row["volume"] == 850_000
    assert row.name.date() == date.today()


def test_snapshot_falls_back_to_flat_bar_without_day_bar(feed_conn, mock_broker):
    """get_day_bar → None (error/holiday degradation) keeps the flat bar."""
    mock_broker.get_day_bar = lambda symbol: None
    feed = build_feed("ohlcv_0072R0KS").load(feed_conn)
    row = feed.snapshot(mock_broker)
    assert row is not None
    assert row["open"] == row["high"] == row["low"] == row["close"] == 10_000.0
    assert row["volume"] == 0


def test_mock_broker_day_bar_is_flat(feed_conn, mock_broker):
    day_bar = mock_broker.get_day_bar("0072R0")
    assert day_bar is not None
    assert day_bar.date == date.today()
    assert day_bar.open == day_bar.high == day_bar.low == day_bar.close == 10_000.0
    assert day_bar.volume == 0



def test_with_row_appends_provisional_bar(feed_conn, mock_broker):
    store_ohlcv(feed_conn, "ohlcv_0072R0KS", make_ohlcv_frame(days=5))
    feed = load_feed(feed_conn, "ohlcv_0072R0KS")
    row = pd.Series(
        {"open": 10_000.0, "high": 10_000.0, "low": 10_000.0, "close": 10_000.0, "volume": 0},
        name=pd.Timestamp(date.today()),
    )
    appended = feed.with_row(row)
    assert len(appended) == len(feed) + 1
    assert appended.index[-1].date() == date.today()
