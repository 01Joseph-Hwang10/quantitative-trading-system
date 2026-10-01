# Implementation Instruction — Initial Quantitative Trading System

Authoritative instruction for implementing `specs/001--initial-implementation`.
Decisions in `plan.md` §9 are binding. Strategy reference:
`notebooks/strategy_1.ipynb` (`GoldEnsembleStrategy`).

## 0. Ground Rules

- Python 3.13, `uv` for dependency management, black (line-length 120) + isort.
- **Never issue real orders.** `BROKER=mock` is the default; the tossinvest
  adapter exists but is only exercised with mocked responses in tests.
- Never modify `.env` values; only append new empty keys if needed.
- Sqlite DBs live under `DATA_DIR` (default `./data`, gitignored).
- Follow existing code style; no abbreviated variable names; prefer top-level
  functions over private class methods.

## 1. Package Layout

```
system/system/
├── apps/
│   ├── trader/
│   │   ├── __main__.py          # argparse CLI: `run` (daemon) / `run --once`
│   │   ├── runner.py            # daemon loop: schedule 15:00 KST cycles
│   │   ├── trader.py            # Trader: one decision cycle
│   │   └── broker/
│   │       ├── __init__.py
│   │       ├── base.py          # BrokerClient Protocol + dataclasses
│   │       ├── tossinvest.py    # TossBroker (async → sync facade)
│   │       └── mock.py          # MockBroker
│   └── monitor/
│       ├── __main__.py          # exec: streamlit run
│       └── app.py               # Streamlit UI (tabs + auth)
├── config/
│   ├── settings.py              # Settings (pydantic-settings)
│   └── gateways.py              # get_broker(), get_dbs(), get_feed_registry()
└── libs/
    ├── db/
    │   ├── metadata.py          # metadata.db schema + DAO functions
    │   └── feed_store.py        # feed.db: _metadata + table lifecycle
    ├── feeds/
    │   ├── base.py              # DataFeed(pd.DataFrame)
    │   ├── registry.py          # name → DataFeed subclass registry
    │   ├── yfinance.py          # OhlcvFeed (trading & proxy gold)
    │   └── macro.py             # ScalarFeed: TNX, DXY, USDKRW
    └── strategy/
        ├── base.py              # Strategy protocol, Signal enum, MarketView
        └── gold_ensemble.py     # GoldEnsembleStrategy
data/                             # runtime artifacts (gitignored)
tests/
```

Dependencies to add: `pydantic-settings`, `python-dotenv`, `pytest`,
`pytest-asyncio`. (croniter, streamlit, ta-lib, yfinance, tossinvest,
pandas, numpy already present.)

## 2. Configuration

`config/settings.py` — replace the example docstring content with a real
`Settings(BaseSettings)`; `load_dotenv(".env")` at module import:

| Field | Env var | Default |
|---|---|---|
| `broker` | `BROKER` | `mock` |
| `toss_client_id` | `TOSSSEC_CLIENT_ID` | required if `BROKER=toss` |
| `toss_client_secret` | `TOSSSEC_CLIENT_SECRET` | required if `BROKER=toss` |
| `authorized_users` | `AUTHORIZED_USERS` | `""` (parsed to list) |
| `google_client_id` | `GOOGLE_CLIENT_ID` | `None` |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | `None` |
| `google_redirect_uri` | `GOOGLE_REDIRECT_URI` | `http://localhost:8501/oauth2callback` |
| `data_dir` | `DATA_DIR` | `./data` |
| `trader_enabled` | `TRADER_ENABLED` | `true` (initial value only) |
| `decision_schedule_cron` | `DECISION_SCHEDULE_CRON` | `0 15 * * 1-5` (15:00 KST, weekdays) |
| `feed_update_schedule_cron` | `FEED_UPDATE_SCHEDULE_CRON` | `0 9 * * 1-5` (09:00 KST catch-up) |

