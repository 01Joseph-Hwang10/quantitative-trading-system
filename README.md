# Quantitative Trading System

An algorithmic trading system for the KRX market, currently running the
**GoldEnsembleStrategy** (macro-gated gold ETF timing strategy, ported from
`notebooks/strategy_1.ipynb`). It consists of two applications:

- **trader** — a long-running daemon that maintains local market-data feeds,
  decides buy/sell/hold once per trading day (default 15:00 KST, configurable
  via cron), and executes whole-position trades through a pluggable broker
  client (`mock` for testing, `tossinvest` for live execution).
- **monitor** — a Streamlit dashboard (Google OIDC login + email allowlist)
  over the same local databases: performance overview, data-feed browsing,
  trade log, and a trader enable/disable switch.

## Repository Layout

```
system/
├── system/                      # Python package
│   ├── apps/
│   │   ├── trader/              # decision cycle, cron runner, broker adapters
│   │   │   ├── trader.py        # one cycle: feeds → strategy → broker → logs
│   │   │   ├── runner.py        # daemon loop (croniter, Asia/Seoul)
│   │   │   ├── __main__.py      # CLI: daemon / --once / healthcheck
│   │   │   └── broker/          # BrokerClient protocol + mock/tossinvest
│   │   └── monitor/             # Streamlit UI (auth, tabs)
│   ├── libs/
│   │   ├── feeds/               # DataFeed (pd.DataFrame subclass) + registry
│   │   ├── strategy/            # Strategy protocol + GoldEnsembleStrategy
│   │   └── db/                  # metadata.db and feed.db layers (sqlite)
│   └── config/                  # Settings (.env) + logging setup + gateway factories
├── terraform/                   # GCP: API enablement, VM, Artifact Registry, firewall, IAM
├── ansible/                     # VM bootstrap + deploy/update/rollback
├── tests/                       # pytest suite
├── Dockerfile / justfile        # image build + operational entrypoints
└── specs/                       # draft/plan/instruction per work phase
```

## Setup

### Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker (for local image builds and production)
- For deployment only: `gcloud`, `terraform`, `ansible-playbook`, `just`

### Local installation

```bash
uv sync                     # install locked dependencies (dev group included)
uv run pytest               # 38 tests, all should pass
```

### Environment files

Two env files, both **gitignored** — never commit real values:

| File | Purpose |
|---|---|
| `.env` | Local development. `BROKER=mock` — can never place real orders. |
| `.env.production` | Synced to the VM by Ansible. `BROKER=toss` — **real orders**. |

Start from `.env.example` and fill in: `TOSSSEC_CLIENT_ID/SECRET` (broker),
`GOOGLE_CLIENT_ID/SECRET` (monitor login), `AUTHORIZED_USERS` (comma-separated
emails allowed into the monitor).

`GOOGLE_CLOUD_PROJECT` (GCP Logging) is **not** user-managed — docker compose
injects it on the VM only, so local runs keep stdout-only logging.

### Run locally (mock broker)

```bash
uv run python -m system.apps.trader --once   # one full decision cycle (mock)
uv run python -m system.apps.trader          # daemon (waits for the schedule)
uv run python -m system.apps.monitor         # dashboard on http://localhost:8501
```

The local `BROKER=mock` setup fills orders at the latest stored close and
persists state in `data/mock_positions.json`; nothing ever reaches a real
broker.

## Where to Modify What

| You want to… | Edit |
|---|---|
| **Trade a new strategy (the normal workflow)** | Create `system/libs/strategy/<your_strategy>.py` implementing `decide(view) -> (Signal, dict)` from `system/libs/strategy/base.py`, then select it in `system/apps/trader/trader.py` |
| Change strategy logic, thresholds, or parameters | Prefer a **new** strategy module over editing `gold_ensemble.py` (see "Strategy notes" below) |
| Add/change data feeds (symbols, sources) | Register in `system/libs/feeds/registry.py`; feed classes live in `system/libs/feeds/` (`OhlcvFeed`, `ScalarFeed`, or a new `DataFeed` subclass) |
| Change position sizing / execution rules | `Trader.execute()` in `system/apps/trader/trader.py` |
| Add a broker backend | Implement `BrokerClient` from `system/apps/trader/broker/base.py`; select via `BROKER` env |
| Change trading schedule | `.env`: `DECISION_SCHEDULE_CRON`, `FEED_UPDATE_SCHEDULE_CRON` (cron, evaluated in `Asia/Seoul`) |
| Add monitor pages/metrics | `system/apps/monitor/app.py` (tabs) and `system/apps/monitor/support.py` (metrics) |
| Change infrastructure (VM size, region, firewall) | `terraform/` (variables in `terraform/terraform.tfvars`) |
| Change VM setup or deploy steps | `ansible/bootstrap.yml`, `ansible/deploy_tasks.yml` |
| Change the runtime image | `Dockerfile` |

### Strategy notes

