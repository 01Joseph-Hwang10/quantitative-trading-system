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
