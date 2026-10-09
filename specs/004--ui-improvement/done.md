# Done — Monitor UI improvement

Implementation record for `plan.md` (requirements from the owner's
`draft.md` — not repeated here). Shipped in `6c1c126` (2026-10-01); graph
fix-up in `d853ff8`; sidebar logo follow-ups in `4752c62` and `cb1b176`.

## Surface

| File | Change |
|---|---|
| `pyproject.toml` / `uv.lock` | Added `plotly>=6` (owner-approved dependency). |
| `system/apps/monitor/app.py` | Replaced top-level `st.tabs` with vertical sidebar navigation via `st.navigation` + `st.Page` (Overview, Performance, Data Feeds, Feed Detail, Trades, Settings); auth gate/email/logout retained above navigation. |
| `system/apps/monitor/app.py` | Feed Detail: lookback control (30/90/180/365/All) + plotly candlestick + volume subplot for `ohlcv` feeds, line chart for `scalar` feeds. |
| `system/apps/monitor/app.py` | New **Performance** page: `st.segmented_control` timespan (1M/3M/6M/YTD/1Y/All, session-state persisted); metric rows — Total Value, Total P&L, Total Return %, Win Rate, Trade Count, Sharpe Ratio, Annualized Volatility, Profit Factor, Max Drawdown (% and ₩), Max Drawdown Duration ("ongoing" when still underwater); equity curve + underwater (drawdown %) charts; latest trades. |
| `system/apps/monitor/app.py` | Auto-refresh toggle in the sidebar — Overview and Performance render inside `st.fragment(run_every="60s")`; plotly equity figure shared by both pages. |
| `system/apps/monitor/support.py` | `compute_performance(conn, start_ts=None)` extended with `total_return_pct`, `volatility`, `sharpe_ratio`, `max_drawdown_pct`/`_amount`/`_duration_days`/`_ongoing`, `drawdown_curve`; helpers for equity/drawdown series and peak-span duration; Sharpe is `None` under 3 snapshots or zero-variance equity. |
| `tests/test_monitor.py` | New metric tests: synthetic snapshot path (100→120→90→110→130) asserting drawdown %/₩/duration, finite Sharpe, `start_ts` filtering; `None`-Sharpe edge cases; `max_drawdown_pct == 0` for monotonic-up history. |
| `system/apps/monitor/assets/logo.svg` + sidebar | Text/SVG logo above the navigation (`4752c62`, `cb1b176`), placed above nav after a first-pass fix. |

## Known limitations (per plan)

- Win rate / profit factor are **not** timespan-filtered (trade pairing
  across a window edge is ambiguous) — noted in the page caption.
- Deferred suggestions (trades filters, position breakdown, benchmark
  overlay, signal color coding) were not implemented.

## Verification status

`pytest` green including the new metric tests; manual smoke test with
`BROKER=mock` verified navigation, both chart types, and timespan edge
cases. A plotting regression found right after merge was fixed in
`d853ff8`.
