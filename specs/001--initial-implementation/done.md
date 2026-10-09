# Done — Initial implementation of the trading system

Implementation record for `instruction.md` (decisions in `plan.md` §9 are
binding — not repeated here). Shipped across two commits on 2026-10-01:
`3b61779` (spec docs + package scaffolding) and `66130e6` (full
implementation, ~3,200 lines).

## Surface

| Area | Shipped |
|---|---|
| `system/config/` | `settings.py` — pydantic `Settings` over `.env` (`BROKER` default `mock`, Toss credentials, Google OIDC, `AUTHORIZED_USERS`, `DATA_DIR`, `TRADER_ENABLED`, `DECISION_SCHEDULE_CRON` / `FEED_UPDATE_SCHEDULE_CRON` evaluated in Asia/Seoul, `LOG_LEVEL`); `gateways.py` — `get_broker()` / db connections / feed registry factories. |
| `system/libs/db/` | `metadata.py` — `trades`, `decisions`, `account_snapshots`, `app_state` schemas + plain DAO functions (KST ISO8601 timestamps); `feed_store.py` — `_metadata` + user feed tables, `upsert_rows` / `read_table` / `list_feeds` / `drop_feed` (drops table **and** meta row) / `last_date`, feed-name sanitization `^[a-z0-9_]+$`. |
| `system/libs/feeds/` | `base.py` — `DataFeed(pd.DataFrame)` with `load()` / idempotent `update()` / `snapshot(broker)` / `START_DATE`; `yfinance.py` (OhlcvFeed), `macro.py` (ScalarFeed), `registry.py` — five registered feeds (`0072R0.KS`, `411060.KS`, `^TNX`, `DX-Y.NYB`, `USDKRW=X`). |
| `system/libs/strategy/` | `base.py` — `Strategy` protocol, `Signal`, `MarketView` (position state lives here, not in feeds); `gold_ensemble.py` — faithful port of notebook cells 4 & 6: `compute_macro_frame` (1-day delayed TNX/DXY/FX → `D_t` for n ∈ {10,20,40,60}), ADX regime switch (Bollinger mean-reversion vs SMA20/60 trend), macro gate entry/exit + stop-loss, static tuned params (`n_macro=10, k=1.5, theta_entry=0.3`). No broker/network access in the module. |
| `system/apps/trader/` | `broker/base.py` — `BrokerClient` protocol (`get_account`, `get_last_price`, `is_market_open`, `buy`, `sell`); `broker/tossinvest.py` — async `TossClient` → sync facade (never live-tested); `broker/mock.py` — deterministic fills, `positions.json` state, reject-on-insufficient-cash; `trader.py` — one cycle (enabled guard → market-open guard → feed update → MarketView with today's snapshots → decide → execute → record decision/trade/snapshot); `runner.py` — croniter daemon (15:00 KST decision, 09:00 KST feed catch-up), SIGTERM-friendly; `__main__.py` — `run` / `--once` CLI. |
| `system/apps/monitor/` | Streamlit app with native Google OIDC (`st.login`), `AUTHORIZED_USERS` gate, Overview / Data Feeds / Feed detail / Trades / Settings tabs; feed **drop** is the only feed.db write path; `support.py` for metric computations. |
| Tests | `tests/` — feed store, feeds, gold-ensemble (notebook parity), trader cycles with `MockBroker`, monitor metrics; `conftest.py` with temp-db fixtures. |
| `pyproject.toml` | Added `pydantic-settings`, `python-dotenv`, `pytest`, `pytest-asyncio`. |

## Deviations from the plan

1. `metadata.db` has no `performance` table — metrics are computed on the
   fly from `trades` / `account_snapshots` in the monitor.
2. No `monitor/pages/` directory — single `app.py` (the app was small).
3. `BrokerClient` narrowed from the draft's `get_balance()` /
   `get_positions()` to a single `get_account()` returning `AccountState`.

## Verification status

Full end-to-end cycle runs with `BROKER=mock` (`--once` exits 0, decision
rows recorded); monitor boots with auth gate; pytest green at the time of
`66130e6`. Deployment (Docker/Terraform/Ansible) was deliberately deferred
to spec 002.
