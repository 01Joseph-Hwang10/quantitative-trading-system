# Implementation Plan — Signals database + `Signals` class + monitor "Signals" page

Source: owner request — add a `signals.db` for derived daily signals (macro
direction score `D_t`, component scores `S_{TNX,t}` / `S_{DXY,t}` / `S_{FX,t}`,
`ADX_t`, technical timing `T_t`, …), introduce `class Signals(pd.DataFrame)`
and refactor the codebase around it, and add a monitor **Signals** page
modeled on the existing **Data Feeds** page.

## 1. Motivation

Today all derived signal values are recomputed on the fly and never stored:

- `D_t` / macro scores: `trader.build_macro_frame()` calls
  `compute_macro_frame()` (gold_ensemble.py) on every cycle; only the last
  value ends up in the decision log.
- `ADX_t` / `T_t`: computed inside `GoldEnsembleStrategy.decide()` from
  `talib` calls; nothing historical is kept.

The monitor cannot show signal history, and the strategy page documents
signals that are invisible anywhere in the UI. A persisted signals layer
gives observability (history, charts), a single source of truth for the
computation (shared by trader and strategy), and a natural home for future
signals.

## 2. Requirements

1. **`signals.db`** — new sqlite database at `data/signals.db`, structured
   like `feed.db`: a `_metadata` table plus one table per signal. Every
   signal is a daily scalar series: `(date TEXT PRIMARY KEY, value REAL)`.
2. **`Signals(pd.DataFrame)`** — a pandas DataFrame subclass mirroring
   `DataFeed`: carries `signal_name` / `signal_source` / `signal_source_params`
   in `_metadata`, and implements `load()` (hydrate from signals.db),
   `persist()` (upsert rows), and `update()` (compute missing range and
   store it; idempotent). Values come from pure compute functions over the
   input feeds — there is no upstream fetch, so `update()` recomputes the
   full series (cheap: ~800 daily rows) and stores only dates after the
   last stored date.
3. **Signal registry** — declarative list of signals: name, source kind,
   input feed names, and compute function. Initial set:
   - `s_tnx`, `s_dxy`, `s_fx` — tanh-normalized macro component scores
     (currently implicit inside `compute_macro_frame`);
   - `d_t_10` (plus `d_t_20`, `d_t_40`, `d_t_60` for notebook parity) —
     macro direction score, mean of the three component scores;
   - `adx_14` — ADX(14) of the trading-stock feed;
   - `t_t` — technical timing series (Bollinger mean reversion in the
     range regime, SMA(20)/SMA(60) trend following in the trend regime).
4. **Single source of truth** — the indicator computations are extracted
   from `compute_macro_frame()` and `GoldEnsembleStrategy.decide()` into
   shared pure functions in the signals package; both the signal store and
   the strategy use them, so persisted values and decision-time values
   cannot drift.
