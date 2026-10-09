# Done — Decisions tab on the Trades page

Implementation record for `plan.md` (motivation, requirements, and owner
decisions from the original draft — not repeated here). Shipped in
`e3c38d5` (2026-10-09). 50/50 tests pass. `system/apps/monitor/` (+ test)
only — no schema, writer, or infrastructure changes.

## Surface

| File | Change |
|---|---|
| `system/apps/monitor/app.py` | `render_trades` now hosts `st.tabs(["Trades", "Decisions"])`. Trades tab: table (newest first) + per-trade expanders as before. Decisions tab: `ts / symbol / signal / reason / executed / indicators` with `indicators_json` compacted and `executed` as a boolean. The early `return` on empty trades is gone — each tab renders its own empty state and never blanks the other. |
| `system/apps/monitor/app.py` | `decisions` fetched once and shared by the Decisions tab and the trade expanders' `_nearest_decision` join (previously fetched only inside the trades branch). |
| `system/apps/monitor/support.py` | **New `decisions_frame()`** — pure, streamlit-free display mapping (`executed` → bool, `indicators_json` → compact string), extracted because `app.py` cannot be imported by pytest at all (it calls `main()` at import, which hard-stops on the Google-OAuth gate). |
| `tests/test_monitor.py` | Unit tests for `decisions_frame`: column order, newest-first ordering, `executed` → bool, indicators JSON compacted, empty-input case. |

## Verification status

`pytest` green plus the new render assertions. UI validated in a real
browser (ego-browser, 2026-10-07) with auth stubbed: seeded data (2 trades +
2 decisions) — grid, expanders, `nearest_decision` join, tab switching all
work; real local `data/` (0 trades, 3 decisions) — Trades tab shows its
empty state while the Decisions tab still renders the table (including
`market_closed` reasons and `{}` indicators). Screenshots captured during
verification.
