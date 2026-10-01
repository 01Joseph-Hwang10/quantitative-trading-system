# Implementation Plan — Monitor UI Improvement

Source: `specs/004--ui-improvement/draft.md`
Scope: `system/apps/monitor/` (+ tests, + one dependency). No infra/deployment changes.

## 1. Requirements (from draft)

1. **Vertical tabs**: page navigation as a left-sidebar menu, not horizontal
   `st.tabs` at the top.
2. **Feed Detail chart**: visualize the selected feed's data.
3. **Performance page** (new):
   - Extended version of the Overview metrics.
   - Must include **Sharpe Ratio**, **Max Drawdown**, and **Max Drawdown Duration**.
   - Must include a **timespan selector** (view metrics over different periods).

## 2. Design Decisions

1. **Navigation**: use Streamlit's native multipage API
   (`st.navigation` + `st.Page`, available since 1.37; we pin `>=1.64`).
   Each `render_*` function becomes a page callback (drop the `tab` param and
   the `with tab:` wrapper). Sidebar order: navigation (auto) → user email →
   Log out button. Single `app.py` is kept — the app is small; no `pages/` dir.
2. **Feed chart rendering**: add **`plotly`** as a dependency
   (`plotly>=6`, pure wheel, modest image-size cost). Feed kind is inferred
   from the table columns (`close` present → `ohlcv`, else `scalar`):
   - `ohlcv` → candlestick chart + volume bars (two-row subplot).
   - `scalar` → line chart of `value`.
   - Feeds that were never fetched keep the current "never fetched" warning.
   - A lookback window control (30/90/180/365 days/All) applies to the chart.
3. **Timespan selector**: `st.segmented_control` with `1M / 3M / 6M / YTD / 1Y / All`.
   It filters the snapshot series (`ts >= start`) and drives every metric and
   chart on the Performance page. Timespan is stored in `st.session_state` via
   the widget key so it survives reruns.
4. **Metric definitions** (computed from `account_snapshots`, risk-free = 0):
   - Daily returns `r_t = total_t / total_{t-1} - 1`.
   - **Sharpe Ratio** = `mean(r) / std(r, ddof=1) * sqrt(252)`; `None` when
     fewer than 3 snapshots or `std == 0`.
   - **Max Drawdown** = `min(total / cummax(total) - 1)`; reported as both a
     percentage and an absolute ₩ loss from the peak.
   - **Max Drawdown Duration** = longest span (calendar days) between a new
     equity peak and recovery back to that peak; if still underwater at the
     end of the window, the duration is measured to the last snapshot and
     labeled "ongoing".
   - Extra extended metrics: Total Return %, Annualized Volatility, plus the
     existing Profit Factor / Win Rate / Trade Count / Total P&L.
5. **Data access**: no new SQL needed — `metadata.list_snapshots` already
   returns the full ascending series; filtering happens in pandas.
   `compute_performance(conn, start_ts=None)` gains a `start_ts` filter so
   Overview (All) and Performance share one code path.
6. **Safety**: read-only behavior unchanged; `drop_feed` flows untouched.
7. **Resolved by owner**: plotly dependency **approved**; additional
   suggestions (1) auto-refresh toggle and (5) sidebar freshness badge are
   **in scope** for this round.

## 3. File Changes

```
pyproject.toml                      # add plotly>=6 to dependencies
system/apps/monitor/app.py          # nav refactor + Feed Detail chart + Performance page
system/apps/monitor/support.py      # extend compute_performance + drawdown/duration helpers
tests/test_monitor.py               # tests for new metrics & timespan filtering
```

### `system/apps/monitor/app.py`

1. `main()`:
   ```python
   pages = [
       st.Page(render_overview,     title="Overview",     icon="📊", default=True),
       st.Page(render_performance,  title="Performance",  icon="📈"),
       st.Page(render_data_feeds,   title="Data Feeds",   icon="🗂️"),
       st.Page(render_feed_detail,  title="Feed Detail",  icon="🔎"),
       st.Page(render_trades,       title="Trades",       icon="🧾"),
       st.Page(render_settings,     title="Settings",     icon="⚙️"),
   ]
   st.navigation(pages).run()
   ```
   - Page callbacks drop the `tab` argument; `with tab:` blocks become plain bodies.
   - Auth gate, user email, and logout stay exactly as-is (above `st.navigation`).

2. `render_feed_detail(feed_conn)`:
   - Keep feed selectbox, metadata caption, and drop button.
   - After the preview table: lookback control (`30/90/180/365/All` days),
     then plot:
     - ohlcv → `plotly.graph_objects` candlestick + volume row,
       x-axis limited to the window.
     - scalar → `st.line_chart` of `value` over the window (no plotly needed).
   - Charts render from the already-read `rows` (re-reads avoided).

