# Implementation Plan — Strategy page (markdown white paper)

Source: owner request (2026) — add a "Strategy" nav tab to the monitor that
shows a markdown page describing the trading strategy. The content lives with
the strategy implementation, not the monitor: the `Strategy` protocol gains a
`description` property, and each concrete strategy keeps its white paper
alongside its code.

## 1. Requirements

1. Monitor sidebar gains a **Strategy** page with a white-paper icon
   (material icon `description`), positioned between Trades and Settings.
2. The page renders the strategy's markdown description — a white paper
   covering the signal construction, entry/exit rules, parameters, and
   provenance.
3. The content is owned by the strategy class (`Strategy` protocol), so the
   monitor stays strategy-agnostic: it renders whatever the active strategy
   returns. No db access — the page is static.

## 2. Design

- `system/libs/strategy/base.py`: add an optional-less protocol member:

  ```python
  @property
  def description(self) -> str:
      """Markdown white paper of the strategy (rendered by the monitor)."""
      ...
  ```

- `system/libs/strategy/gold_ensemble.py`: reorganize the module into a fixed
  layout so the description is easy to find and edit:

  ```
  [imports]
  [constants]
  _STRATEGY_DESCRIPTION = """
  [strategy description]
  """
  class GoldEnsembleStrategy:
      [implementations]   # description property + decide()
  [helper functions]      # compute_macro_frame, _normalize_index
  ```

  - The former module docstring (notebook provenance, tuned hyperparameters)
    is folded into `_STRATEGY_DESCRIPTION`.
  - `description` is a property returning `_STRATEGY_DESCRIPTION`.
  - No behavior change: `decide`, `compute_macro_frame`, and
    `_normalize_index` are relocated verbatim. `compute_macro_frame` is
    imported by `apps/trader/trader.py` and the tests — module-level, so the
    move below the class is transparent to importers.

- `system/apps/monitor/app.py`: new page + renderer:

  ```python
  st.Page(render_strategy, title="Strategy",
          icon=":material/description:", url_path="strategy")

  def render_strategy() -> None:
      strategy = GoldEnsembleStrategy()
      st.header(f"Strategy: {strategy.name}")
      st.markdown(strategy.description)
  ```

  The monitor imports `GoldEnsembleStrategy` directly (same hard-wiring as
  the trader, which also instantiates it).

## 3. Implementation Order

1. `base.py`: add `description` to the `Strategy` protocol.
2. `gold_ensemble.py`: reorganize to the fixed layout; add `_STRATEGY_DESCRIPTION`
   and the `description` property.
3. `app.py`: add the nav entry and `render_strategy()`.

## 4. Acceptance Criteria

- [ ] Sidebar shows Overview, Performance, Data Feeds, Trades, Strategy,
      Settings.
- [ ] Strategy page renders the white paper markdown (tables, headings) for
      `gold_ensemble`.
- [ ] `Strategy` protocol documents the `description` member; any concrete
      strategy exposes it.
- [ ] No behavior change in the trading path: existing tests pass.

## 5. Verification

- `pytest` full suite.
- `py_compile` + flake8/ruff on the touched files.
- Manual smoke test of the rendered page (Streamlit run) — record outcome in
  `done.md`.

Record the outcome in `done.md` after implementation.