**This repository expects new strategies to be implemented as new modules, not
edits to `gold_ensemble.py`.** That file is a frozen, parity-tested port of
`notebooks/strategy_1.ipynb` — it is the reference implementation whose
behavior is pinned by `tests/test_gold_ensemble.py` (macro-frame parity,
entry/exit conditions). Tweaking it in place would silently invalidate the
tests and the documented backtest results. Instead:

1. Prototype and tune in a notebook under `notebooks/` (as `strategy_1.ipynb`
   was), and validate in- and out-of-sample against benchmarks.
2. Create `system/libs/strategy/<name>.py` implementing the `Strategy`
   protocol: `decide(view: MarketView) -> tuple[Signal, dict]` — pure decision
   logic, no broker or network access.
3. Point the Trader at it in `system/apps/trader/trader.py` (the strategy
   instance is the single wiring point).
4. Add tests: parity for any signal math you port, plus decisions on
   hand-built frames (entries, each exit, warmup).

For reference, the incumbent strategy:

- **Macro gate** `D_t` — tanh-normalized reversal scores over delayed
  TNX / DXY / USDKRW (computed in `compute_macro_frame`; a 1-day lag prevents
  look-ahead bias).
- **Technical timing** `T_t` — ADX regime switch: ADX < 25 → Bollinger
  mean-reversion, else SMA(20)/SMA(60) trend-following.
- Entry (flat): `D_t > theta_entry` **and** `T_t == +1`. Exits (any of 5):
  macro reversal, timing reversal, trend breakdown, range reversion target,
  stop-loss vs. entry price.
- Tuned parameters are static: `n_macro=10, k=1.5, theta_entry=0.3`
  (optimization output from the notebook).

## Deployment (GCP)

Production is a single e2-micro VM (`us-central1-a`, 16 GB disk, static IP)
running two containers from one image, provisioned with Terraform and
configured/deployed with Ansible over IAP-only SSH.

```bash
just provision        # terraform apply + generate ansible inventory (once)
just build            # build the image, tag :latest + :<git-sha>
just push             # build linux/amd64 and push to Artifact Registry
just deploy true      # first install: bootstrap VM + deploy current build
just update           # guarded rolling update (build → push → restart)
just update --force   # same, bypassing the market-hours guard
just rollback         # manual rollback to the previous image tag
just status           # container states + trader heartbeat
just logs trader      # tail logs (or: just logs monitor)
just tunnel           # port-forward the monitor to localhost:8501
```

#### Accessing the monitor (HTTPS)

The monitor is also served through a Cloud Run nginx reverse proxy
(`terraform/main.tf` → `google_cloud_run_v2_service.monitor_proxy`), which
gives Google OAuth a valid HTTPS redirect domain:

- **URL:** `terraform output monitor_proxy_url` (e.g.
  `https://quantitative-trading-monitor-<hash>-uc.a.run.app`) — log in with an
  `AUTHORIZED_USERS` email.
- Uses the stock nginx image with an in-line config (no Dockerfile, no extra
  infra); it proxies to the VM's static IP on port 8501 (firewall rule
  `quantitative-trading-monitor-8501`).
- It is fire-and-forget: `just update` touches only the VM; an IP change
  propagates on the next `terraform apply`.
