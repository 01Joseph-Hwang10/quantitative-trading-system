# Draft — Decisions table on the Trades page

Add a **Decisions** table to the monitor so every strategy decision (including
non-executed ones — HOLD, market-closed, disabled) is visible, on the **same
page as the Trades table** (`render_trades`, `system/apps/monitor/app.py`).

## Motivation

- The trader writes one `decisions` row per scheduled cycle
  (15:00 KST, `metadata.record_decision`), but the monitor only surfaces
  decisions indirectly — the "Latest Trades" expanders show the *nearest*
  decision for each executed trade. Decisions that do **not** lead to a trade
  (most HOLD cycles, `market_closed`, `disabled`) are invisible in the UI.
- The Trades page is the natural home: a decision is the "why" behind a trade,
  and the existing trade expanders already join the two by timestamp.
- No schema or writer changes are needed — `metadata.list_decisions()` already
  exists and the table is small (one row per trading day).

## Requirements

1. The Trades page (`render_trades`) shows a **Decisions** subheader with the
   full decisions table, in addition to the existing Trades table.
2. Columns: `ts`, `symbol`, `signal`, `reason`, `executed`, `indicators`.
   - `indicators` renders `indicators_json` compacted (existing
     `_compact_json` helper) so the grid stays readable.
   - `executed` renders as a boolean (or ✅ / —) rather than 0/1.
3. Ordering: newest first (`ts DESC`), matching the Trades table.
4. Empty state: "No decisions recorded yet." when the table is empty (page
   must not blank out the Trades table just because decisions are empty —
   the current early-return when there are no trades would hide decisions;
   restructure so each table renders independently).
5. Read-only: no new SQL beyond the existing `list_decisions`; no writer or
   schema changes.

## Approach

- **Horizontal tabs on the Trades page** (owner decision): `st.tabs
  (["Trades", "Decisions"])` — the page switches between the two tables via
  tab, instead of stacking sections vertically.
- Drop the early `return` on empty trades; render each tab with its own
  empty state so an empty table never blanks out the other.
- Decisions tab: `st.dataframe` over
  `[dict(row) for row in metadata.list_decisions(conn)]` with the `ts` kept
  verbatim (consistent with the Trades table) and `indicators_json`
  compacted to an `indicators` column.
- Row detail: per-decision expanders would double the DOM for a table that
  grows daily; instead rely on the compacted JSON column + Streamlit's
  built-in dataframe cell inspection. If a full-JSON view is wanted later it
  can be added as an expander capped to the latest N rows.

## Resolved decisions (owner)

1. **Placement**: horizontal tabs on the Trades page — Trades and Decisions
   switchable in one view (not stacked sections, not a separate page).
2. **Detail view for `indicators_json`**: compact column only.
3. **Row cap**: none (~1 row/day; the Trades table is already unbounded).
