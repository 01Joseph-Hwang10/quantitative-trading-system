"""Trade history, decisions, account snapshots, and app state (metadata.db)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  price REAL NOT NULL,
  order_id TEXT,
  status TEXT NOT NULL,
  strategy TEXT NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  signal TEXT NOT NULL,
  reason TEXT NOT NULL,
  indicators_json TEXT NOT NULL,
  executed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS account_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  cash REAL NOT NULL,
  market_value REAL NOT NULL,
  total REAL NOT NULL,
  positions_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect(metadata_db_path: Path) -> sqlite3.Connection:
    """Open (and initialize) the metadata database.

    `check_same_thread=False` lets Streamlit fragments (auto-refresh reruns in a
    worker thread) reuse connections opened on the main script thread.
    """
    metadata_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(metadata_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def record_trade(
    conn: sqlite3.Connection,
    *,
    ts: str,
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    order_id: str | None,
    status: str,
    strategy: str,
    note: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO trades (ts, symbol, side, quantity, price, order_id, status, strategy, note)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, symbol, side, quantity, price, order_id, status, strategy, note),
    )
    conn.commit()
    return int(cursor.lastrowid)


def record_decision(
    conn: sqlite3.Connection,
    *,
    ts: str,
    symbol: str,
    signal: str,
    reason: str,
    indicators: dict[str, Any],
    executed: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO decisions (ts, symbol, signal, reason, indicators_json, executed)" " VALUES (?, ?, ?, ?, ?, ?)",
        (ts, symbol, signal, reason, json.dumps(indicators, default=str), int(executed)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def record_snapshot(
    conn: sqlite3.Connection,
    *,
    ts: str,
    cash: float,
    market_value: float,
    total: float,
    positions: list[dict[str, Any]],
) -> int:
    cursor = conn.execute(
        "INSERT INTO account_snapshots (ts, cash, market_value, total, positions_json)" " VALUES (?, ?, ?, ?, ?)",
        (ts, cash, market_value, total, json.dumps(positions, default=str)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM trades ORDER BY ts DESC, id DESC"))


def list_decisions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM decisions ORDER BY ts DESC, id DESC"))


def list_snapshots(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM account_snapshots ORDER BY ts ASC, id ASC"))


def latest_snapshot(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM account_snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
    return row


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?)" " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def seed_state(conn: sqlite3.Connection, initial_state: dict[str, str]) -> None:
    """Insert defaults for keys that are not present yet."""
    for key, value in initial_state.items():
        if get_state(conn, key) is None:
            set_state(conn, key, value)
