# Plan — Stop requesting the not-yet-published daily bar

Source: `specs/007--yfinance-today-bar-error/draft.md`
Status: **implemented and verified on 2026-10-07** (48/48 tests pass; the
trader's schedules, decision flow, and `snapshot()` behavior are unchanged).

## Changes

| File | Change |
|---|---|
| `system/libs/feeds/base.py` | In `DataFeed.update()`, default `fetch_end` to the **last completed day** (`date.today() - timedelta(days=1)`); keep the explicit `end` parameter authoritative (no current call site passes it). Update the docstring: yfinance daily bars exist only for completed sessions; today's row comes from `snapshot()` via the broker. When `fetch_start > fetch_end` (already caught up), return 0 **without calling yfinance** — this is what eliminates the request entirely. |
| `system/libs/feeds/base.py` | Defense in depth: wrap `self.fetch_rows(...)` in `try/except Exception` → log a warning (`feed=%s fetch failed`, `logger.warning(..., exc_info=True)`) and return 0, so one flaky ticker can't abort the remaining feeds in `Trader.update_feeds()` (currently an exception propagates to the Runner and skips siblings). |
| `tests/test_feeds.py` | New tests: (1) `update()` with last=two-days-ago requests a range ending **yesterday** (recorded `fetch_rows` calls); (2) fully caught-up feed performs **no upstream call** and returns 0; (3) `fetch_rows` raising → warning logged, returns 0, and a sibling feed still updates. Existing `test_update_fetches_only_missing_range` reworked to be date-robust (expected count computed from the frame vs yesterday, instead of a hard-coded 5 that assumed the old today-inclusive range). |

## Notes from implementation

- Existing tests stored upstream frames ending *today*; after the clamp they
  were already caught up, so the new tests store through two days ago (fetch
  range end = yesterday) to exercise the actual fetch path.

## Why no data lag regression

- Today's request for DX-Y.NYB/^TNX **always** failed ("Data doesn't exist"),
  so clamping to yesterday loses nothing.
- US tickers: at 09:00 KST day *T+1* (00:00 UTC), yesterday's (day *T*) bar is
  already published (~21–22:00 UTC of *T*) → fetched the same morning as
  before. At the 15:00 KST decision the range is empty → 0 calls, and the
  strategy's `shift(1)` delayed macro frame is unchanged.
- KRX feeds (`0072R0.KS`, `411060.KS`): yesterday's bar exists at 09:00 KST;
  today's row is covered by `apply_snapshot`/`snapshot()` (spec 006), which is
  strictly better (real intraday OHLCV from the broker).

## Out of scope

- Suppressing the `yfinance` logger globally (would hide real errors).
- Adding an after-US-close feed-update cron (not needed: macro frame uses
  `shift(1)` delayed values, so intraday DXY freshness adds nothing).

## Verification

1. `uv run pytest` — all existing + new tests pass.
2. Deploy → next 09:00 KST feed update ships **no** `Data doesn't exist`
   ERROR in GCP Logging (check `logName=...trader`, `python_logger=yfinance`).