3. `render_performance(metadata_conn)` (new):
   - `st.segmented_control("Timespan", ["1M","3M","6M","YTD","1Y","All"], default="3M")`.
   - Row 1 (5 columns): Total Value, Total P&L, Total Return %, Win Rate, Trade Count.
   - Row 2 (4 columns): Sharpe Ratio, Annualized Volatility, Profit Factor, Max Drawdown.
   - Row 3 (2 columns): Max Drawdown (₩), Max Drawdown Duration (days; "… (ongoing)").
   - Equity curve chart for the window (`st.area_chart`).
   - Underwater (drawdown %) chart for the window, below the equity curve.
   - Latest trades table (same as Overview's, up to 10).

4. `render_overview` unchanged except: uses `compute_performance(conn)` (All)
   and drops its tab wrapper.

### `system/libs/db` — none (no schema change)

### `system/apps/monitor/support.py`

1. Refactor `compute_performance(conn, start_ts: str | None = None) -> dict`,
   existing keys unchanged, new keys added:
   - `total_return_pct`, `volatility`, `sharpe_ratio`,
     `max_drawdown_pct`, `max_drawdown_amount`, `max_drawdown_duration_days`,
     `max_drawdown_ongoing`, `drawdown_curve`.
2. New private helpers (module-level functions, per project style):
   - `_filter_snapshots(snapshots, start_ts) -> list[sqlite3.Row]`
   - `_equity_series(snapshots) -> pd.DataFrame` (indexed by ts)
   - `_drawdown_series(equity: pd.Series) -> pd.Series` (`equity / cummax - 1`)
   - `_max_drawdown(equity: pd.Series) -> tuple[float, float]` (pct, amount)
   - `_max_drawdown_duration(ts: pd.Series, equity: pd.Series) -> tuple[int, bool]`
     (days, ongoing flag) — scan peaks with a monotonic running max; when a new
     peak is set, close the previous underwater span.
3. Buy/SELL pairing logic (win rate / profit factor) stays as-is; it is not
   timespan-filtered in v1 (trade pairing across a window edge is ambiguous);
   noted as a known limitation in the page caption.

### `tests/test_monitor.py`

1. Extend the existing seeded-history test to assert the new keys exist and
   that `max_drawdown_pct == 0.0` for the monotonic-up seeded scenario.
2. New test: insert synthetic snapshots via `metadata.record_snapshot`
   (e.g., 100 → 120 → 90 → 110 → 130) and assert:
   - `max_drawdown_pct == pytest.approx(90 / 120 - 1)`,
   - `max_drawdown_amount == pytest.approx(30.0)`,
   - drawdown duration spans the 90→130 recovery,
   - `sharpe_ratio` is a finite float,
   - `start_ts` filtering shrinks the series and recomputes metrics.
3. New test: fewer than 3 snapshots → `sharpe_ratio is None`, flat equity
   (`std == 0`) → `sharpe_ratio is None`.

## 4. Implementation Order

1. Add `plotly` dependency (`uv add plotly`), sync lockfile.
2. `support.py`: metric refactor + helpers + tests (pure logic, no UI).
3. `app.py`: navigation refactor (mechanical; behavior-identical otherwise).
4. `app.py`: Feed Detail chart.
5. `app.py`: Performance page.
6. Manual smoke test: `uv run streamlit run ...` with mock broker data
   (`BROKER=mock`); verify nav, charts, timespan edge cases (empty window,
   single snapshot).

## 5. Acceptance Criteria

- [ ] Navigation renders vertically in the left sidebar; no top tabs remain.
- [ ] Feed Detail shows a candlestick+volume chart for OHLCV feeds and a line
      chart for scalar feeds; lookback control works; unfetched feeds warn.
- [ ] Performance page shows all Overview metrics plus Sharpe Ratio, Max
      Drawdown (%, ₩), Max Drawdown Duration, Total Return %, Volatility.
- [ ] Timespan selector (1M/3M/6M/YTD/1Y/All) filters equity curve, drawdown
      chart, and all metrics.
- [ ] `pytest` passes, including new metric tests.
- [ ] `just build` still succeeds with the added dependency.

## 6. Additional Suggestions — Resolution

The draft invited extra ideas. Owner decision: **plotly approved**, and
suggestions **(1) auto-refresh toggle** and **(5) sidebar freshness badge** are
included in this round:

1. **Auto-refresh toggle** (in scope): sidebar `st.toggle("Auto-refresh")`;
   when on, Overview and Performance bodies render inside
   `st.fragment(run_every="60s")` fragments so metrics/charts refresh every
   minute without a full-page rerun.
2. **Freshness badge in sidebar** (in scope): shows the last account snapshot
   timestamp and the most recent feed `last_updated_at`, so a stalled trader
   daemon or stale feeds are visible at a glance.
3. **Trades page filters**, **position breakdown on Overview**, **benchmark
   overlay (KOSPI)**, **color-coded trade signals**: deferred to a follow-up
   spec.
