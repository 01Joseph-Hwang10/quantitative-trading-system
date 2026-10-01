# Implementation Plan — Initial Quantitative Trading System

Source: `specs/001--initial-implementation/draft.md`
Strategy reference: `notebooks/strategy_1.ipynb` (`GoldEnsembleStrategy`)

## 1. Goal

Bootstrap the first end-to-end version of the trading system: a `trader` app that
feeds data → computes signals → decides buy/sell/hold → (mock-)executes via
tossinvest and logs trades; and a `monitor` app (Streamlit) that visualizes
performance, manages data feeds, and gates access via Google login.

**Safety constraint**: no real orders are issued in this initial implementation.
The tossinvest broker client is mocked for all testing; the real client is wired
behind the same `BrokerClient` interface but disabled.

## 2. Repository Layout (target)

```
system/                          # repo root (= cwd)
├── system/                      # python package
│   ├── apps/
│   │   ├── trader/
│   │   │   ├── __main__.py      # CLI entrypoint (run loop / single cycle)
│   │   │   ├── trader.py        # Trader orchestration class
│   │   │   ├── broker/
│   │   │   │   ├── base.py      # BrokerClient protocol
│   │   │   │   ├── tossinvest.py# tossinvest adapter (async → sync facade)
│   │   │   │   └── mock.py      # MockBroker (persists to metadata.db)
│   │   │   └── logging.py       # trade/decision log writers
│   │   └── monitor/
│   │       ├── __main__.py      # `streamlit run` wrapper
│   │       ├── app.py           # Streamlit entry (tabs, auth)
│   │       └── pages/           # per-tab render functions
│   ├── config/
│   │   ├── settings.py          # pydantic-settings, env vars
│   │   └── gateways.py          # tossinvest client factory, db connections
│   └── libs/
│       ├── feeds/
│       │   ├── base.py          # DataFeed (pd.DataFrame subclass)
│       │   ├── registry.py      # feed discovery / name↔class mapping
│       │   ├── yfinance.py      # YFinanceFeed (daily OHLCV)
│       │   └── macro.py         # TNX / DXY / USDKRW feeds
│       ├── strategy/
│       │   ├── base.py          # Strategy protocol (decide() → Signal)
│       │   └── gold_ensemble.py # GoldEnsembleStrategy (port of notebook)
│       └── db/
│           ├── metadata.py      # metadata.db schema + DAOs
│           └── feed_store.py    # feed.db management incl. `_metadata`
├── data/                        # runtime: metadata.db, feed.db (gitignored)
├── tests/                       # pytest suite
└── pyproject.toml               # add: pytest, pydantic-settings, python-dotenv, streamlit-authenticator/google auth lib
```

## 3. Core Interfaces

### DataFeed (`libs/feeds/base.py`)

- Subclass of `pd.DataFrame` (per draft): constructed from rows loaded from
  `feed.db`; empty frame + metadata if never fetched.
- Class attrs: `name: str` (unique table name in `feed.db`), `source: str`
  (`yfinance`), `START_DATE: date | None` (series start; `None` for
  non-time-series), `symbols: list[str]` or similar source params.
- `update()`: fetch missing range from source (using `START_DATE` + last
  timestamp in `_metadata`/table), upsert into its table, update `_metadata`
  (`last_updated_at`, row count, source params).
- **Same-day bar**: completed daily bars come from yfinance, which does not
  publish the current (incomplete) trading day. The trader fills today's bar
  at decision time from the broker's market-data API (`PriceResponse` /
  intraday `CandlePageResponse`) via a `snapshot()` hook on the feed; in mock
  mode this replays the latest completed bar.
- Loader: `DataFeed.load(name, db)` classmethod → read table + attach metadata.

### Strategy (`libs/strategy/base.py`)

- `decide(view: MarketView) -> Signal` where `Signal ∈ {BUY, SELL, HOLD}` and
  `MarketView` bundles the trading-stock OHLCV + macro feeds (+ latest position
  context). **No broker access** — pure decision function.