Both cron expressions are evaluated in `Asia/Seoul` via `croniter`.
Cron cannot know KRX holidays — the cycle's market-open guard (`HOLD_SKIP
(market_closed)`) remains the authority on whether a decision actually runs.
| `log_level` | `LOG_LEVEL` | `INFO` |

Derived properties: `metadata_db_path`, `feed_db_path`.
`.streamlit/secrets.toml` is **not** used; OIDC config is passed programmatically.

## 3. Databases

### `metadata.db` (`libs/db/metadata.py`)
```sql
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,                -- ISO8601 KST
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,              -- BUY | SELL
  quantity INTEGER NOT NULL,       -- whole units only
  price REAL NOT NULL,
  order_id TEXT,
  status TEXT NOT NULL,            -- FILLED | REJECTED | PENDING
  strategy TEXT NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL,
  signal TEXT NOT NULL,            -- BUY | SELL | HOLD | HOLD_SKIP
  reason TEXT NOT NULL,
  indicators_json TEXT NOT NULL,   -- adx, d_t, t_t, prices, regime
  executed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS account_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  cash REAL NOT NULL,
  market_value REAL NOT NULL,
  total REAL NOT NULL,
  positions_json TEXT NOT NULL     -- [{symbol, quantity, avg_price, last_price}]
);
CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```
Seed `app_state` with `trader_enabled` from settings on first open.
Provide plain functions: `record_trade`, `record_decision`,
`record_snapshot`, `get_state`/`set_state`, `list_trades`,
`list_decisions`, `list_snapshots`, `latest_snapshot`.
All timestamps KST (`Asia/Seoul`), stored ISO8601 with offset.

### `feed.db` (`libs/db/feed_store.py`)
- `_metadata(name TEXT PRIMARY KEY, source TEXT, source_params_json TEXT,
  start_date TEXT, last_updated_at TEXT, row_count INTEGER)`.
- User feed tables: `ohlcv` shape `(date TEXT PRIMARY KEY, open REAL, high REAL,
  low REAL, close REAL, volume INTEGER)`; `scalar` shape
  `(date TEXT PRIMARY KEY, value REAL)`.
- Functions: `upsert_rows(name, kind, rows)`, `read_table(name)`,
  `list_feeds()`, `get_feed_meta(name)`, `drop_feed(name)` (drops table **and**
  its `_metadata` row — the only delete path), `last_date(name)`.
- Table names sanitized (`^[a-z0-9_]+$`) to prevent SQL injection via feed names.

## 4. DataFeed Layer (`libs/feeds/`)

```python
class DataFeed(pd.DataFrame):
    name: str                    # unique feed.db table name
    kind: str                    # "ohlcv" | "scalar"
    source: str                  # "yfinance" | ...
    source_params: dict          # e.g. {"ticker": "411060.KS"}

    @classmethod
    def load(cls, name: str, feed_db: Path) -> "Self": ...       # from feed.db; empty + meta if absent
    def update(self, *, end: date | None = None) -> None: ...    # fetch missing, upsert, touch _metadata
    def snapshot(self, broker: "BrokerClient") -> pd.Series | None: ...  # same-day bar via broker (mock: last row)
    @property
    def START_DATE(self) -> date | None: ...                     # first row date (None if empty)
```

Concrete feeds (registered in `registry.py`, keyed by `name`):

| name | kind | source | source_params |
|---|---|---|---|
| `ohlcv_0072R0KS` | ohlcv | yfinance | ticker `0072R0.KS` (trading stock) |
| `ohlcv_411060KS` | ohlcv | yfinance | ticker `411060.KS` (proxy gold) |
| `macro_tnx` | scalar | yfinance | ticker `^TNX` (Close) |
| `macro_dxy` | scalar | yfinance | ticker `DX-Y.NYB` (Close) |
| `macro_usdkrw` | scalar | yfinance | ticker `USDKRW=X` (Close) |

- `update()` is idempotent: fetch from `max(last_date, START_DATE_DEFAULT)` →
  today; `START_DATE_DEFAULT = "2022-10-01"` (notebook window start).
- **Same-day problem**: yfinance has no bar for the in-progress KRX session.
  At decision time the trader calls `snapshot(broker)` per feed to append a
  provisional today-row (OHLC = last price, volume 0 for ohlcv; latest price
  for scalar). In mock mode, snapshot returns the latest stored row unchanged.
  Provisional rows are recomputed (upsert-overwritten) on the next `update()`.

## 5. Strategy (`libs/strategy/`)

```python
class Signal(str, Enum): BUY; SELL; HOLD

