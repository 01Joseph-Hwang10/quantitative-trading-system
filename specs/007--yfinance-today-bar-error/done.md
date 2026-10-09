# Done — Stop requesting the not-yet-published yfinance daily bar

Implementation record for `plan.md` (root cause and invariant from the
original draft — not repeated here). Shipped in `02ec526` (2026-10-07);
follow-up docs commit `65c8e01` (relation to spec 006). 48/48 tests pass.
Schedules, decision flow, and `snapshot()` behavior unchanged.

## Surface

| File | Change |
|---|---|
| `system/libs/feeds/base.py` | `DataFeed.update()` defaults `fetch_end` to the **last completed day** (`date.today() - timedelta(days=1)`); explicit `end` stays authoritative. When already caught up (`fetch_start > fetch_end`), returns 0 **without calling yfinance** — eliminating the guaranteed-daily `Data doesn't exist` ERROR for US-session tickers (DX-Y.NYB, ^TNX) at the 09:00 KST update. |
| `system/libs/feeds/base.py` | Defense in depth: `fetch_rows` wrapped in `try/except` → `logger.warning(..., exc_info=True)` + return 0, so one flaky ticker can't abort sibling feeds in `Trader.update_feeds()`. |
| `tests/test_feeds.py` | New: update requests a range ending **yesterday**; fully caught-up feed makes **no upstream call** and returns 0; raising `fetch_rows` → warning logged, 0 rows, sibling feed still updates. Existing range test reworked to be date-robust (frames stored through two days ago so the fetch path is actually exercised). |

## Notes

- No data-lag regression: today's request always failed anyway; yesterday's
  bar is published before the 09:00 KST update for both US and KRX tickers.
- This **enforces** spec 006's invariant ("yfinance owns completed daily
  bars; the broker owns live data"): before the clamp, the 15:00 decision
  cycle could fetch today's intraday `0072R0.KS` bar from yfinance, silently
  skipping the broker snapshot. Shared caveat (flagged in both specs):
  broker snapshot prices are raw while stored yfinance bars are
  `auto_adjust=True` — safe only while the ticker is `0072R0.KS` (factor 1).
- Out of scope (unchanged): no global yfinance-logger suppression, no
  after-US-close cron (macro frame uses `shift(1)` delayed values).

## Verification status

`uv run pytest` — all tests pass. Deployed via `just update` on 2026-10-07;
the next 09:00 KST feed update produced no `Data doesn't exist` ERROR in
GCP Logging.
