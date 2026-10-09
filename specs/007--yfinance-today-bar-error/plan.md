# Plan — Stop requesting the not-yet-published daily bar

Source: original agent draft — merged into this plan (see Appendix)
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

## Relation to spec 006 (snapshot improvement)

007 **enforces** 006's invariant rather than merely coexisting with it:
"yfinance owns completed daily bars; the broker owns live data."

- `apply_snapshot` (trader.py) appends the broker day bar only when
  `last_date < today`. Before 007, the 15:00 KST decision cycle's
  `update_feeds` could fetch today's intraday `0072R0.KS` bar from yfinance
  (KRX session open), setting `last_date = today` and **silently skipping**
  the broker snapshot — the strategy then decided on an auto-adjusted
  yfinance bar instead of 006's raw-price broker OHLCV. The yesterday-clamp
  makes that path impossible.
- Flow after 007: 09:00 KST update stores through yesterday; the 15:00
  decision finds `last_date = yesterday < today` → broker `get_day_bar` row
  appended ephemerally via `with_row` (006's flow, unchanged); the next
  morning's update re-fetches it from yfinance as a completed bar.
- Shared pre-existing caveat (flagged in both specs): broker snapshot prices
  are raw while stored yfinance bars are `auto_adjust=True` — safe only
  because `0072R0.KS` has adjustment factor 1; re-check if the ticker
  changes. Unchanged by 007.

## Out of scope

- Suppressing the `yfinance` logger globally (would hide real errors).
- Adding an after-US-close feed-update cron (not needed: macro frame uses
  `shift(1)` delayed values, so intraday DXY freshness adds nothing).

## Verification

1. `uv run pytest` — all existing + new tests pass.
2. Deploy → next 09:00 KST feed update ships **no** `Data doesn't exist`
   ERROR in GCP Logging (check `logName=...trader`, `python_logger=yfinance`).

## Appendix — Original draft (agent-authored, merged from `draft.md`)

# Draft — Daily ERROR noise: "Data doesn't exist" from yfinance

Production logs a `yfinance` ERROR every feed update:

```
2026-10-07 00:00:30,085 ERROR yfinance $DX-Y.NYB: Data doesn't exist for
startDate = 1791345600, endDate = 1791432000   (2026-10-07 04:00Z → 10-08 04:00Z)
```

## Root cause

- `feed_update_schedule_cron = "0 9 * * 1-5"` fires at 09:00 KST = 00:00 UTC —
  matches the log timestamp exactly (00:00:30Z).
- `DataFeed.update()` (system/libs/feeds/base.py:115) computes
  `fetch_start = last + 1 day` (= **today**, since yesterday's bar is stored)
  and `fetch_end = date.today()` (**today**, container runs UTC). Net effect:
  the request covers *today's* daily bar only.
- DX-Y.NYB (ICE US Dollar Index) publishes its daily bar only after the US
  session ends (~22:00 UTC). At 09:00 KST it cannot exist → yfinance logs
  ERROR and returns an empty frame. `update()` degrades fine (0 rows stored,
  cycle continues), so this is log noise + a wasted API call — but it is
  *guaranteed* every weekday for US-session tickers (DX-Y.NYB, ^TNX; FX
  USDKRW=X also has no 00:00 UTC daily bar).

## Invariant violated

The design (see `OhlcvFeed`/`ScalarFeed` docstrings and spec 006) already
states: **yfinance owns completed daily bars; the broker snapshot covers the
in-progress day.** `fetch_end = today` contradicts that — it asks yfinance for
a bar the design says must not come from yfinance.
