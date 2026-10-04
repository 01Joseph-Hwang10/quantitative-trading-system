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