- `GoldEnsembleStrategy`: port of `GoldEnsembleStrategy.next()` logic
  (ADX regime switch → Bollinger mean-reversion / SMA trend; macro gate `D_t`
  with `theta_entry`/`theta_exit`; parameters from notebook's tuned defaults).
  Indicators computed with `ta-lib` on the feed frame; macro `D_t_*` columns
  precalculated exactly as in notebook cell 4 (1-day delay to avoid look-ahead).

### BrokerClient (`apps/trader/broker/base.py`)

- Protocol: `get_account()`, `get_balance()`, `get_positions()`,
  `buy(symbol, qty)`, `sell(symbol, qty)`, `is_market_open()`.
- `TossBroker`: wraps async `tossinvest.TossClient` with a sync facade
  (`asyncio.run` per call or a dedicated loop); credentials from `.env`.
- `MockBroker`: implements the same protocol with in-memory + `metadata.db`
  persistence, deterministic fills at last close. Selected via `BROKER=mock|toss`
  env (default `mock`).

### Trader (`apps/trader/trader.py`)

Single cycle:
1. Load/update data feeds (feed.db).
2. Fetch account state from broker (balance, positions).
3. `strategy.decide(...)` → Signal.
4. If not trading-enabled (settings flag) or market closed → log `HOLD(skip)` only.
5. Else execute via `BrokerClient` (mock in this iteration), record order + fill.
6. Write rows to `metadata.db`: `decisions`, `trades`, `account_snapshots`.

Runner: long-running daemon — `python -m system.apps.trader`. Each day it
computes the next KRX trading day and sleeps until **15:00 KST**, then runs one
cycle. The cycle is also runnable one-shot via `--once` for testing/manual
runs. Same-day bar synthesis via the broker's market-data API (see §3 DataFeed).
Trader enable/disable is checked from `app_state.trader_enabled` each cycle.

## 4. Database Schema

### `metadata.db` (shared)
- `trades(id, ts, symbol, side, qty, price, order_id, status, strategy, note)`
- `decisions(id, ts, symbol, signal, reason, indicators_json, executed)`
- `account_snapshots(id, ts, cash, market_value, total, positions_json)`
- `performance(id, ts, metric, value)` — or computed on the fly in monitor
- `app_state(key, value)` — e.g. `trader_enabled`, `last_run_at`

### `feed.db`
- `_metadata(name PK, source, source_params_json, start_date, last_updated_at, row_count)`
- One table per feed, e.g. `ohlcv_411060KS`, `ohlcv_0072R0KS`, `macro_tnx`,
  `macro_dxy`, `macro_usdkrw` — columns `(date PK, open, high, low, close, volume)`;
  macro feeds `(date PK, value)`.

## 5. Monitor (Streamlit)

- Auth: Google OAuth. Preferred: `streamlit` OIDC (`st.login` with Google
  provider, requiring `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/redirect URL env
  + localhost callback config). Fallback if OIDC unavailable in the pinned
  streamlit version: `streamlit-google-auth` package. Gate every tab:
  email must be in `AUTHORIZED_USERS` (comma-separated).
- Tabs per draft, with the owner's permission amendment: Overview (market
  open/closed, balance, positions, P&L/win-rate metrics, latest trades, equity
  chart), Data Feeds (list + last update time + **delete/drop** button only —
  no update), Feed detail page (read-only table + delete/drop), Trades
  (full table with details), Settings (trader enable/disable toggle →
  `app_state.trader_enabled`).
- Reads `metadata.db`/`feed.db`; feed **drop** (table + `_metadata` row) is the
  only write path into `feed.db`.

## 6. Config

`system/config/settings.py` (pydantic-settings, `load_dotenv(".env")`):
`TOSSSEC_CLIENT_ID`, `TOSSSEC_CLIENT_SECRET` (existing), `BROKER`,
`AUTHORIZED_USERS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI`, `DATA_DIR` (default `./data`), `TRADER_ENABLED`,
`LOG_LEVEL`. `.env` gains the new keys with empty values; no secrets committed.

## 7. Testing

- Unit: feed upsert/idempotent update; strategy signals vs hand-computed
  notebook values on a small fixture slice; `D_t` parity with notebook cell 4.
- Integration: full trader cycle with `MockBroker` on a temp db; monitor
  functions against seeded dbs.
- `pytest` + pytest-asyncio for tossinvest adapter (mocked transport).

## 8. Milestones

1. **M1 Foundations** — settings, db schemas + DAOs, package scaffolding.
2. **M2 DataFeed layer** — base class, yfinance + macro feeds, feed.db
   management, idempotent `update()`.
3. **M3 Strategy** — `GoldEnsembleStrategy` port + parity tests vs notebook.
4. **M4 Broker + Trader** — `BrokerClient` protocol, `MockBroker`, tossinvest
   adapter (untested against live), trader cycle + logging, CLI entrypoints.
5. **M5 Monitor** — auth + all tabs, feed management wired to feed code.
6. **M6 Polish** — README, Dockerfile (empty today), pytest green, lint
   (black/isort per pyproject).

## 9. Resolved Decisions (from owner)

1. **Trader runtime**: long-running loop daemon inside the app. Later, the VM
   runs **two Docker containers**: one for `apps/trader`, one for
   `apps/monitor` (Dockerfile authored in a follow-up task; entrypoints must be
   container-friendly: `python -m system.apps.trader` and
   `streamlit run -m system.apps.monitor` style).
2. **Hyperparameters**: notebook-tuned values baked in statically.
3. **Position sizing**: whole-position only (no fractional sizing).
4. **Cadence**: one decision per trading day at **15:00 KST** (30 minutes
   before close), using same-day data. The schedule is a **configurable cron
   expression** (`DECISION_SCHEDULE_CRON`, default `0 15 * * 1-5`, evaluated in
   Asia/Seoul via croniter; a second cron covers the 09:00 feed catch-up).
   Cron controls *when we attempt*; the market-open guard still decides
   whether a decision runs on KRX holidays. ⚠️ Implication: yfinance daily
   bars are not available for the current (incomplete) trading day, so the
   trader needs a same-day snapshot source — the tossinvest market-data API
   (current price / intraday candles) synthesizes today's bar at decision
   time; in mock mode the trader replays using the latest completed bar.
5. **Google auth**: Streamlit native OIDC (`st.login` / Google provider).
6. **Monitor feed permissions**: **read + drop-table only**. No feed `update()`
   from the monitor (the trader daemon owns updates), no row-wise deletes —
   only dropping an entire feed table (with its `_metadata` row).
