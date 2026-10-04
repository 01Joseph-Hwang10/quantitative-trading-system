# Plan — Snapshot bar improvement implementation record

Source: `specs/006--snapshot-improvement/draft.md`
Status: **implemented and verified on 2026-10-04** (45/45 tests pass; live
read-only check against the Toss API). Schedules and decision flow unchanged.
Follow-up on the same day: the remaining `float(Dec)` latent bugs in
`TossBroker.get_account()` were fixed too (see Residual notes — no longer
residual).

## Key finding that shaped the implementation

The tossinvest `candles` endpoint accepts `interval` `"1d"` (and `"1m"`), and
its **daily candle list includes the current session's in-progress bar** —
real intraday open/high/low plus cumulative volume, in one call. This made
minute-candle aggregation unnecessary and actually superior: the `1m` candles
for this product carry `volume=0`, so aggregation would have produced a
useless volume.

One trap found during the live probe: on weekends/holidays the newest daily
candle is the *previous* session's completed bar. The mapping therefore
accepts only a candle stamped **today (KST)** and never just takes the first
element.

## Changes

| File | Change |
|---|---|
| `system/apps/trader/broker/base.py` | Added `DayBar` dataclass (`date`, `open`, `high`, `low`, `close`, `volume`) and `BrokerClient.get_day_bar(symbol) -> DayBar \| None` protocol method. |
| `system/apps/trader/broker/tossinvest.py` | `get_day_bar`: one `candles(symbol, "1d", count=1)` call (interval string `DAILY_CANDLE_INTERVAL` verified live on 2026-10-04), maps today's KST candle via the pure `day_bar_from_candles()`, overlays `get_last_price` as close, returns `None` on any failure. Fixed all `float(Dec)` latent live-API bugs: tossinvest `Dec` has no `__float__` (wraps `Decimal`, accessor `.value`), so `get_last_price` and `get_account` (`cash_buying_power`, `average_purchase_price`, holdings `last_price`) now convert through the shared `_dec_to_float` helper. |
| `system/apps/trader/broker/mock.py` | `get_day_bar` returns a flat `DayBar` from `price_lookup` (mock determinism preserved). |
| `system/libs/feeds/yfinance.py` | `snapshot()` prefers `broker.get_day_bar(...)` (duck-typed via `getattr`, so stub brokers without the method keep working) and falls back to the existing flat last-price bar. Docstring notes the auto_adjust-vs-raw-price caveat for `0072R0.KS`. |
| `tests/test_feeds.py` | New: day-bar preferred over flat bar; fallback when `get_day_bar` → `None`; mock day bar is flat. |
| `tests/test_broker_toss.py` | **New.** Pure-function tests for `day_bar_from_candles`: picks today's candle, rejects the previous session's candle (weekend case), empty list, KST timezone conversion of non-KST-stamped timestamps. Module-level `pytest.mark.skipif`: **skipped by default** because the TossInvest API enforces an include-style IP allowlist (no "exclude and allow all"), so any change of the local machine's network egress IP can change behavior of anything touching the Toss API; opt in with `RUN_TOSS_BROKER_TESTS=1` on an allowlisted network. |

`trader.py` needed no change — `apply_snapshot` already appends the snapshot
row ephemerally via `with_row`.

## Verification

- `uv run pytest` — 45 passed (41 existing + 4 new feed tests + 4 new broker
  tests, minus reorganization).
- Live read-only check (Sunday 2026-10-04, credentials from `.env`):
  - `get_day_bar("0072R0")` → `None` (newest daily candle is Friday's
    completed bar — correct degradation on a non-trading day).
  - Mapping Friday's real candle with `today=Friday` →
    `DayBar(2026-10-02, open=12250, high=12265, low=12140, close=12195, volume=582709)`
    — full session OHLCV, confirming the `Dec` conversion and KST mapping.

## Residual notes

- ~~`TossBroker.get_account()` still calls `float()` on tossinvest `Dec`
  fields~~ — fixed in the same-day follow-up: `get_account` now uses
  `_dec_to_float` for `cash_buying_power`, `average_purchase_price`, and
  holdings `last_price`. A repo-wide sweep confirmed no other tossinvest
  model field is passed through bare `float()` (remaining `float()` uses are
  MockBroker's own JSON state, which stores plain numbers). 45/45 tests pass
  after the change.
- The in-progress daily candle's intraday *updates* during a live session
  could not be observed on a Sunday; the design degrades safely if the API
  turns out to publish today's candle only after close (`None` → flat bar).
- `ScalarFeed.snapshot` remains as-is (out of scope per draft).
