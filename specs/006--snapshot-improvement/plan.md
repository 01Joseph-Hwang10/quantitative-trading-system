# Plan — Snapshot bar improvement implementation record

Source: original agent draft — merged into this plan (see Appendix)
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
| `tests/test_broker_toss.py` | **New.** Pure-function tests for `day_bar_from_candles`: picks today's candle, rejects the previous session's candle (weekend case), empty list, KST timezone conversion of non-KST-stamped timestamps. Always-on (no skip directive): the tests are network-free and environment-independent — an initial include-style-IP-allowlist skip directive was added and then rolled back after confirming no Toss API call exists on the test path. |

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

## Appendix — Original draft (agent-authored, merged from `draft.md`)

# Draft — Snapshot bar improvement

Improve the trader's same-day provisional bar (see `apply_snapshot` in
`system/apps/trader/trader.py` and `snapshot()` in `system/libs/feeds/yfinance.py`).
Today it is a **flat bar** — `open = high = low = close` = last broker price,
`volume = 0` — because yfinance publishes daily bars only after the session
closes and the broker only exposed a last-price endpoint.

## Motivation

The strategy (`system/libs/strategy/gold_ensemble.py`, ported from
`notebooks/strategy_1.ipynb`) decides on the last daily bar:

- **ADX(14)** consumes `high`/`low` directly. A zero-range provisional bar
  contributes no true range for that day and systematically understates ADX.
- **Bollinger bands / tanh thresholds** compare the last close against bands;
  the flat bar is fine for close, but the missing real open/high/low means the
  bar deviates from what the notebook backtest computed on genuine bars.

The schedules themselves (decision 15:00 KST, feed catch-up 09:00 KST) are
unchanged and stay as-is; only the quality of the provisional bar improves.

## Approach

Keep the architecture boundary — **yfinance for completed daily bars, the
broker for live data** — and source a real intraday bar from the Toss
`tossinvest` client (v0.1.0 already exposes
`TossClient.candles(symbol, interval, count, before)` → `Candle` objects with
full OHLCV). No new dependency, no yfinance intraday fallback (spotty KRX
coverage, rate limits, and it would break the "broker owns live data" design).

## Requirements

1. The provisional bar for the in-progress session carries the session's real
   `open` (first candle), `high` (max), `low` (min), cumulative `volume`
   (sum), and `close` = freshest last price (broker `get_last_price`, falling
   back to the last candle's close).
2. Only candles stamped **today (KST)** are aggregated.
3. Failure path degrades, never breaks the cycle: if the candles call errors,
   returns no candles, or the broker type doesn't provide a day bar, `snapshot()`
   falls back to the existing flat-bar behavior (current `get_last_price` path).
4. Broker surface change is minimal: one new `BrokerClient` protocol method.
   `MockBroker` implements it from `price_lookup` (flat bar, same shape) so
   mock-mode behavior and existing tests stay deterministic.
5. Nothing is persisted: the snapshot row stays ephemeral (`with_row` in
   `trader.py`), and the next morning's yfinance bar remains the stored record.
6. The 15:00 decision, order flow, and both cron schedules are unchanged.

## Changes (planned)

| File | Change |
|---|---|
| `system/apps/trader/broker/base.py` | Add `DayBar` dataclass (`date`, `open`, `high`, `low`, `close`, `volume`) and extend the `BrokerClient` protocol with `get_day_bar(symbol) -> DayBar \| None`. |
| `system/apps/trader/broker/tossinvest.py` | Implement `get_day_bar`: fetch today's intraday candles via `self._client.candles(...)`, filter to today (KST), aggregate OHLC + volume; return `None` on any failure. Interval string to be verified against the live API once and hardcoded (≈380 bars at 1-minute granularity covers 09:00–15:30; use the `before` cursor if `count` is capped). |
| `system/apps/trader/broker/mock.py` | Implement `get_day_bar` from `price_lookup` as a flat bar (preserves mock determinism). |
| `system/libs/feeds/yfinance.py` | `snapshot()` prefers `broker.get_day_bar(...)` and builds a full OHLCV row from it; falls back to the existing flat bar when `None`. |
| `tests/test_trader.py` / `tests/test_feeds.py` | Cover: day-bar aggregation (open/high/low/volume/close), KST day filtering, fallback to the flat bar when `get_day_bar` returns `None` or raises, and unchanged mock behavior. |

## Notes / known caveats

- yfinance bars are `auto_adjust`-adjusted while broker prices are raw; for
  `0072R0.KS` (spot-gold ETF, no splits/dividends) the factor is 1. If the
  trading ticker ever changes, this assumption must be re-checked — add a
  comment in `snapshot()`.
- The tossinvest client passes `interval` through opaquely; the exact accepted
  interval string is unverifiable offline. Implementation must pick one,
  document it, and fail closed (requirement 3) if the API rejects it.
- `ScalarFeed.snapshot` (macro tickers) is out of scope: at 15:00 KST the US
  markets are closed, `get_last_price` already yields `None`, and macro signals
  are lag-shifted anyway.

## Verification

- `uv run pytest` — full suite green, including new snapshot tests.
- `BROKER=mock` daemon cycle logs the provisional bar values (non-flat
  open/high/low when the mock is configured with an intraday bar).
- Manual check against `BROKER=toss` before the next trading day's decision
  cycle: one `get_day_bar` call returns a bar whose close ≈ last price and
  whose high/low bracket the session range.