5. **Trader integration** — the decision cycle updates signals (after feeds)
   on the same schedule; first run backfills full history from
   `2022-10-01` (the feed window start). Signals hold completed-session
   values only; the decision-time intraday values (today's snapshot bar)
   keep being computed on the fly by `decide()` via the shared functions.
6. **Monitor "Signals" page** — mirrors Data Feeds: list of stored signals
   (metadata table), a detail tab (line chart with window selector, latest
   200 rows preview, drop button). Read-only on signals.db except
   `drop_signal`; updates are owned by the trader daemon.
7. No behavior change in trading decisions: existing tests pass (with
   mechanical updates for the moved/renamed pieces).

## 3. Design

### 3.1 Storage — `system/libs/db/signals_store.py`

Mirror of `feed_store.py` (same name-validation regex, `_metadata` shape,
transaction style, "only drop deletes" rule):

```
_metadata (name TEXT PRIMARY KEY, source TEXT, source_params_json TEXT,
           start_date TEXT, last_updated_at TEXT, row_count INTEGER)
<signal>  (date TEXT PRIMARY KEY, value REAL)
```

Functions: `connect()`, `get_signal_meta()`, `list_signals()`,
`read_table()`, `last_date()`, `upsert_rows()`, `drop_signal()`. NaN rows
are skipped when persisting (indicator warm-up heads are not stored).

### 3.2 Class — `system/libs/signals/base.py`

```python
class Signals(pd.DataFrame):
    """Derived daily signal loaded from (and persisted to) signals.db."""
    _metadata = ["signal_name", "signal_source", "signal_source_params"]
    SOURCE = "derived"
```

- `__init__(data, *, name, source, source_params)` — same constructor
  pattern as `DataFeed`.
- `name` property; `START_DATE` property (copy of the DataFeed logic).
- `load(conn)` — hydrate in place from signals.db (empty frame with
  registry params if never computed), reusing the in-place re-init trick
  from `DataFeed.load`.
- `persist(conn, rows)` — `signals_store.upsert_rows(...)`.
- `compute(inputs) -> pd.Series` — subclass hook: turns loaded input feeds
  into the signal series (the analog of `DataFeed.fetch_rows`).
- `update(conn_signals, feed_conn) -> int` — load inputs via
  `feeds.registry.load_feed`, `compute()`, keep finite values after
  `signals_store.last_date()`, persist. Returns the number of stored rows;
  idempotent.

### 3.3 Package — `system/libs/signals/`

```
system/libs/signals/
├── __init__.py
├── base.py        # Signals(pd.DataFrame)
├── macro.py       # macro score / D_t computations (from gold_ensemble)
├── technical.py   # ADX / T_t computations (from gold_ensemble)
└── registry.py    # SIGNAL_DEFINITIONS + build_signal()/load_signal()
```

- **`macro.py`** — `compute_macro_frame()` moves here verbatim (it is
  imported by `trader.py` and tests; importers are updated), and is
  refactored so the component scores are exposed as functions:
  `compute_score_tnx/dxy/fx(tnx, dxy, fx)` and
  `compute_d_t(tnx, dxy, fx, window)`. The tanh/eps/rolling logic stays in
  one place; `compute_macro_frame` keeps returning the same wide frame
  (now as a `Signals` instance with name `"macro"`), so `decide()`'s
  `D_t_{n}` column lookup is unchanged apart from the field rename.
- **`technical.py`** — extract from `decide()`:
  `compute_adx_series(ohlcv, n_adx) -> pd.Series` and
  `compute_t_t_series(ohlcv, params) -> pd.Series` (regime switch per the
  documented rules). `decide()` calls these for its last-bar values instead
  of inlining `talib` calls — same math, one implementation.
- **`registry.py`** —
  `SIGNAL_DEFINITIONS: dict[str, SignalDefinition]` where
  `SignalDefinition = (type[Signals]-subclass or factory, inputs, compute)`;
  entries: `s_tnx`, `s_dxy`, `s_fx` (input: the matching `macro_*` feed),
  `d_t_10|20|40|60` (inputs: all three macro feeds), `adx_14`, `t_t`
  (input: `TRADING_STOCK_FEED`). Plus `signal_names()`,
  `build_signal(name)`, `load_signal(conn_signals, conn_feed, name)` —
  mirroring `feeds/registry.py`.

### 3.4 Config & connections

- `config/settings.py`: add `signals_db_path` property →
  `data_dir / "signals.db"`.
- `config/gateways.py`: `Connections` gains a `signals` field.
- `apps/monitor/app.py` `_monitor_connections`: open
  `signals_store.connect(settings.signals_db_path)`.
- `apps/trader/trader.py` + `runner.py`: `Trader.__init__` takes
  `signals_conn`; `run_cycle()` calls `self.update_signals()` right after
  `update_feeds()`; the runner's feed-update schedule also refreshes
  signals (no new cron setting — signals are derived, so they follow the
  feed update).

### 3.5 Strategy / MarketView refactor

- `strategy/base.py`: `MarketView.macro: pd.DataFrame` is renamed to
  `MarketView.signals: pd.DataFrame` — a `Signals` instance (the wide
  macro frame) carrying the `D_t_{n}` columns. Docstrings updated
  (position state still comes only from the broker).
- `trader.py`: `build_macro_frame()` → `build_macro_signals()` returning
  the `Signals` frame (function relocated to `signals/macro.py`); the view
  is constructed with `signals=...`.
- `gold_ensemble.py`: `decide()` reads `view.signals[d_t_column]` (same
  reindex/ffill), and obtains ADX / BB / SMA / T_t via the shared
  functions in `signals/technical.py`. `_STRATEGY_DESCRIPTION`,
  parameters, and decision semantics are untouched.
- `compute_macro_frame` is removed from `gold_ensemble.py`; importers
  (`trader.py`, tests) now import from `system.libs.signals.macro`.

### 3.6 Monitor — "Signals" page

`render_signals(signals_conn)` in `app.py`, registered between Data Feeds
and Trades (`title="Signals"`, `url_path="signals"`, icon
`:material/insights:`). Mirrors `render_data_feeds`:

- caption: signals are computed by the trader daemon from stored feeds;
  the monitor only reads or drops.
- **Signals** tab: metadata table (`name`, `source`, `row_count`,
  `last_updated_at`, compacted `source_params_json`), plus a drop form
  (select + confirmation checkbox) calling `signals_store.drop_signal`.
- **Signal Detail** tab: signal selector (stored ∪ registered names),
  metadata line, window selector (30/90/180/365/All), line chart reusing
  `_scalar_figure` (every signal table has the scalar `value` column),
  latest-200-rows preview, per-signal drop button.
- Sidebar freshness gains "Last signal update" (max `last_updated_at` over
  `signals_store.list_signals`).

### 3.7 Out of scope

- Backfill tooling outside the trader's normal `update()` path (first
  daemon run backfills automatically; a manual `--once` run does too).
