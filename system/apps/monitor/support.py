"""Helpers for the monitor app: OIDC secrets materialization, metrics."""

from __future__ import annotations

import json
import secrets as secret_generator
import sqlite3
from pathlib import Path

import toml

from system.config.settings import Settings
from system.libs.db import metadata

# Google OIDC discovery document (Streamlit requires server_metadata_url).
GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def ensure_auth_secrets(settings: Settings) -> Path:
    """Materialize `.streamlit/secrets.toml` [auth] from environment settings.

    Streamlit's native OIDC reads its provider config from secrets.toml; this
    keeps actual configuration in env vars and the generated file gitignored.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required for monitor authentication")
    secrets_path = Path(".streamlit/secrets.toml")
    existing: dict = {}
    if secrets_path.exists():
        existing = toml.loads(secrets_path.read_text())
    # Shared OIDC settings live in [auth]; the named provider "google" (used by
    # st.login("google")) requires its own [auth.google] section.
    auth_section = existing.setdefault("auth", {})
    auth_section.setdefault("redirect_uri", settings.google_redirect_uri)
    auth_section.setdefault("cookie_secret", _cookie_secret(settings))
    auth_section.setdefault(
        "google",
        {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "server_metadata_url": GOOGLE_METADATA_URL,
            "client_kwargs": {"scope": "openid profile email"},
        },
    )
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(toml.dumps(existing))
    return secrets_path


def _cookie_secret(settings: Settings) -> str:
    """Stable random cookie secret, persisted under the data dir."""
    path = settings.data_dir / "cookie_secret"
    if path.exists():
        return path.read_text().strip()
    value = secret_generator.token_hex(32)
    settings.ensure_data_dir()
    path.write_text(value)
    return value


def is_authorized(email: str | None, settings: Settings) -> bool:
    if email is None:
        return False
    return email in settings.authorized_user_list


def compute_performance(conn: sqlite3.Connection) -> dict:
    """P&L, win rate, and profit factor from logged trades and snapshots."""
    trades = metadata.list_trades(conn)
    filled = [row for row in trades if row["status"] == "FILLED"]
    sells = [row for row in filled if row["side"] == "SELL"]
    buys = [row for row in filled if row["side"] == "BUY"]

    # Pair each SELL with the most recent BUY before it (FIFO-ish, single symbol).
    buy_prices = [row["price"] for row in reversed(buys)]
    per_trade_returns = []
    for sell in sells:
        if buy_prices:
            entry = buy_prices.pop()
            per_trade_returns.append(sell["price"] / entry - 1 if entry > 0 else 0.0)

    wins = [value for value in per_trade_returns if value > 0]
    losses = [value for value in per_trade_returns if value <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    snapshots = metadata.list_snapshots(conn)
    equity = [row["total"] for row in snapshots]
    total_pnl = equity[-1] - equity[0] if len(equity) >= 2 else 0.0

    return {
        "trade_count": len(filled),
        "win_rate": (len(wins) / len(per_trade_returns)) if per_trade_returns else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "total_pnl": total_pnl,
        "equity_curve": [(row["ts"], row["total"]) for row in snapshots],
    }


def positions_from_snapshot(row: sqlite3.Row | None) -> list[dict]:
    if row is None:
        return []
    return json.loads(row["positions_json"])
