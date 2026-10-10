# Done — Merge Feed Detail into Data Feeds

Implementation record for `plan.md` (owner request, 2026). No commit yet at
time of writing.

## Surface

| File | Change |
|---|---|
| `system/apps/monitor/app.py` | `main()`: removed the `st.Page` entry for Feed Detail (`url_path="feed-detail"`); nav is now Overview, Performance, Data Feeds, Trades, Settings. |
| `system/apps/monitor/app.py` | `render_data_feeds()` now hosts both former page bodies as horizontal tabs — `st.tabs(["Feeds", "Feed Detail"])` — in the Trades-page style. Bodies moved verbatim; `return` in the Detail tab replaced by `else` branch so the Feeds tab still renders. |
| `system/apps/monitor/app.py` | Deleted `render_feed_detail()`; module docstring notes the tab merge. |
| `specs/011--merge-feed-pages/` | `plan.md` (this spec). |

## Verification

- `pytest`: 50 passed (no tests referenced the page functions).
- `grep` confirmed no remaining references to `render_feed_detail` /
  `feed-detail` in `system/` or `tests/`.
- Manual smoke test with ego-browser against a locally run Streamlit
  (`localhost:8501`, local `.env`, Google OIDC login):
  - Sidebar shows exactly Overview / Performance / Data Feeds / Trades /
    Settings — no Feed Detail entry.
  - Data Feeds page renders horizontal tabs "Feeds | Feed Detail" at the top.
  - **Feeds** tab: feed list table (macro_dxy, macro_tnx, macro_usdkrw,
    ohlcv_0072R0KS, ohlcv_411060KS) + "Delete a feed (drop table)" form.
  - **Feed Detail** tab: feed selector, meta line (Source · Rows · Last
    updated), 90-day scalar chart for `macro_dxy` rendered. OHLCV path is
    unchanged verbatim code (candlestick rendering verified in spec 004).

## Known limitations (per plan)

- Old `#/feed-detail` bookmarks 404; users navigate to Data Feeds → "Feed
  Detail" tab (Streamlit tabs are not deep-linkable without query-param
  plumbing).