- End-to-end health check: `curl https://<proxy-url>/_stcore/health` → `ok`.
  (`/healthz` is intercepted by Google's run.app frontend.)
- One-time console config (done 2026-10): OAuth client redirect URI
  `https://<proxy-url>/oauth2callback`, consent-screen authorized domain
  `https://<proxy-url>`, branding homepage + privacy policy
  (`/privacy`, served by the proxy), consent screen **published to
  production**. Console changes propagate in 5 min – a few hours.

- `just update` **rejects during KRX market hours** (weekdays 09:00–15:30 KST);
  `--force` overrides. Failed updates keep the old version running and print
  the error plus a rollback hint — rollback is always manual.
- All GCP recipes activate the `quantitative-trading` IAM context via
  `ctx use` automatically.
- **Logs are shipped to GCP Logging** in production: both containers attach a
  Cloud Logging handler when compose injects `GOOGLE_CLOUD_PROJECT`, writing
  with the VM service account (`roles/logging.logWriter`) — no Ops Agent
  needed on the 1 GB VM. Logs still stream to stdout (`just logs trader`).
  Each service carries a `service` label (`trader` / `monitor`) so its logs
  can be filtered independently in the Logs Explorer. In the links below,
  replace `<PROJECT_ID>` with your GCP project ID (`project_id` in
  `terraform/terraform.tfvars`):

  | View | Logs Explorer link |
  |---|---|
  | Trader | `https://console.cloud.google.com/logs/query;query=labels.service=trader?project=<PROJECT_ID>` |
  | Monitor | `https://console.cloud.google.com/logs/query;query=labels.service=monitor?project=<PROJECT_ID>` |
  | Both services | `https://console.cloud.google.com/logs/query;query=labels.service=trader%20OR%20labels.service=monitor?project=<PROJECT_ID>` |

  If a deep link misbehaves, open the Logs Explorer for your project
  (`https://console.cloud.google.com/logs/query?project=<PROJECT_ID>`) and
  paste the filter (e.g. `labels.service="trader"`) into the query editor,
  or verify from the terminal:

  ```bash
  gcloud logging read 'labels.service="trader"' \
    --project <PROJECT_ID> --limit 5
  ```
- Terraform state is local (`terraform/*.tfstate`, gitignored).

### Generated & environment-specific files

| File | What it is |
|---|---|
| `terraform/terraform.tfvars` | **Your** deployment values: `project_id`, `region`/`zone`, `owner_email` (the Google account that gets OS Login + IAP roles). Not secrets, but environment-specific — gitignored; copy `terraform/terraform.tfvars.example` and fill in your own values. |
| `ansible/inventory.ini` | **Generated** by `just provision` from Terraform outputs — do not edit by hand. It points Ansible at the VM through the IAP tunnel (`127.0.0.1:2222`), records the OS-Login username resolved from `gcloud compute os-login describe-profile`, and references `~/.ssh/google_compute_engine` as the SSH key. Gitignored; regenerate with `just provision`. |
| `.streamlit/secrets.toml` | Materialized from env vars at monitor startup (see `.gitignore`). |

The first `just provision` also performs a warm-up `gcloud compute ssh` that
registers your machine's key with OS Login — expect the very first run to
create the key and ask nothing interactively.

## Notes & Warnings

1. **`BROKER=toss` places real orders.** The production VM runs with
   `BROKER=toss` and will trade at 15:00 KST on trading days. Locally, keep
   `.env` at `BROKER=mock`. Never test with a production env file.
2. **Trader enable/disable** is a runtime switch (monitor → Settings tab, or
   `app_state.trader_enabled` in `metadata.db`). The daemon checks it every
   cycle; disabling skips decisions without stopping the daemon.
3. **Monitor access** has two paths: the Cloud Run HTTPS proxy (primary; see
   *Deployment → Accessing the monitor*) and SSH-port-forwarding
   (`just tunnel`). The OAuth redirect URI is
   `https://quantitative-trading-monitor-<hash>-uc.a.run.app/oauth2callback`
   in `.env.production`; `http://localhost:8501/oauth2callback` remains
   registered on the OAuth client for tunnel use. Never "simplify" either to
   the VM's bare IP — Google rejects raw-IP redirect URIs. Only emails in
   `AUTHORIZED_USERS` get past the login gate.
4. **Same-day data**: yfinance publishes daily bars only after the session
   closes. At decision time the trader appends a provisional same-day bar from
   the broker's price API (mock mode: replays the last stored bar). It is
   overwritten by the real bar on the next `update()`.
5. **Feeds are trader-owned**: the monitor can read feeds and drop whole feed
   tables — it can never update feed rows. Updates happen in the daemon (on
   schedule and before every decision).
6. **Holidays**: the local update guard knows weekdays only; the trader daemon
   itself checks the KRX market calendar before every decision, so holidays
   result in a logged `HOLD_SKIP (market_closed)` instead of a trade.
7. **Secrets**: `.env`, `.env.production`, terraform state, and the generated
   `.streamlit/secrets.toml` are all gitignored. The VM's `.env` is root-only
   (mode 0600).
8. **Databases** live in `data/` on the VM (bind-mounted into both containers):
   `metadata.db` (trades, decisions, account snapshots, app state) and
   `feed.db` (market data). Back them up before destructive experiments.
9. **Scale**: everything is sized for the e2-micro (1 GB RAM + 2 GB swap).
   Adding heavier strategies or more feeds — resize the VM first
   (`vm_machine_type` in `terraform/terraform.tfvars`).

## Development

```bash
uv run pytest                          # tests
uv run black system tests              # format
uv run isort system tests              # sort imports
```

### Local DB sync (`just db`)

Two-way sync of `data/metadata.db` and `data/feed.db` with the VM — the
closest safe equivalent to a gcsfuse-style mount (SQLite is never mounted or
opened over the network; files are copied whole over IAP SSH with sha256
verification, timestamped backups, and a last-synced fingerprint in
`data/.db-sync-state.json`). See `specs/008--db-sync/plan.md`.

```bash
just db             # usage + status (local vs remote sha/mtime verdicts)
just db sync        # two-way converge (refused during KRX market hours)
just db pull        # VM -> local
just db push        # local -> VM (market-hours guarded)
just db watch       # near-live mirroring: fswatch + 30s poll
```

Safety rails: `sync`/`push` refuse while the KRX market is open (`--force`
overrides; `watch` warns instead of refusing); divergence never auto-merges —
pick a winner with `just db pull <db> --overwrite` or `just db push <db>
--force`; the replaced file is always backed up (last 5 kept, in
`data/backups/` locally and `data/backups/` on the VM).

Design decisions and phase-by-phase instructions live in `specs/`:
`001--initial-implementation` (app), `002--deployment` (infrastructure),
`003--readme` (this README), `004--ui-improvement` (monitor UI), and
`005--gcp-logging` (Cloud Logging integration) — each with a `plan.md`
(implementation record; `001`–`004` also keep the owner's original
`draft.md`/`instruction.md`).
