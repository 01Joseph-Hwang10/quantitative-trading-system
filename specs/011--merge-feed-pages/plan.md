# Implementation Plan — Merge Feed Detail into Data Feeds (horizontal tabs)

Source: owner request (2026) — merge the "Data Feeds" page and the "Feed
Detail" page into a single "Data Feeds" page, organized as horizontal tabs at
the top, in the same style as the Trades page (`st.tabs(["Trades", "Decisions"])`).

## 1. Requirements

1. Single sidebar entry **Data Feeds**; the **Feed Detail** sidebar entry is
   removed.
2. The merged page shows two horizontal tabs at the top of the page, like the
   Trades page:
   - **Feeds** tab — the existing Data Feeds body (feed list table + drop-feed
     form).
   - **Feed Detail** tab — the existing Feed Detail body (feed selector, meta
     line, chart with lookback control, 200-row preview, per-feed delete).
3. No behavior change inside either tab — bodies move verbatim.

## 2. Design

- Keep the single-page file structure (`app.py`); no `pages/` dir.
- `render_data_feeds(feed_conn)` becomes the sole entry point:

  ```python
  def render_data_feeds(feed_conn: sqlite3.Connection) -> None:
      st.header("Data Feeds")
      st.caption(...)  # unchanged
      tab_list, tab_detail = st.tabs(["Feeds", "Feed Detail"])
      with tab_list:
          ...  # existing render_data_feeds body
      with tab_detail:
          ...  # existing render_feed_detail body
  ```

- `render_feed_detail()` is deleted (body moved into the second tab).
- `main()`: drop the `st.Page(..., render_feed_detail, url_path="feed-detail")`
  entry from the nav list.
- Widget keys are unaffected: the two selectboxes live on different pages
  (sidebar nav separates script runs), and the drop form is a separate form.
  `st.rerun()` after a drop still works inside a tab.
- Known limitation: old `#/feed-detail` bookmarks 404 (Streamlit tabs cannot be
  deep-linked without query-param plumbing — not worth it here).

## 3. Implementation Order

1. `app.py`: navigation — remove the Feed Detail page entry.
2. `app.py`: merge bodies into `render_data_feeds` with `st.tabs`; delete
   `render_feed_detail`.
3. Update the module docstring page list if needed.

## 4. Acceptance Criteria

- [ ] Sidebar shows Overview, Performance, Data Feeds, Trades, Settings (no
      Feed Detail).
- [ ] Data Feeds page renders horizontal tabs "Feeds" / "Feed Detail" at the
      top, styled like the Trades page tabs.
- [ ] Feeds tab: list table + drop form behave as before.
- [ ] Feed Detail tab: selector, meta, chart (OHLCV candlestick / scalar line),
      preview, delete button behave as before.
- [ ] `pytest` passes (no tests reference these page functions, but confirm).

## 5. Verification

Manual smoke test with ego-browser against a locally run Streamlit instance
(`uv run streamlit run -m system.apps.monitor` with the local `.env`):
Google OIDC login, tab navigation, chart rendering on a real feed.

Record the outcome in `done.md` after implementation.
