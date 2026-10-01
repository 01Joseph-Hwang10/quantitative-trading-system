"""feed_store: upserts, reads, metadata, and drop semantics."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from system.libs.db import feed_store


def _rows(count: int, kind: str) -> list[dict]:
    if kind == "ohlcv":
        return [
            {
                "date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
                "open": 100.0 + offset,
                "high": 101.0 + offset,
                "low": 99.0 + offset,
                "close": 100.5 + offset,
                "volume": 1000,
            }
            for offset in range(count)
        ]
    return [
        {
            "date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
            "value": 5.0 + offset,
        }
        for offset in range(count)
    ]


@pytest.mark.parametrize("kind", ["ohlcv", "scalar"])
def test_upsert_and_read_roundtrip(feed_conn, kind):
    feed_store.upsert_rows(
        feed_conn,
        f"test_{kind}",
        kind,
        _rows(5, kind),
        source="test",
        source_params={"ticker": "X"},
        start_date=date(2026, 1, 1),
        now=datetime(2026, 1, 1),
    )
    rows = feed_store.read_table(feed_conn, f"test_{kind}")
    assert len(rows) == 5
    meta = feed_store.get_feed_meta(feed_conn, f"test_{kind}")
    assert meta["row_count"] == 5
    assert meta["source"] == "test"
    assert meta["start_date"] == "2026-01-01"


def test_upsert_is_idempotent(feed_conn):
    for _ in range(2):
        feed_store.upsert_rows(
            feed_conn,
            "feed_x",
            "scalar",
            _rows(3, "scalar"),
            source="test",
            source_params={},
            start_date=None,
            now=datetime(2026, 1, 2),
        )
    assert len(feed_store.read_table(feed_conn, "feed_x")) == 3
    assert feed_store.get_feed_meta(feed_conn, "feed_x")["row_count"] == 3


def test_last_date_and_last_value(feed_conn):
    feed_store.upsert_rows(
        feed_conn,
        "feed_y",
        "scalar",
        _rows(4, "scalar"),
        source="test",
        source_params={},
        start_date=None,
        now=datetime(2026, 1, 1),
    )
    assert feed_store.last_date(feed_conn, "feed_y") == date(2026, 1, 4)
    assert feed_store.last_value(feed_conn, "feed_y") == 8.0
    assert feed_store.last_date(feed_conn, "missing") is None
    assert feed_store.last_value(feed_conn, "missing") is None


def test_drop_feed_removes_table_and_metadata(feed_conn):
    feed_store.upsert_rows(
        feed_conn,
        "feed_z",
        "ohlcv",
        _rows(2, "ohlcv"),
        source="test",
        source_params={},
        start_date=None,
        now=datetime(2026, 1, 1),
    )
    feed_store.drop_feed(feed_conn, "feed_z")
    assert feed_store.get_feed_meta(feed_conn, "feed_z") is None
    with pytest.raises(sqlite3.OperationalError):
        feed_store.read_table(feed_conn, "feed_z")
    # Dropping an absent feed is a no-op.
    feed_store.drop_feed(feed_conn, "feed_z")


def test_feed_name_is_sanitized(feed_conn):
    for bad_name in ["Bad Name", "feed; DROP TABLE users", "a" * 64]:
        with pytest.raises(ValueError):
            feed_store.last_date(feed_conn, bad_name)
