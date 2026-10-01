"""Helpers for the monitor app: OIDC secrets materialization, metrics."""

from __future__ import annotations

import json
import secrets as secret_generator
import sqlite3
from pathlib import Path

import pandas as pd
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


def compute_performance(conn: sqlite3.Connection, start_ts: str | None = None) -> dict:
    """P&L, win rate, profit factor, and risk metrics from trades and snapshots.

    Trade-based metrics (win rate, profit factor, trade count) are computed over
    the full history — BUY/SELL pairing across a window edge is ambiguous.
    Equity-based metrics (P&L, drawdown, Sharpe, …) respect `start_ts`.
    """
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

    snapshots = _filter_snapshots(metadata.list_snapshots(conn), start_ts)
    equity = _equity_series(snapshots)
    total_pnl = float(equity.iloc[-1] - equity.iloc[0]) if len(equity) >= 2 else 0.0
    total_return_pct = float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) >= 2 and equity.iloc[0] > 0 else None
    returns = equity.pct_change().dropna()
    volatility = float(returns.std(ddof=1) * ANNUALIZATION_FACTOR) if len(returns) >= 2 else None
    sharpe_ratio = _sharpe_ratio(returns)
    max_drawdown_pct, max_drawdown_amount = _max_drawdown(equity)
    duration_days, drawdown_ongoing = _max_drawdown_duration(equity)
    drawdown = _drawdown_series(equity)

    return {
        "trade_count": len(filled),
        "win_rate": (len(wins) / len(per_trade_returns)) if per_trade_returns else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "volatility": volatility,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_amount": max_drawdown_amount,
        "max_drawdown_duration_days": duration_days,
        "max_drawdown_ongoing": drawdown_ongoing,
        "equity_curve": [(row["ts"], row["total"]) for row in snapshots],
        "drawdown_curve": [
            (row["ts"], float(value)) for row, value in zip(snapshots[1:], drawdown.iloc[1:], strict=False)
        ],
    }


# Snapshots are taken once per trading day (decision schedule), so equity
# returns are treated as daily and annualized over ~252 trading days.
ANNUALIZATION_FACTOR = 252**0.5


def _filter_snapshots(snapshots: list[sqlite3.Row], start_ts: str | None) -> list[sqlite3.Row]:
    """Keep snapshots at or after `start_ts` (ISO strings compare lexically)."""
    if start_ts is None:
        return snapshots
    return [row for row in snapshots if row["ts"] >= start_ts]


def _equity_series(snapshots: list[sqlite3.Row]) -> pd.Series:
    """Total account value as a float series indexed by parsed snapshot time."""
    index = pd.to_datetime([row["ts"] for row in snapshots])
    return pd.Series([float(row["total"]) for row in snapshots], index=index, dtype=float)


def _drawdown_series(equity: pd.Series) -> pd.Series:
    """Underwater curve: equity relative to the running peak (≤ 0, first point 0)."""
    return equity / equity.cummax() - 1


def _sharpe_ratio(returns: pd.Series) -> float | None:
    """Annualized Sharpe (risk-free = 0); None when undefined."""
    if len(returns) < 2:
        return None
    std = returns.std(ddof=1)
    if std == 0 or pd.isna(std):
        return None
    return float(returns.mean() / std * ANNUALIZATION_FACTOR)


def _max_drawdown(equity: pd.Series) -> tuple[float | None, float | None]:
    """Worst peak-to-trough decline as (fraction, absolute amount)."""
    if len(equity) < 2:
        return None, None
    drawdown = _drawdown_series(equity)
    trough = drawdown.idxmin()
    pct = float(drawdown.loc[trough])
    amount = float(equity.loc[trough] - equity.loc[:trough].max())
    return pct, amount


def _max_drawdown_duration(equity: pd.Series) -> tuple[int | None, bool]:
    """Longest peak-to-recovery span in calendar days, with an ongoing flag.

    A span opens when equity sets a running peak and closes when equity returns
    to that peak. If the window ends still underwater, the open span counts too
    and `ongoing` is True when that open span is the longest one.
    """
    if len(equity) < 2:
        return None, False
    best_days = 0
    best_is_open = False
    peak_value = float(equity.iloc[0])
    peak_ts = equity.index[0]
    underwater = False
    for ts, value in equity.items():
        if value >= peak_value:
            if underwater:
                # Recovery closes a real drawdown span (peak → recovery).
                best_days = max(best_days, (ts - peak_ts).days)
                underwater = False
                best_is_open = False
            peak_value = float(value)
            peak_ts = ts
        else:
            underwater = True
            best_is_open = True
    if best_is_open:
        # Window ends still underwater: the open span may be the longest one.
        best_days = max(best_days, (equity.index[-1] - peak_ts).days)
    return best_days, best_is_open


def positions_from_snapshot(row: sqlite3.Row | None) -> list[dict]:
    if row is None:
        return []
    return json.loads(row["positions_json"])