@dataclass
class MarketView:
    symbol: str
    ohlcv: pd.DataFrame          # trading-stock feed incl. today's snapshot
    macro: pd.DataFrame          # unified frame with delayed TNX/DXY/FX + D_t_{n} cols
    position_quantity: int       # current held units (0 = flat)
    position_entry_price: float | None   # avg entry price from broker; None if flat

# Position state lives here, NOT in DataFeed: the feed is market data only;
# entry price is account state sourced from BrokerClient.get_account().

class Strategy(Protocol):
    def decide(self, view: MarketView) -> tuple[Signal, dict]: ...
    # returns (signal, indicators dict for logging)
```

`gold_ensemble.py` — faithful port of notebook cells 4 & 6:

- Macro score `compute_macro_frame(tnx, dxy, fx)` replicating cell 4 exactly:
  1-day shift (`*_delayed`), `D_t_n = (−tanh(r_tnx/σ_tnx) − tanh(r_dxy/σ_dxy) + tanh(r_fx/σ_fx)) / 3`
  for n ∈ {10, 20, 40, 60}, σ = rolling std of the n-day pct-change.
- `GoldEnsembleStrategy` with **static tuned parameters** (confirmed from the
  notebook's optimization output — Sharpe 1.2181, Train Return 31.59%):
  - Tuned: `n_macro=10`, `k=1.5`, `theta_entry=0.3`
  - Fixed: `n_adx=14, theta_adx=25, n_bb=20, n_short=20, n_long=60,
    theta_exit=-0.2, theta_stop=0.05`
- `decide()` implements `next()` logic with ta-lib indicators:
  - NaN/short-history guard → `HOLD` with reason `warmup`.
  - Regime: `adx < 25` → range (Bollinger mean reversion), else trend (SMA20/60).
  - `T_t` per notebook: range → `T_t=+1` if `P_t < lower and P_t > P_prev`,
    `T_t=−1` if `P_t > upper and P_t < P_prev`; trend → `+1` if
    `ma_short > ma_long and P_t > ma_long`, `−1` if the mirror condition.
  - Entry (flat only): `D_t > theta_entry and T_t == 1`.
  - Exit (any of the 5 notebook conditions, tracked position needed for cond5):
    1. `D_t < theta_exit`
    2. `T_t == -1`
    3. `adx >= theta_adx and (P_t < ma_long or ma_short < ma_long)`
    4. `adx < theta_adx and P_t >= mu` (SMA20 middle band)
    5. stop-loss: `(P_t / entry_price) - 1 < -theta_stop`
  - Returns signal + `indicators` dict `{adx, d_t, t_t, regime, close, lower, upper, mu, ma_short, ma_long}`.
- No broker/network access anywhere in this module. Unit-testable in isolation.

## 6. Broker Layer (`apps/trader/broker/`)

```python
@dataclass
class AccountState:
    cash: float
    positions: list[Position]    # symbol, quantity (int), avg_price, last_price

class BrokerClient(Protocol):
    def get_account(self) -> AccountState: ...
    def get_last_price(self, symbol: str) -> float | None: ...   # for same-day snapshot
    def is_market_open(self) -> bool: ...                        # KrMarketCalendar
    def buy(self, symbol: str, quantity: int) -> Trade: ...
    def sell(self, symbol: str, quantity: int) -> Trade: ...
