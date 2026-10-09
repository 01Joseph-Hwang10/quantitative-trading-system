"""Monitor app: performance dashboard over metadata.db and feed.db.

Read-only on feed.db except `drop_feed` (table + `_metadata` row), per spec —
the monitor never updates feed data (that is the trader daemon's job).
Pages are navigated vertically via the sidebar (st.navigation).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from system.apps.monitor import support
from system.config.gateways import Connections
from system.config.settings import Settings
from system.libs.db import feed_store, metadata
from system.libs.feeds.registry import feed_names

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Quant Trading Monitor", page_icon="📈", layout="wide")

# Text logo shown above the sidebar navigation (and the collapsed-sidebar header).
st.logo(str(Path(__file__).with_name("assets") / "logo.svg"))

# Performance page timespan selector: label → lookback in days ("YTD"/"All" special).
TIMESPAN_OPTIONS = ("1M", "3M", "6M", "YTD", "1Y", "All")
TIMESPAN_DAYS = {"1M": 30, "3M": 91, "6M": 183, "1Y": 365}


@st.cache_resource
def _monitor_connections(settings: Settings) -> Connections:
    """Process-lifetime db connections.

    Fragment reruns (auto-refresh) execute after the script run that opened the
    connections has finished, so connections must not be scoped to `main()`.
    Streamlit serializes script runs per session, so there is no concurrent
    access; `check_same_thread=False` covers the differing worker threads.
    """
    settings.ensure_data_dir()
    connections = Connections(
        metadata=metadata.connect(settings.metadata_db_path),
        feed=feed_store.connect(settings.feed_db_path),
    )
    metadata.seed_state(connections.metadata, {"trader_enabled": str(settings.trader_enabled).lower()})
    return connections


def main() -> None:
    settings = Settings()

    # ── Authentication (native Streamlit OIDC + allowlist gate) ─────────────
    if not settings.google_client_id or not settings.google_client_secret:
        st.error("Google OAuth is not configured (set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).")
        st.stop()

    support.ensure_auth_secrets(settings)

    if not st.user.is_logged_in:
        st.title("Quantitative Trading Monitor")
        st.button("Log in with Google", on_click=lambda: st.login("google"))
        st.stop()

    email = st.user.email
    if not support.is_authorized(email, settings):
        st.error(f"Unauthorized user: {email}. Ask an administrator to add you to AUTHORIZED_USERS.")
        st.button("Log out", on_click=st.logout)
        st.stop()

    connections = _monitor_connections(settings)
    st.sidebar.write(f"👤 {email}")
    st.sidebar.button("Log out", on_click=st.logout)
    st.sidebar.toggle("Auto-refresh (60s)", key="auto_refresh")
    _render_freshness(connections.metadata, connections.feed)

    pages = [
        st.Page(
            partial(render_overview, connections.metadata),
            title="Overview",
            icon="📊",
            url_path="overview",
            default=True,
        ),
        st.Page(
            partial(render_performance, connections.metadata),
            title="Performance",
            icon="📈",
            url_path="performance",
        ),
        st.Page(partial(render_data_feeds, connections.feed), title="Data Feeds", icon="🗂️", url_path="data-feeds"),
        st.Page(partial(render_feed_detail, connections.feed), title="Feed Detail", icon="🔎", url_path="feed-detail"),
        st.Page(partial(render_trades, connections.metadata), title="Trades", icon="🧾", url_path="trades"),
        st.Page(
            partial(render_settings, settings, connections.metadata),
            title="Settings",
            icon="⚙️",
            url_path="settings",
        ),
    ]
    st.navigation(pages).run()


# ── Sidebar ────────────────────────────────────────────────────────────────
def _render_freshness(metadata_conn: sqlite3.Connection, feed_conn: sqlite3.Connection) -> None:
    """Freshness badge: latest snapshot and feed update, to spot a stalled daemon."""
    st.sidebar.subheader("Freshness")
    snapshot = metadata.latest_snapshot(metadata_conn)
    st.sidebar.caption(f"Last snapshot: {snapshot['ts'] if snapshot else 'never'}")
    updates = [row["last_updated_at"] for row in feed_store.list_feeds(feed_conn) if row["last_updated_at"]]
    st.sidebar.caption(f"Last feed update: {max(updates) if updates else 'never'}")


# ── Overview ────────────────────────────────────────────────────────────────
def render_overview(metadata_conn: sqlite3.Connection) -> None:
    st.header("Overview")
    if st.session_state.get("auto_refresh"):
        _overview_fragment(metadata_conn)
    else:
        _overview_body(metadata_conn)


@st.fragment(run_every="60s")
def _overview_fragment(metadata_conn: sqlite3.Connection) -> None:
    _overview_body(metadata_conn)


def _overview_body(metadata_conn: sqlite3.Connection) -> None:
    snapshot = metadata.latest_snapshot(metadata_conn)
    performance = support.compute_performance(metadata_conn)

    col_balance, col_total, col_pnl, col_winrate = st.columns(4)
    col_balance.metric("Cash", f"₩{snapshot['cash']:,.0f}" if snapshot else "—")
    col_total.metric("Total Value", f"₩{snapshot['total']:,.0f}" if snapshot else "—")
    col_pnl.metric(
        "Total P&L",
        f"₩{performance['total_pnl']:,.0f}",
        delta=f"{performance['total_pnl']:+,.0f}" if snapshot else None,
    )
    col_winrate.metric(
        "Win Rate",
        f"{performance['win_rate'] * 100:.1f}%" if performance["win_rate"] is not None else "—",
    )

    st.subheader("Current Positions")
    positions = support.positions_from_snapshot(snapshot)
    if positions:
        st.dataframe(pd.DataFrame(positions), use_container_width=True)
    else:
        st.write("No open positions.")

    st.subheader("Performance Metrics")
    metric_profit_factor, metric_trades = st.columns(2)
    metric_profit_factor.metric(
        "Profit Factor",
        f"{performance['profit_factor']:.2f}" if performance["profit_factor"] is not None else "—",
    )
    metric_trades.metric("Trades", performance["trade_count"])

    st.subheader("Equity Curve")
    if performance["equity_curve"]:
        equity_frame = pd.DataFrame(performance["equity_curve"], columns=["ts", "total"])
        equity_frame["ts"] = pd.to_datetime(equity_frame["ts"])
        st.plotly_chart(_equity_figure(equity_frame.set_index("ts")["total"]), use_container_width=True)
    else:
        st.write("No account snapshots yet.")

    st.subheader("Latest Trades")
    latest = metadata.list_trades(metadata_conn)[:10]
    if latest:
        st.dataframe(pd.DataFrame([dict(row) for row in latest]), use_container_width=True)
    else:
        st.write("No trades yet.")


# ── Performance ─────────────────────────────────────────────────────────────
def render_performance(metadata_conn: sqlite3.Connection) -> None:
    st.header("Performance")
    if st.session_state.get("auto_refresh"):
        _performance_fragment(metadata_conn)
    else:
        _performance_body(metadata_conn)


@st.fragment(run_every="60s")
def _performance_fragment(metadata_conn: sqlite3.Connection) -> None:
    _performance_body(metadata_conn)


def _performance_body(metadata_conn: sqlite3.Connection) -> None:
    span = st.segmented_control("Timespan", TIMESPAN_OPTIONS, default="3M")
    performance = support.compute_performance(metadata_conn, _timespan_start_ts(span))

    equity_curve = performance["equity_curve"]
    total_value = equity_curve[-1][1] if equity_curve else None

    row_one = st.columns(5)
    row_one[0].metric("Total Value", f"₩{total_value:,.0f}" if total_value is not None else "—")
    row_one[1].metric("Total P&L", f"₩{performance['total_pnl']:,.0f}")
    row_one[2].metric("Total Return", _format_pct(performance["total_return_pct"]))
    row_one[3].metric("Win Rate", _format_pct(performance["win_rate"]))
    row_one[4].metric("Trades", performance["trade_count"])

    row_two = st.columns(4)
    row_two[0].metric("Sharpe Ratio", _format_ratio(performance["sharpe_ratio"]))
    row_two[1].metric("Volatility (ann.)", _format_pct(performance["volatility"]))
    row_two[2].metric("Profit Factor", _format_ratio(performance["profit_factor"]))
    row_two[3].metric("Max Drawdown", _format_pct(performance["max_drawdown_pct"]))

    duration = performance["max_drawdown_duration_days"]
    row_three = st.columns(2)
    row_three[0].metric(
        "Max Drawdown (₩)",
        f"₩{performance['max_drawdown_amount']:,.0f}" if performance["max_drawdown_amount"] is not None else "—",
    )
    row_three[1].metric(
        "Max Drawdown Duration",
        (
            f"{duration} days" + (" (ongoing)" if performance["max_drawdown_ongoing"] else "")
            if duration is not None
            else "—"
        ),
    )

    st.caption(
        "Win rate, profit factor, and trade count are computed over the full trade "
        "history (BUY/SELL pairing across a timespan edge is ambiguous); all other "
        "metrics respect the selected timespan."
    )

    st.subheader("Equity Curve")
    if equity_curve:
        equity_frame = pd.DataFrame(equity_curve, columns=["ts", "total"])
        equity_frame["ts"] = pd.to_datetime(equity_frame["ts"])
        st.plotly_chart(_equity_figure(equity_frame.set_index("ts")["total"]), use_container_width=True)
    else:
        st.write("No account snapshots in this timespan.")

    st.subheader("Drawdown")
    if performance["drawdown_curve"]:
        drawdown_frame = pd.DataFrame(performance["drawdown_curve"], columns=["ts", "drawdown"])
        drawdown_frame["ts"] = pd.to_datetime(drawdown_frame["ts"])
        st.plotly_chart(_drawdown_figure(drawdown_frame.set_index("ts")["drawdown"]), use_container_width=True)
    else:
        st.write("Not enough snapshots to compute drawdown.")

    st.subheader("Latest Trades")
    latest = metadata.list_trades(metadata_conn)[:10]
    if latest:
        st.dataframe(pd.DataFrame([dict(row) for row in latest]), use_container_width=True)
    else:
        st.write("No trades yet.")


def _timespan_start_ts(span: str | None) -> str | None:
    """ISO start timestamp for a timespan label (None for All)."""
    if span in (None, "All"):
        return None
    now = datetime.now()
    if span == "YTD":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    return (now - timedelta(days=TIMESPAN_DAYS[span])).isoformat()


def _format_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def _format_ratio(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


# ── Data Feeds (read + drop only) ───────────────────────────────────────────
def render_data_feeds(feed_conn: sqlite3.Connection) -> None:
    st.header("Data Feeds")
    st.caption(
        "Feeds are updated by the trader daemon. The monitor can only read "
        "feeds or delete (drop) an entire feed table."
    )
    feeds = feed_store.list_feeds(feed_conn)
    if not feeds:
        st.write("No data feeds stored yet.")
        return
    feed_frame = pd.DataFrame([dict(row) for row in feeds])
    feed_frame["source_params_json"] = feed_frame["source_params_json"].apply(support.compact_json)
    st.dataframe(feed_frame, use_container_width=True)

    st.subheader("Delete a feed (drop table)")
    with st.form("drop_feed_form"):
        name = st.selectbox("Feed", [row["name"] for row in feeds])
        confirmed = st.checkbox("I understand this permanently drops the feed table")
        submitted_delete = st.form_submit_button("Delete feed")
    if submitted_delete:
        if confirmed:
            feed_store.drop_feed(feed_conn, name)
            st.success(f"Dropped feed {name!r}")
            st.rerun()
        else:
            st.warning("Tick the confirmation checkbox to delete.")


def render_feed_detail(feed_conn: sqlite3.Connection) -> None:
    st.header("Feed Detail")
    stored = [row["name"] for row in feed_store.list_feeds(feed_conn)]
    candidates = sorted(set(stored) | set(feed_names()))
    if not candidates:
        st.write("No feeds available.")
        return
    name = st.selectbox("Feed", candidates)
    meta = feed_store.get_feed_meta(feed_conn, name)
    if meta is None:
        st.warning(f"{name!r} has never been fetched (no rows stored).")
        return
    st.write(f"Source: `{meta['source']}` · Rows: {meta['row_count']} · " f"Last updated: {meta['last_updated_at']}")

    rows = feed_store.read_table(feed_conn, name)
    frame = pd.DataFrame([dict(row) for row in rows])
    frame["date"] = pd.to_datetime(frame["date"])

    st.subheader("Chart")
    lookback_days = st.selectbox(
        "Chart window",
        [30, 90, 180, 365, None],
        index=1,
        format_func=lambda days: "All" if days is None else f"{days} days",
    )
    if lookback_days is not None:
        frame = frame[frame["date"] >= pd.Timestamp.now() - pd.Timedelta(days=lookback_days)]
    if frame.empty:
        st.write("No rows in the selected window.")
    elif "close" in frame.columns:
        st.plotly_chart(_ohlcv_figure(frame, name), use_container_width=True)
    else:
        st.plotly_chart(_scalar_figure(frame, name), use_container_width=True)

    st.caption("Preview (latest 200 rows)")
    st.dataframe(pd.DataFrame([dict(row) for row in rows[-200:]]), use_container_width=True)
    if st.button("Delete this feed (drop table)"):
        feed_store.drop_feed(feed_conn, name)
        st.success(f"Dropped feed {name!r}")
        st.rerun()


def _padded_range(values: pd.Series, padding: float = 0.05) -> list[float]:
    """Tight y-axis range around the data so small variations stay visible."""
    low, high = float(values.min()), float(values.max())
    if low == high:
        margin = abs(low) * 0.01 or 1.0
    else:
        margin = (high - low) * padding
    return [low - margin, high + margin]


def _equity_figure(equity: pd.Series) -> go.Figure:
    """Equity curve with the y-axis zoomed to the data range (never zero-based)."""
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=equity.index, y=equity.values, mode="lines", name="Total value", line=dict(width=2)))
    figure.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    figure.update_yaxes(range=_padded_range(equity), tickformat=",.0f")
    return figure


def _drawdown_figure(drawdown: pd.Series) -> go.Figure:
    """Underwater chart as a filled percentage area, zoomed to the data range."""
    pct = drawdown * 100
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=pct.index,
            y=pct.values,
            mode="lines",
            fill="tozeroy",
            name="Drawdown",
            line=dict(width=1.5, color="rgb(255,82,82)"),
            fillcolor="rgba(255,82,82,0.25)",
        )
    )
    figure.update_layout(height=240, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
    low = float(pct.min())
    figure.update_yaxes(range=[low * 1.1 if low < 0 else -1, 0], ticksuffix="%")
    return figure


def _scalar_figure(frame: pd.DataFrame, name: str) -> go.Figure:
    """Line chart for a scalar feed with the y-axis zoomed to the data range."""
    values = frame.set_index("date")["value"]
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=values.index, y=values.values, mode="lines", name=name, line=dict(width=2)))
    figure.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    figure.update_yaxes(range=_padded_range(values))
    return figure


def _ohlcv_figure(frame: pd.DataFrame, name: str) -> go.Figure:
    """Candlestick with a volume subplot for an OHLCV feed."""
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.03,
    )
    figure.add_trace(
        go.Candlestick(
            x=frame["date"],
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            name=name,
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=frame["date"], y=frame["volume"], name="Volume", marker_color="rgba(100,116,160,0.5)", showlegend=False
        ),
        row=2,
        col=1,
    )
    figure.update_layout(
        height=520,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    figure.update_yaxes(title_text="Price", row=1, col=1)
    figure.update_yaxes(title_text="Volume", row=2, col=1)
    return figure


# ── Trades ──────────────────────────────────────────────────────────────────
def render_trades(metadata_conn: sqlite3.Connection) -> None:
    st.header("Trades")
    trades = metadata.list_trades(metadata_conn)
    decisions = [dict(row) for row in metadata.list_decisions(metadata_conn)]

    tab_trades, tab_decisions = st.tabs(["Trades", "Decisions"])

    with tab_trades:
        if not trades:
            st.write("No trades recorded yet.")
        else:
            st.dataframe(pd.DataFrame([dict(row) for row in trades]), use_container_width=True)
            for row in trades:
                with st.expander(
                    f"{row['ts']} · {row['side']} {row['quantity']} {row['symbol']} " f"@ {row['price']:,.0f} ({row['status']})"
                ):
                    st.json(
                        {
                            "id": row["id"],
                            "order_id": row["order_id"],
                            "strategy": row["strategy"],
                            "note": row["note"],
                            "nearest_decision": _nearest_decision(decisions, row["ts"]),
                        }
                    )

    with tab_decisions:
        decisions_frame = support.decisions_frame(decisions)
        if decisions_frame.empty:
            st.write("No decisions recorded yet.")
        else:
            st.dataframe(decisions_frame, use_container_width=True)


def _nearest_decision(decisions: list[dict], ts: str) -> dict | None:
    candidates = [decision for decision in decisions if decision["ts"] <= ts]
    if not candidates:
        return None
    return max(candidates, key=lambda decision: decision["ts"])


# ── Settings ────────────────────────────────────────────────────────────────
def render_settings(settings: Settings, metadata_conn: sqlite3.Connection) -> None:
    st.header("Settings")
    enabled = metadata.get_state(metadata_conn, "trader_enabled", "true") == "true"
    new_enabled = st.toggle("Trader enabled", value=enabled)
    if new_enabled != enabled:
        metadata.set_state(metadata_conn, "trader_enabled", str(new_enabled).lower())
        st.success(f"Trader {'enabled' if new_enabled else 'disabled'}")
        st.rerun()

    decisions = metadata.list_decisions(metadata_conn)
    st.write(f"Broker mode: `{settings.broker}`")
    st.write(f"Last trader activity: {decisions[0]['ts'] if decisions else 'never'}")
    st.caption(
        f"Decision schedule: `{settings.decision_schedule_cron}` · "
        f"Feed update schedule: `{settings.feed_update_schedule_cron}` ({settings.timezone})"
    )


main()
