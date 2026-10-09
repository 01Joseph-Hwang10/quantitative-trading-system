# Implementation Plan — Decisions tab on the Trades page

Source: `specs/009--decisions-table/draft.md`
Status: **implemented and verified on 2026-10-07** (50/50 tests pass; UI
validated in a real browser via ego-browser — see §5).
Scope: `system/apps/monitor/app.py` only (+ test). No schema, writer, or
infrastructure changes.

## 1. Requirements (from draft)

1. Trades page hosts **two horizontal tabs** — "Trades" and "Decisions" —
   switchable in one view (`st.tabs`), newest-first rows in both tables.
2. Decisions table columns: `ts`, `symbol`, `signal`, `reason`, `executed`,
   `indicators` (`indicators_json` compacted).
3. Independent rendering: an empty table shows its empty state inside its own
   tab and never blanks out the other tab (removes the current early-return
   on empty trades).
4. Read-only; reuses `metadata.list_decisions`.

## 2. Design Decisions

1. **Tabs, not stacked sections**: `st.tabs(["Trades", "Decisions"])` inside
   `render_trades` (owner decision). Streamlit renders both tab bodies every
   run (tabs are display-level only), so there is no data cost, and tab
   switching costs no rerun.
2. **Indicators**: one compacted `indicators` column via the existing
   `_compact_json` helper — no expanders (a ~1-row/day table makes per-row
   expanders disproportionate; Streamlit's dataframe cell inspection covers
   occasional full-JSON peeking).
3. **Row cap**: none; the table grows ~1 row/day and the Trades table already
   renders unbounded.
4. **Data access**: no new SQL. `metadata.list_decisions` returns all rows
   newest-first already; transform in pandas (`executed` → bool,
   `indicators_json` → compact string). One fetch shared between the
   Decisions tab and the trade expanders' `_nearest_decision` join.
5. **Trade expanders stay in the Trades tab** under the trades table, exactly
   as today.

## 3. File Changes

```
system/apps/monitor/app.py   # render_trades: two-tab layout (Trades | Decisions)
tests/test_monitor.py        # render-level test for the tab layout
```

### `system/apps/monitor/app.py` — `render_trades`

```python
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

    with tab_decisions:
        decisions_frame = pd.DataFrame(
            [
                {
                    "ts": row["ts"],
                    "symbol": row["symbol"],
                    "signal": row["signal"],
                    "reason": row["reason"],
                    "executed": bool(row["executed"]),
                    "indicators": _compact_json(row["indicators_json"]),
                }
                for row in decisions
            ]
        )
        if decisions_frame.empty:
            st.write("No decisions recorded yet.")
        else:
            st.dataframe(decisions_frame, use_container_width=True)
```

- The early `return` on empty trades is gone: both tabs always render, each
  with its own empty state.
- `decisions` is fetched once and shared by the Decisions tab and
  `_nearest_decision` (previously fetched only inside the trades branch).
- `_nearest_decision` and all helpers unchanged.

### `tests/test_monitor.py`

- Deviation from the original assumption: there were **no existing
  render-level tests**, and `app.py` cannot be imported by pytest at all —
  the module calls `main()` at import, which hard-stops on the Google-OAuth
  gate. The display mapping was therefore extracted into
  `support.decisions_frame()` (pure, streamlit-free) and unit-tested there.
- New tests: column order (`ts, symbol, signal, reason, executed,
  indicators`), newest-first ordering, `executed` mapped to bool, indicators
  JSON compacted, and the empty-input case.
- The full tab layout + empty states are verified in the browser (§5), which
  also covers the expander join regression.

## 5. Browser verification (ego-browser, 2026-10-07)

Ran the app locally twice with auth stubbed (temp dir launcher; repo `.env`,
`data/`, and `.streamlit/secrets.toml` untouched):

| Instance | Data | Checked | Result |
|---|---|---|---|
| :8601 | seeded (2 trades + 2 decisions) | Trades tab shows the grid (newest first) + both expanders; Decisions tab shows `ts/symbol/signal/reason/executed/indicators` with compact JSON and `executed` checkboxes; tab switching works | ✅ screenshots |
| :8601 | same | Expander opens: `nearest_decision` join + `order_id` render (no regression) | ✅ |
| :8602 | repo `data/` (0 trades, 3 decisions) | Trades tab shows "No trades recorded yet."; Decisions tab still renders the table (incl. `market_closed` reason and `{}` indicators) | ✅ screenshots |

## 4. Verification

1. `pytest` — all existing tests pass plus the new render assertions.
2. Local run (`streamlit run` against `data/metadata.db`, which has 3 decision
   rows): Trades page shows the Trades tab (table + expanders) and the
   Decisions tab with `signal=HOLD`, `executed=False`, compact `indicators`
   JSON; switching tabs costs no rerun; no regression in the trade expander
   join.
3. Empty-db check (temp metadata.db): both tabs render their empty states,
   no exception.
