# Done — Snapshot bar improvement (real intraday bar from the broker)

Implementation record for `plan.md` (motivation, requirements, and caveats
from the original draft — not repeated here). Shipped in `9c54e7e`
(2026-10-04); skip-directive cleanup in `3bcff5b` (2026-10-05). 45/45 tests
pass; verified live against the Toss API (read-only).

## Key implementation finding (shaped the result)

The tossinvest daily candle list **includes the current session's
in-progress bar** — real intraday OHLC plus cumulative volume in one
`candles(symbol, "1d", count=1)` call. Minute-candle aggregation (the
draft's default approach) was dropped: `1m` candles for this product carry
`volume=0`, so aggregation would have produced a useless volume. The
`DAILY_CANDLE_INTERVAL` string was verified live once and hardcoded.

## Surface

| File | Change |
|---|---|
| `system/apps/trader/broker/base.py` | `DayBar` dataclass (`date`, `open`, `high`, `low`, `close`, `volume`) + `BrokerClient.get_day_bar(symbol) -> DayBar \| None` protocol method. |
| `system/apps/trader/broker/tossinvest.py` | `get_day_bar`: one daily-candle call → today's KST candle via the pure `day_bar_from_candles()` (weekend/holiday safe: rejects a candle stamped on a previous session), close overlaid with the freshest `get_last_price`, returns `None` on any failure (graceful fallback to the flat bar). |
| `system/apps/trader/broker/tossinvest.py` | Fixed all latent `float(Dec)` live-API bugs: tossinvest `Dec` has no `__float__` — `get_last_price` and `get_account` (`cash_buying_power`, `average_purchase_price`, holdings `last_price`) now convert via the shared `_dec_to_float` helper. |
| `system/apps/trader/broker/mock.py` | `get_day_bar` returns a flat `DayBar` from `price_lookup` (mock determinism preserved). |
| `system/libs/feeds/yfinance.py` | `snapshot()` prefers `broker.get_day_bar(...)` (duck-typed via `getattr`) and falls back to the existing flat last-price bar; docstring notes the auto_adjust-vs-raw-price caveat for `0072R0.KS`. |
| `tests/test_feeds.py` | Day bar preferred over flat bar; fallback when `get_day_bar` → `None`; mock day bar is flat. |
| `tests/test_broker_toss.py` | **New.** Network-free pure-function tests for `day_bar_from_candles` (today's candle picked, previous-session candle rejected, empty list, KST conversion). A default skip directive was added in `9c54e7e` and removed in `3bcff5b` after confirming no Toss API call exists on the test path — always-on now. |

Schedules, decision flow, and `trader.py` were unchanged (the snapshot row
stays ephemeral via `with_row`).

## Deviations from the draft

1. Daily candles instead of minute-candle aggregation (see finding above).
2. The in-progress bar's *intraday updates* during a live session were not
   observable (implementation day was a Sunday); the design degrades safely
   to `None` → flat bar if the API publishes today's candle only after close.

## Verification status

`uv run pytest` — 45 passed (41 existing + 4 feed + 4 broker tests, minus
reorganization). Live read-only check on Sunday 2026-10-04: `get_day_bar`
correctly returned `None` (newest candle = Friday's completed bar), and
mapping Friday's candle → `DayBar(2026-10-02, open=12250, high=12265,
low=12140, close=12195, volume=582709)` confirmed `Dec` conversion and KST
mapping. Deployed with the next `just update`; production logs show the
non-flat provisional bars during live sessions.
