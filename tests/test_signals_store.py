"""signals_store: upserts, reads, metadata, and drop semantics."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from system.libs.db import signals_store


def _rows(count: int) -> list[dict]:
    return [
        {
            "date": (date(2026, 1, 1) + timedelta(days=offset)).isoformat(),
            "value": 0.5 + offset * 0.1,
        }
        for offset in range(count)
    ]


def test_upsert_and_read_roundtrip(signals_conn):
    signals_store.upsert_rows(
        signals_conn,
        "d_t_10",
        _rows(5),
        source="macro",
        source_params={"n_macro": 10},
        start_date=date(2026, 1, 1),
        now=datetime(2026, 1, 1),
    )
    rows = signals_store.read_table(signals_conn, "d_t_10")
    assert len(rows) == 5
    meta = signals_store.get_signal_meta(signals_conn, "d_t_10")
    assert meta["row_count"] == 5
    assert meta["source"] == "macro"
    assert meta["start_date"] == "2026-01-01"


def test_upsert_is_idempotent(signals_conn):
    for _ in range(2):
        signals_store.upsert_rows(
            signals_conn,
            "adx_14",
            _rows(3),
            source="technical",
            source_params={},
            start_date=None,
            now=datetime(2026, 1, 2),
        )
    assert len(signals_store.read_table(signals_conn, "adx_14")) == 3
    assert signals_store.get_signal_meta(signals_conn, "adx_14")["row_count"] == 3


def test_last_date(signals_conn):
    signals_store.upsert_rows(
        signals_conn,
        "t_t",
        _rows(4),
        source="technical",
        source_params={},
        start_date=None,
        now=datetime(2026, 1, 1),
    )
    assert signals_store.last_date(signals_conn, "t_t") == date(2026, 1, 4)
    assert signals_store.last_date(signals_conn, "missing") is None


def test_drop_signal_removes_table_and_metadata(signals_conn):
    signals_store.upsert_rows(
        signals_conn,
        "s_fx",
        _rows(2),
        source="macro",
        source_params={},
        start_date=None,
        now=datetime(2026, 1, 1),
    )
    signals_store.drop_signal(signals_conn, "s_fx")
    assert signals_store.get_signal_meta(signals_conn, "s_fx") is None
    with pytest.raises(sqlite3.OperationalError):
        signals_store.read_table(signals_conn, "s_fx")
    # Dropping an absent signal is a no-op.
    signals_store.drop_signal(signals_conn, "s_fx")


def test_signal_name_is_sanitized(signals_conn):
    for bad_name in ["Bad Name", "signal; DROP TABLE users", "a" * 64]:
        with pytest.raises(ValueError):
            signals_store.last_date(signals_conn, bad_name)