```

- **TossBroker**: wraps async `tossinvest.TossClient` (credentials from
  settings). Implement with a per-instance event loop or `asyncio.run`.
  Map: balance → `BuyingPowerResponse`, positions → `HoldingsOverview`,
  orders → `OrderCreateRequest` (market, whole units). **Do not live-test
  against the real API.** Keep the module import-light so tests never
  instantiate a real client.
- **MockBroker**: deterministic fills at the last known price (from feeds);
  persists nothing itself — the Trader logs outcomes to `metadata.db`;
  maintains cash/positions in memory seeded from a `positions.json` under
  `DATA_DIR` so the daemon survives restarts. Rejects orders when cash is
  insufficient (exercising the reject path).

## 7. Trader App (`apps/trader/`)

### `trader.py` — one cycle
1. `set_state` guard: read `trader_enabled` from `app_state`; if disabled →
   log `HOLD_SKIP (disabled)` and exit.
2. `is_market_open()`; if closed → `HOLD_SKIP (market_closed)`.
3. `feed.update()` for all registered feeds (idempotent).
4. Build `MarketView`: trading-stock ohlcv + `compute_macro_frame(...)`,
   appending today's snapshots via `feed.snapshot(broker)`.
5. `strategy.decide(view)` → signal + indicators; `record_decision`.
6. Execution (whole-position only):
   - `BUY` and flat → buy with available cash floor(price) shares.
   - `SELL` and holding → sell entire position.
   - Otherwise no order.
7. Execute via `BrokerClient`, `record_trade` (FILLED/REJECTED),
   then `record_snapshot` of post-trade account state.

### `runner.py` — daemon
- Two cron schedules from settings (`decision_schedule_cron`,
  `feed_update_schedule_cron`), evaluated in `Asia/Seoul` with `croniter`:
  compute the earliest next-fire time among both expressions, sleep until it.
- On a decision-schedule fire: run one cycle (the market-open guard inside the
  cycle skips holidays).
- On a feed-update-schedule fire: run `feed.update()` for all feeds only
  (keeps feed.db fresh for the monitor without the monitor doing writes).
- Sleep with small-interval wake (e.g. 30 s) so SIGTERM (Docker stop) is
  handled gracefully within seconds and newly fired schedules are picked up.
- Structured logging to stdout (container-friendly).

### `__main__.py`
`python -m system.apps.trader` → daemon; `python -m system.apps.trader --once`
→ single cycle (exit 0 on success). Container entrypoint = the daemon form.

## 8. Monitor App (`apps/monitor/app.py`)

Streamlit entry (`__main__.py` shells out to
`streamlit run system/apps/monitor/app.py` … adjust to package path; must work
in a container).

### Auth (native Streamlit OIDC)
- Configure Google provider via `st.login("google", ...)` /
  `st.auth.AuthConfig` with `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI` from settings.
- After login: email must be in `AUTHORIZED_USERS` (comma-separated); else
  `st.error` + stop. All tabs render only after the gate.

### Tabs
1. **Overview**: KRX market open/closed (calendar or broker), current balance
   + total value (`latest_snapshot`), current positions, performance metrics
   (total P&L, win rate, profit factor computed from `trades`), latest N
   trades, equity curve chart from `account_snapshots`.
2. **Data Feeds**: table of `_metadata` (name, source, start_date,
   last_updated_at, row_count) + per-row **Delete (drop)** button with
   confirmation. **No update action.**
3. **Feed detail** (selectbox or query param): read-only preview of the table
   (head + row count), metadata, delete button. Drop = table + `_metadata` row.
4. **Trades**: full `trades` table, sortable, with per-trade detail expansion
   (indicators from the paired `decisions` row).
5. **Settings**: toggle bound to `app_state.trader_enabled` (write to
   metadata.db), current broker mode, last trader run time.

## 9. Tests (`tests/`)

- `test_feed_store.py`: upsert idempotency, `last_date`, `drop_feed` removes
  both table and meta row, name sanitization.
- `test_feeds.py`: `update()` fills only the missing range (mock yfinance);
  `load()` on empty db returns empty frame with metadata.
- `test_gold_ensemble.py`: `compute_macro_frame` parity vs a fixture slice
  exported from the notebook; signal decisions on hand-built frames
  (range-regime entry, trend-regime entry, each exit condition, warmup HOLD).
- `test_trader.py`: full cycle with `MockBroker` + temp dbs — buy when flat +
  BUY signal, sell entire position, HOLD_SKIP paths (disabled, market closed),
  insufficient-cash rejection recorded as REJECTED.
- `test_monitor.py`: metric computations (win rate, P&L) against seeded db.
- Async adapter tests with mocked transport only.

## 10. Acceptance Criteria

1. `uv run python -m system.apps.trader --once` runs a full mock cycle against
   freshly created `data/*.db` and exits 0, logging a decision row.
2. Daemon form sleeps and wakes at the configured cron schedule (default
   15:00 KST weekdays); SIGTERM exits cleanly.
3. `uv run streamlit run .../app.py` boots; unauthorized email is blocked;
   authorized email sees all 5 tabs with real db data.
4. Feed delete in monitor drops table + metadata; no write path updates feeds.
5. No code path reaches the real tossinvest API unless `BROKER=toss` is set
   explicitly.
6. `uv run pytest` green; black/isort clean.
