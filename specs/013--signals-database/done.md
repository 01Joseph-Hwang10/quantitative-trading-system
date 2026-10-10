# Done — signals database + `Signals` class + monitor "Signals" page

Implemented per `plan.md` (commit `d3a0fc7`, deployed image `56efc1e-dirty`
— built from the same working tree before the commit, no code difference).

## What shipped

- `system/libs/db/signals_store.py` — signals.db layer mirroring
  `feed_store` (`_metadata` + one scalar table per signal; drop is the
  only delete path).
- `system/libs/signals/` — `Signals(pd.DataFrame)` (with a `_constructor`
  override so `_metadata` attributes survive pandas ops) +
  `DerivedSignal` registry hook; `macro.py` (component scores + `D_t`,
  moved from `gold_ensemble.compute_macro_frame`), `technical.py`
  (`adx_14`, `t_t` series, extracted from `decide()`), `registry.py`
  (`s_tnx`, `s_dxy`, `s_fx`, `d_t_10/20/40/60`, `adx_14`, `t_t`).
- `MarketView.macro` → `MarketView.signals`; `decide()` reads ADX/T_t via
  the shared functions (single source of truth, no behavior change).
- Trader: `update_signals()` after `update_feeds()` on the decision cycle
  and the feed-update cron; `signals_conn` wired through
  `Connections`/`open_connections`/CLI.
- Monitor: "Signals" page (list + detail tabs, chart window selector,
  latest-200 preview, drop) + "Last signal update" freshness line.

## Deviations from plan

- `compute_t_t_series` masks bars as NaN where ADX, the Bollinger mean, or
  the long SMA are undefined (the plan only mentioned ADX warm-up) —
  otherwise bars 27–59 of the backfill would store a spurious `t_t = 0`.
- `Signals` overrides `_constructor` (DataFeed does not) so slicing
  (`tail`, `iloc`) keeps the subclass + custom attributes; the plan's
  acceptance criterion required this and the existing `DataFeed` has the
  same latent gap (left as-is, out of scope).

## Verification

- `pytest`: 67 passed (was 57; +10 new across `test_signals_store.py`,
  `test_signals.py`). `black`/`isort` clean on touched files.
- Local UI smoke test via ego-browser against a throwaway data dir
  (`DATA_DIR=/tmp/...`, mock feeds backfilled by one `update_feeds()` +
  `update_signals()` run): Signals nav entry present, freshness line
  shows "Last signal update", list tab renders the 9-signal metadata
  table, detail tab renders `adx_14`/`d_t_10` (source badge, 1026 rows,
  chart, preview, drop button).
- Deployed with `just update` (rolling, market-hours guard passed):
  Ansible health check reported `Deploy healthy: 56efc1e-dirty running
  (trader, monitor)`; both containers healthy, heartbeat fresh.
- VM check: `data/signals.db` exists with the `_metadata` schema; signal
  tables backfill at the next scheduled feed update (Mon 2026-10-12
  09:00 KST).
- DB sync untouched: only `just db status` (in-sync) was run.