- Any change to decision semantics, parameters, or the broker path.
- Persistence of the intermediate raw/delayed macro columns (they already
  live in feed.db as `macro_*` feeds).

## 4. Implementation Order

1. `libs/db/signals_store.py` (+ `tests/test_signals_store.py`, mirroring
   `test_feed_store.py`).
2. `libs/signals/` package: `base.py`, `macro.py` (move + refactor from
   `gold_ensemble.py`), `technical.py` (extract from `decide()`),
   `registry.py` (+ `tests/test_signals.py` covering compute functions
   against the existing `compute_macro_frame`/`decide` fixtures).
3. `config/settings.py` + `config/gateways.py`: `signals_db_path`,
   `Connections.signals`.
4. `strategy/base.py` + `trader.py` + `runner.py`: rename
   `MarketView.macro` → `signals`, wire the signals connection, add
   `update_signals()` to the cycle and the feed-update schedule (update
   `tests/test_trader.py`, `tests/test_runner.py`, `tests/conftest.py`).
5. `apps/monitor/app.py`: Signals page + freshness line (update
   `tests/test_monitor.py`).
6. README repository-layout paragraph: mention `signals.db` and the
   signals package.

## 5. Acceptance Criteria

- [ ] `data/signals.db` exists after a trader cycle, with `_metadata` and
      one scalar table per registered signal; re-running the cycle stores
      no duplicate rows (idempotent `update`).
- [ ] `Signals` behaves like `DataFeed`: survives pandas operations with
      its `_metadata` attributes, hydrates via `load()`, drops cleanly via
      `signals_store.drop_signal`.
- [ ] Persisted `d_t_10` matches the notebook formula
      (`compute_macro_frame` golden values from existing tests); persisted
      `adx_14`/`t_t` match `decide()`'s last-bar values on completed bars.
- [ ] Trader decision path unchanged: same Signal outputs on the existing
      test fixtures; `MarketView.macro` no longer exists.
- [ ] Monitor shows a Signals page equivalent to Data Feeds (list, detail
      chart, preview, drop), and read-only access rules hold.
- [ ] Full `pytest` suite passes; ruff/flake8 clean on touched files.

## 6. Verification

- `pytest` full suite.
- `ruff check` / `py_compile` on touched files.
- Local smoke test: `BROKER=mock` run one trader `--once` cycle, then
  `streamlit` run the monitor and eyeball the Signals page (history charts
  for `d_t_10`, `adx_14`, `t_t`; drop flow on a scratch signal). Record
  the outcome in `done.md`.

Record the outcome in `done.md` after implementation.
