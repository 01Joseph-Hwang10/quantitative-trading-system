# Quantitative Trading System

Initial implementation of a KRX gold-ETF trading system (`GoldEnsembleStrategy`,
ported from `notebooks/strategy_1.ipynb`) with two applications:

- **trader** — long-running daemon that updates data feeds (yfinance → sqlite),
  decides buy/sell/hold at a configurable cron time (default 15:00 KST), and
  executes via a pluggable broker client (`mock` by default; `tossinvest`
  adapter available but never live by default).
- **monitor** — Streamlit dashboard (Google OIDC login + `AUTHORIZED_USERS`
  allowlist) over the same sqlite databases: performance overview, feed
  browsing, trade log, and a trader enable/disable toggle.

## Layout

- `system/apps/trader/` — trader app (cycle, cron runner, broker adapters)
- `system/apps/monitor/` — monitor app (Streamlit)
- `system/libs/` — shared libraries (feeds, strategy, sqlite layers)
- `system/config/` — settings (`.env`) and gateway factories
- `data/` — runtime artifacts: `metadata.db`, `feed.db`, mock broker state
- `specs/` — draft, plan, and the implementation instruction

## Usage

```bash
uv sync

# Trader: single mock cycle (safe — never issues real orders)
uv run python -m system.apps.trader --once

# Trader: daemon (fires cycles on DECISION_SCHEDULE_CRON, Asia/Seoul)
uv run python -m system.apps.trader

# Monitor (requires GOOGLE_CLIENT_ID/SECRET + AUTHORIZED_USERS in .env)
uv run python -m system.apps.monitor
```

Configuration lives in `.env` (see `.env.example`). Broker mode defaults to
`mock`; set `BROKER=toss` only when real execution is intended.

## Tests

```bash
uv run pytest
```
