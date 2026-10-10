"""Signal storage (signals.db): `_metadata` table plus one table per signal.

A signal is a derived daily scalar series computed from the stored data
feeds — unlike feed.db there is no upstream fetch. Only `drop_signal`
deletes anything, and it always removes the signal table *and* its
`_metadata` row together. The monitor may only read signals and call
`drop_signal`; updates are owned by the trader daemon.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any

# Signal table names are used in SQL identifiers — restrict them hard.
SIGNAL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

SIGNAL_COLUMNS = "(date TEXT PRIMARY KEY, value REAL)"


def connect(signals_db_path) -> sqlite3.Connection:
    """Open (and initialize) the signal database.

    `check_same_thread=False` lets Streamlit fragments (auto-refresh reruns in a
    worker thread) reuse connections opened on a previous script-run thread.
    """
    signals_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(signals_db_path, check_same_thread=False)
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
    if not SIGNAL_NAME_PATTERN.match(name) or len(name) > 63:
        raise ValueError(f"Invalid signal table name: {name!r}")
    return name


def get_signal_meta(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM _metadata WHERE name = ?", (name,)).fetchone()


def list_signals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM _metadata ORDER BY name"))


def read_table(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Read every row of a signal table (raises if the signal does not exist)."""
    _validate_name(name)
    return list(conn.execute(f"SELECT * FROM {name} ORDER BY date"))


def last_date(conn: sqlite3.Connection, name: str) -> date | None:
    """Latest date stored in the signal table, or None for an absent/empty signal."""
    _validate_name(name)
    if get_signal_meta(conn, name) is None:
        return None
    row = conn.execute(f"SELECT MAX(date) AS max_date FROM {name}").fetchone()
    if row is None or row["max_date"] is None:
        return None
    return date.fromisoformat(str(row["max_date"])[:10])


def upsert_rows(
    conn: sqlite3.Connection,
    name: str,
    rows: list[dict[str, Any]],
    *,
    source: str,
    source_params: dict[str, Any],
    start_date: date | None,
    now: datetime,
) -> None:
    """Insert or replace signal rows and refresh `_metadata` in one transaction.

    `rows` items are dicts with date/value keys; `date` values must be ISO
    strings or `datetime.date`. Rows already filtered to finite values by
    `Signals.update` (indicator warm-up heads are never stored).
    """
    _validate_name(name)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {name} {SIGNAL_COLUMNS}")
    insert_sql = f"INSERT OR REPLACE INTO {name} VALUES (:date, :value)"
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


def drop_signal(conn: sqlite3.Connection, name: str) -> None:
    """Drop the signal table and its `_metadata` row (the only delete path)."""
    _validate_name(name)
    with conn:
        conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("DELETE FROM _metadata WHERE name = ?", (name,))
