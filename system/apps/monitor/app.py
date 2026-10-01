"""Monitor app: performance dashboard over metadata.db and feed.db.

Read-only on feed.db except `drop_feed` (table + `_metadata` row), per spec —
the monitor never updates feed data (that is the trader daemon's job).
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pandas as pd
import streamlit as st

from system.apps.monitor import support
from system.config.gateways import open_connections
from system.config.settings import Settings
from system.libs.db import feed_store, metadata
from system.libs.feeds.registry import feed_names

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Quant Trading Monitor", page_icon="📈", layout="wide")


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
        st.logout("Log out")
        st.stop()

    st.sidebar.write(f"👤 {email}")
    st.sidebar.logout()

    with open_connections(settings) as connections:
        overview_tab, feeds_tab, feed_detail_tab, trades_tab, settings_tab = st.tabs(
            ["Overview", "Data Feeds", "Feed Detail", "Trades", "Settings"]
        )
        render_overview(overview_tab, connections.metadata)
        render_data_feeds(feeds_tab, connections.feed)
        render_feed_detail(feed_detail_tab, connections.feed)
        render_trades(trades_tab, connections.metadata)
        render_settings(settings_tab, settings, connections.metadata)


# ── Overview ────────────────────────────────────────────────────────────────
def render_overview(tab, metadata_conn: sqlite3.Connection) -> None:
    with tab:
        st.header("Overview")
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
            st.line_chart(equity_frame.set_index("ts")["total"])
        else:
            st.write("No account snapshots yet.")

        st.subheader("Latest Trades")
        latest = metadata.list_trades(metadata_conn)[:10]
        if latest:
            st.dataframe(pd.DataFrame([dict(row) for row in latest]), use_container_width=True)
        else:
            st.write("No trades yet.")


# ── Data Feeds (read + drop only) ───────────────────────────────────────────
def render_data_feeds(tab, feed_conn: sqlite3.Connection) -> None:
    with tab:
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
        feed_frame["source_params_json"] = feed_frame["source_params_json"].apply(_compact_json)
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


def render_feed_detail(tab, feed_conn: sqlite3.Connection) -> None:
    with tab:
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
        st.write(
            f"Source: `{meta['source']}` · Rows: {meta['row_count']} · " f"Last updated: {meta['last_updated_at']}"
        )
        rows = feed_store.read_table(feed_conn, name)
        st.caption("Preview (latest 200 rows)")
        st.dataframe(pd.DataFrame([dict(row) for row in rows[-200:]]), use_container_width=True)
        if st.button("Delete this feed (drop table)"):
            feed_store.drop_feed(feed_conn, name)
            st.success(f"Dropped feed {name!r}")
            st.rerun()


# ── Trades ──────────────────────────────────────────────────────────────────
def render_trades(tab, metadata_conn: sqlite3.Connection) -> None:
    with tab:
        st.header("Trades")
        trades = metadata.list_trades(metadata_conn)
        if not trades:
            st.write("No trades recorded yet.")
            return
        st.dataframe(pd.DataFrame([dict(row) for row in trades]), use_container_width=True)
        decisions = [dict(row) for row in metadata.list_decisions(metadata_conn)]
        for row in trades:
            with st.expander(
                f"{row['ts']} · {row['side']} {row['quantity']} {row['symbol']} "
                f"@ {row['price']:,.0f} ({row['status']})"
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


def _nearest_decision(decisions: list[dict], ts: str) -> dict | None:
    candidates = [decision for decision in decisions if decision["ts"] <= ts]
    if not candidates:
        return None
    return max(candidates, key=lambda decision: decision["ts"])


# ── Settings ────────────────────────────────────────────────────────────────
def render_settings(tab, settings: Settings, metadata_conn: sqlite3.Connection) -> None:
    with tab:
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


def _compact_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), separators=(",", ":"))
    except (TypeError, ValueError):
        return raw


main()
