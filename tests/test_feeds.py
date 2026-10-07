"""DataFeed: idempotent updates, load-from-db, same-day snapshot hook."""

from __future__ import annotations

import logging
from datetime import date, timedelta
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


def test_update_fetches_only_missing_range(feed_conn):
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

    # The default range ends yesterday (upstream daily bars are completed
    # sessions only), so today's row is intentionally not requested.
    last = stored.index[-1].date()
    yesterday = date.today() - timedelta(days=1)
    expected = sum(1 for index in full.index if last < index.date() <= yesterday)

    inserted = feed.update(feed_conn)
    assert inserted == expected
    assert feed_store.get_feed_meta(feed_conn, "ohlcv_0072R0KS")["row_count"] == 25 + expected

    # Second update: nothing new upstream → nothing stored.
    assert feed.update(feed_conn) == 0


def test_update_requests_only_completed_days(feed_conn):
    """The default fetch range ends yesterday: today's bar comes from snapshot()."""
    calls: list[tuple[date, date]] = []

    class RecordingFeed(StubOhlcvFeed):
        def fetch_rows(self, start, end):
            calls.append((start, end))
            return []

    # Store through two days ago so the fetch path runs (yesterday = range end).
    cutoff = date.today() - timedelta(days=2)
    yesterday = date.today() - timedelta(days=1)
    stored = make_ohlcv_frame(days=5)
    store_ohlcv(feed_conn, "ohlcv_0072R0KS", stored[[d <= cutoff for d in stored.index.date]])

    feed = RecordingFeed(name="ohlcv_0072R0KS", source_params={"ticker": "0072R0.KS"}).load(feed_conn)
    assert feed.update(feed_conn) == 0
    assert calls == [(feed.index[-1].date() + timedelta(days=1), yesterday)]


def test_update_makes_no_request_when_caught_up(feed_conn):
    """Already caught up through yesterday → zero upstream calls (no yfinance ERROR noise)."""
    calls: list[tuple[date, date]] = []

    class RecordingFeed(StubOhlcvFeed):
        def fetch_rows(self, start, end):
            calls.append((start, end))
            return []

    stored = make_ohlcv_frame(days=5)
    yesterday = date.today() - timedelta(days=1)
    store_ohlcv(feed_conn, "ohlcv_0072R0KS", stored[[d <= yesterday for d in stored.index.date]])

    feed = RecordingFeed(name="ohlcv_0072R0KS", source_params={"ticker": "0072R0.KS"}).load(feed_conn)
    assert feed.update(feed_conn) == 0
    assert calls == []


def test_update_fetch_error_is_swallowed_and_logged(feed_conn, caplog):
    """One flaky ticker logs a warning and returns 0 without aborting siblings."""

    class FailingFeed(StubOhlcvFeed):
        def fetch_rows(self, start, end):
            raise RuntimeError("upstream down")

    # Store through two days ago so the fetch path runs (yesterday = range end).
    cutoff = date.today() - timedelta(days=2)
    stored = make_ohlcv_frame(days=5)
    store_ohlcv(feed_conn, "ohlcv_0072R0KS", stored[[d <= cutoff for d in stored.index.date]])

    feed = FailingFeed(name="ohlcv_0072R0KS", source_params={"ticker": "0072R0.KS"}).load(feed_conn)
    with caplog.at_level(logging.WARNING, logger="system.libs.feeds.base"):
        assert feed.update(feed_conn) == 0
    assert any("fetch failed" in record.getMessage() for record in caplog.records)

    # A sibling feed in the same update loop still updates.
    sibling = StubOhlcvFeed(name="ohlcv_411060KS", source_params={"ticker": "411060.KS"})
    sibling.upstream_rows = [
        {"date": (date.today() - timedelta(days=1)).isoformat(), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}
    ]
    assert sibling.update(feed_conn) == 1


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
