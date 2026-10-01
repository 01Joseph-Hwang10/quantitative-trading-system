"""Data feed storage (feed.db): `_metadata` table plus one table per feed.

Only `drop_feed` deletes anything, and it always removes the feed table *and*
its `_metadata` row together. The monitor may only read feeds and call
`drop_feed`; updates are owned by the trader daemon.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

FEED_KIND = Literal["ohlcv", "scalar"]

# Feed table names are used in SQL identifiers — restrict them hard.
FEED_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

OHLCV_COLUMNS = "(date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
SCALAR_COLUMNS = "(date TEXT PRIMARY KEY, value REAL)"


def connect(feed_db_path: Path) -> sqlite3.Connection:
    """Open (and initialize) the feed database."""
    feed_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(feed_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _metadata ("
        " name TEXT PRIMARY KEY,"
        " source TEXT NOT NULL,"
        " source_params_json TEXT NOT NULL,"
        " start_date TEXT,"
        " last_updated_at TEXT,"
        " row_count INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    return conn


def _validate_name(name: str) -> str:
    if not FEED_NAME_PATTERN.match(name) or len(name) > 63:
        raise ValueError(f"Invalid feed table name: {name!r}")
    return name


def _ensure_table(conn: sqlite3.Connection, name: str, kind: FEED_KIND) -> None:
    columns = OHLCV_COLUMNS if kind == "ohlcv" else SCALAR_COLUMNS
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} {columns}")


def get_feed_meta(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM _metadata WHERE name = ?", (name,)).fetchone()


def list_feeds(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM _metadata ORDER BY name"))


def read_table(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Read every row of a feed table (raises if the feed does not exist)."""
    _validate_name(name)
    return list(conn.execute(f"SELECT * FROM {name} ORDER BY date"))


def last_date(conn: sqlite3.Connection, name: str) -> date | None:
    """Latest date stored in the feed table, or None for an absent/empty feed."""
    _validate_name(name)
    if get_feed_meta(conn, name) is None:
        return None
    row = conn.execute(f"SELECT MAX(date) AS max_date FROM {name}").fetchone()
    if row is None or row["max_date"] is None:
        return None
    return date.fromisoformat(str(row["max_date"])[:10])


def last_value(conn: sqlite3.Connection, name: str) -> float | None:
    """Most recent close (ohlcv) or value (scalar), or None if unavailable."""
    _validate_name(name)
    if get_feed_meta(conn, name) is None:
        return None
    kind = _table_kind(conn, name)
    column = "close" if kind == "ohlcv" else "value"
    row = conn.execute(f"SELECT {column} FROM {name} ORDER BY date DESC LIMIT 1").fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _table_kind(conn: sqlite3.Connection, name: str) -> str:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
    return "ohlcv" if "close" in columns else "scalar"


def upsert_rows(
    conn: sqlite3.Connection,
    name: str,
    kind: FEED_KIND,
    rows: list[dict[str, Any]],
    *,
    source: str,
    source_params: dict[str, Any],
    start_date: date | None,
    now: datetime,
) -> None:
    """Insert or replace feed rows and refresh `_metadata` in one transaction.

    `rows` items are dicts; for ohlcv: date/open/high/low/close/volume, for
    scalar: date/value. `date` values must be ISO strings or `datetime.date`.
    """
    _validate_name(name)
    if kind not in ("ohlcv", "scalar"):
        raise ValueError(f"Invalid feed kind: {kind!r}")
    _ensure_table(conn, name, kind)
    placeholders = "(:date, :open, :high, :low, :close, :volume)" if kind == "ohlcv" else "(:date, :value)"
    insert_sql = f"INSERT OR REPLACE INTO {name} VALUES {placeholders}"
    normalized_rows = []
    for row in rows:
        normalized = dict(row)
        normalized["date"] = str(normalized["date"])[:10]
        normalized_rows.append(normalized)
    with conn:
        conn.executemany(insert_sql, normalized_rows)
        row_count = int(conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"])
        conn.execute(
            "INSERT INTO _metadata (name, source, source_params_json, start_date, last_updated_at, row_count)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            " source = excluded.source,"
            " source_params_json = excluded.source_params_json,"
            " start_date = excluded.start_date,"
            " last_updated_at = excluded.last_updated_at,"
            " row_count = excluded.row_count",
            (
                name,
                source,
                json.dumps(source_params),
                start_date.isoformat() if start_date else None,
                now.isoformat(),
                row_count,
            ),
        )


def drop_feed(conn: sqlite3.Connection, name: str) -> None:
    """Drop the feed table and its `_metadata` row (the only delete path)."""
    _validate_name(name)
    with conn:
        conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("DELETE FROM _metadata WHERE name = ?", (name,))
