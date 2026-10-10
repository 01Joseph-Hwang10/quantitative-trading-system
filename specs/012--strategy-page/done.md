# Done — Strategy page (markdown white paper)

Implementation record for `plan.md` (owner request, 2026). Shipped in
`df7ee74`.

## Surface

| File | Change |
|---|---|
| `system/libs/strategy/base.py` | `Strategy` protocol gains a `description` property (`str`, markdown white paper) — documented in the protocol docstring. |
| `system/libs/strategy/gold_ensemble.py` | Module reorganized to the plan's fixed layout: imports → constants → `_STRATEGY_DESCRIPTION` → `GoldEnsembleStrategy` → helper functions. New `description` property returns `_STRATEGY_DESCRIPTION`; the former module docstring (notebook provenance, tuned hyperparameters) is folded into it. `decide`, `compute_macro_frame`, `_normalize_index` moved verbatim — no logic change. |
| `system/apps/monitor/app.py` | Nav gains `st.Page(render_strategy, title="Strategy", icon=":material/description:", url_path="strategy")` between Trades and Settings. New `render_strategy()` renders `st.header(f"Strategy: {strategy.name}")` + `st.markdown(strategy.description)`; static, no db access. |
| `specs/012--strategy-page/` | This spec (`plan.md` + `done.md`). |

The white paper (`_STRATEGY_DESCRIPTION`) covers: overview (macro gate `D_t` ×
technical timing `T_t`, long-only, whole positions), the `D_t` input table
(`^TNX` / `DX-Y.NYB` / `USDKRW=X` tanh-normalized reversal scores, 1-day lag),
the ADX(14) regime switch (Bollinger mean reversion in range, SMA20/60 trend
following in trend), entry rule (`D_t > 0.3` and `T_t = +1`), the five OR-ed
exit triggers, a parameter table (tuned vs notebook defaults), and provenance
(`notebooks/strategy_1.ipynb`, Sharpe 1.2181 / Return 31.59% on the training
range).

## Deviations from the plan

- None functional. The plan's example used `st.header` + `st.markdown`, which
  is what shipped; the icon uses Streamlit's material-icon syntax
  (`:material/description:`) as planned.

## Verification status

- `pytest`: 50 passed (full suite, includes `test_gold_ensemble.py` parity /
  regime / entry / exit tests — unaffected by the reorganization).
- `py_compile` clean on all three touched files; flake8/ruff clean on
  `system/libs/strategy/` and `system/apps/monitor/app.py`.
- Import sanity: `GoldEnsembleStrategy().description` returns the 3,065-char
  markdown document; `compute_macro_frame` still importable from
  `system.apps.trader.trader` and the tests.
- Manual Streamlit smoke test: not yet run — the monitor requires Google OIDC
  credentials and is normally exercised on the deployed instance. Note:
  `app.py` calls `main()` at module level, so it cannot be imported outside
  the Streamlit runtime (pre-existing property, unchanged by this spec).
