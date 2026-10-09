# Done — Deployment: GCP e2-micro VM + Docker + Terraform + Ansible

Implementation record for `instruction.md` (owner decisions in `plan.md`
§0 — not repeated here). Shipped in `ae1eb97` (2026-10-01); follow-ups
`cb1b176` (Artifact Registry keep-latest-5 cleanup policy in Terraform).

## Surface

| File / area | Change |
|---|---|
| `system/apps/trader/runner.py` | Heartbeat: writes `DATA_DIR/trader_heartbeat` on every wake + at startup. |
| `system/apps/trader/__main__.py` | `healthcheck` subcommand — exit 0 iff heartbeat mtime < 10 min old (compose healthcheck). |
| `system/apps/monitor/__main__.py` | Streamlit booted with `--server.address 0.0.0.0 --server.headless true`. |
| `Dockerfile` | Multi-stage (`uv:python3.13-bookworm-slim` builder → `python:3.13-slim`), non-root uid-1000 `app` user; ta-lib via manylinux wheels. `.dockerignore` added. |
| `terraform/` | Project APIs (compute, artifactregistry, oslogin, iap), Artifact Registry (docker, us-central1), static external IP, e2-micro VM (Debian 12, 16 GB pd-balanced, dedicated SA with `artifactregistry.reader` + `logging.logWriter`, `enable-oslogin=TRUE`), firewall: TCP 22 from the IAP range only, owner IAM (`osLogin`, `iap.tunnelResourceAccessor`), outputs `vm_external_ip` / AR URL. `terraform.tfvars` is gitignored (`.example` committed later in `86a9e1b`). |
| `ansible/` | `bootstrap.yml` — apt base + 2 GB swapfile, Docker Engine + compose plugin, `/opt/quantitative-trading` owned by uid 1000; `deploy_tasks.yml` — `.env.production` → VM (mode 0600), renders `docker-compose.yml.j2`, `current_tag`/`previous_tag` bookkeeping, metadata-server-token `docker login`, compose pull/up; `update.yml` / `rollback.yml`; IAP-based inventory pointing at `127.0.0.1:2222`. |
| `justfile` | `build` (sha + `-dirty` tags), `push` (oauth2accesstoken login), `provision` (terraform + inventory regeneration), `deploy`, `update` (KRX 09:00–15:30 market-hours guard, `--force` override), `rollback`, `status`, `logs`, `tunnel`. All GCP recipes are shebang recipes under `ctx use quantitative-trading`. |
| `tests/test_runner.py` | **New** — heartbeat written per wake; healthcheck exit codes (fresh/stale/missing). |
| `.env.example` | Documents `BROKER=toss` real-order semantics; `GOOGLE_REDIRECT_URI` stays `localhost:8501` (SSH-tunnel friendly). |

## As-built deviations from the instruction

1. **No Google Cloud SDK on the VM** — the GCE image's unsigned
   `google-cloud.list` repo conflicts with a signed one; AR pulls instead
   authenticate via the VM service-account token from the metadata server
   (`docker login -u oauth2accesstoken` before each compose pull).
2. **`just push` cross-builds** — `docker buildx build --platform
   linux/amd64 --push` (dev machine is arm64; a native build produced
   `exec format error` on the VM).
3. **No automatic rollback** — as decided in `plan.md` §0.8: a failed
   `just update` prints the failed task + container logs and points at
   `just rollback` (previous tag preserved in `previous_tag`).

## Verification status

Deployed to the real project on 2026-10-01: `just provision` / `deploy`
brought up both containers healthy (trader heartbeat, monitor on
`127.0.0.1:8501` only), `just tunnel` + browser login verified, market-hours
update guard verified. Later `just update` rollouts (specs 005–009) exercise
the pipeline continuously. Live acceptance deliberately avoided triggering a
real (`BROKER=toss`) decision cycle.
