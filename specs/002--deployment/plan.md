# Deployment Plan — GCP VM + Docker + Terraform + Ansible

Source: `specs/002--deployment/draft.md`
Project: `quantitative-trading-510302` (confirmed).

## 0. Resolved Decisions (from owner)

1. **Region**: `us-central1` for everything — VM (zone `us-central1-a`),
   Artifact Registry, and static IP.
2. **Project**: `quantitative-trading-510302` (confirmed).
3. **VM OS**: Debian 12 + Ansible-installed Docker (not COS).
4. **Monitor exposure**: no public port. Monitor binds to `127.0.0.1:8501` on
   the VM; users reach it via SSH port-forwarding
   (`ssh -L 8501:localhost:8501`), so the existing
   `http://localhost:8501/oauth2callback` OIDC redirect works unchanged.
5. **SSH access**: pure IAM — OS Login enabled on the instance + IAP-tunneled
   SSH (firewall allows TCP 22 only from the IAP range `35.235.240.0/20`). The
   deploying user gets `roles/compute.osLogin` and
   `roles/iap.tunnelResourceAccessor` via Terraform.
6. **Secrets**: Ansible copies the local `.env` to the VM (never committed).
7. **Update guard**: `just update` rejects during KRX market hours
   (09:00–15:30 KST, weekdays); `--force` overrides.
8. **Rollback**: **manual** `just rollback` only. `just update` records the
   previous tag on the VM and, on failure, exits with the error information
   and a pointer to `just rollback` (no automatic rollback).
9. **Image strategy**: one image, two containers via compose command
   overrides.
10. **Production broker**: the VM's `.env` uses **`BROKER=toss` — the trader
    will place REAL orders at 15:00 KST on trading days.** Deployment
    acceptance must not trigger accidental orders (see Ground Rules in
    `instruction.md`).

## 1. Goal

Run the trading system in production on a single GCP e2-micro VM (16 GB disk,
static external IP): two Docker containers — `trader` (daemon) and `monitor`
(Streamlit) — built from one image pushed to a provisioned Artifact Registry.
Provisioning with Terraform, VM configuration + deploy with Ansible, and
day-2 operations (`just update` with market-hours guard + manual rollback)
via just.

## 2. Target Architecture

```
[local machine]
  just build/push ──▶ Artifact Registry (us-central1)
  terraform apply ──▶ VM + static IP + AR + firewall + IAM
  ansible-playbook ──▶ VM (via IAP): docker, compose stack, .env
  just update ───────▶ guarded build+push+pull+restart
  just rollback ─────▶ redeploy previous tag (manual)

[GCP VM e2-micro, us-central1-a, 16GB pd-balanced]
  docker compose:
    - trader  (python -m system.apps.trader, restart unless-stopped,
               heartbeat-file healthcheck)
    - monitor (streamlit, published on 127.0.0.1:8501 only)
  shared ./data bind mount → metadata.db + feed.db + mock state
  access: SSH via IAP (OS Login); monitor via ssh -L 8501:localhost:8501
```

### Terraform (`terraform/`)
- API enablement: compute, artifactregistry, oslogin, iap.
- `google_artifact_registry_repository` — docker, `us-central1`.
- `google_compute_address` — static external IP (`us-central1`).
- `google_compute_instance` — e2-micro, 16 GB pd-balanced, Debian 12
  (`debian-cloud/debian-12`), dedicated service account with
  `roles/artifactregistry.reader` + `roles/logging.logWriter`, metadata
  `enable-oslogin=TRUE`.
- IAM for the deploying user (variable): `roles/compute.osLogin`,
  `roles/iap.tunnelResourceAccessor`.
- Firewall: allow TCP 22 **only** from `35.235.240.0/20` (IAP range). No rule
  for 8501 (monitor is SSH-forwarded only).
- Outputs: static IP, AR repo URL.
- State: local (gitignored); optional GCS backend documented for later.

### Docker
- Multi-stage `Dockerfile` at repo root: `python:3.13-slim` + `uv`
  (`uv sync --frozen --no-dev`; ta-lib >= 0.7 ships manylinux wheels bundling
  the C library — no source build; fallback documented), non-root `app` user.
- `docker-compose.yml` rendered on the VM by Ansible:
  - `trader`: `python -m system.apps.trader`, `restart: unless-stopped`,
    heartbeat-file healthcheck,
  - `monitor`: streamlit, published on **`127.0.0.1:8501:8501`** only,
  - shared `./data` bind mount (metadata.db, feed.db, state).
- Tags: `us-central1-docker.pkg.dev/quantitative-trading-510302/<repo>/app:<git-sha>`
  and `:latest`.

### Ansible (`ansible/`)
- `inventory.ini` + `ansible.cfg` — VM external IP, SSH via **IAP ProxyCommand**
  (`gcloud compute start-iap-tunnel`), OS Login user.
- `bootstrap.yml` — apt base packages, 2 GB swap (e2-micro has 1 GB RAM),
  Docker Engine + compose plugin, Google Cloud SDK +
  `gcloud auth configure-docker` (VM SA pulls from AR via metadata tokens),
  `/opt/quantitative-trading` app dir with `data/` subdir.
- `deploy.yml` — sync local `.env` → VM (mode 600), render `docker-compose.yml`
  with the image tag, record `current_tag`/`previous_tag` files, compose up.
- `update.yml` — pre-flight market-hours guard, pull, up -d, post-check
  container states; **on failure: print error info + hint `just rollback`
  (no automatic rollback)**.
- `rollback.yml` — redeploy using the tag recorded in `previous_tag`.

### justfile (repo root)
All GCP-touching recipes run inside `ctx use quantitative-trading` (shebang
recipes; per AGENTS.md).
- `just build` — docker build, tag `:latest` + `:<git-sha>`
- `just push` — token-based `docker login` + push both tags to AR
- `just provision` — `terraform init/apply`; writes the VM IP into the
  Ansible inventory
- `just deploy` — bootstrap.yml then deploy.yml (first install)
- `just update [--force]` — market-hours guard → build → push → update.yml
- `just rollback` — rollback.yml (previous tag)
- `just status` / `just logs <service>` — inspect via SSH
- `just tunnel` — `gcloud compute start-iap-tunnel` + local
  `ssh -L 8501:localhost:8501` helper for the monitor

## 3. Required App Changes (discovered during planning)

1. **Trader heartbeat**: the runner loop touches `data/trader_heartbeat` each
   wake so the compose healthcheck can verify liveness (the daemon has no HTTP
   endpoint).
2. **Monitor bind/host settings**: run streamlit with
   `server.address=0.0.0.0` inside the container (compose publishes to
   127.0.0.1 on the host), `server.headless=true`.
3. **`.env.example` additions**: `GOOGLE_REDIRECT_URI` stays
   `http://localhost:8501/oauth2callback` (works through the SSH tunnel);
   document `BROKER=toss` production semantics.

## 4. First-Deploy Runbook (order matters)

1. `ctx use quantitative-trading`; verify billing enabled on the project.
2. `just provision` — Terraform creates AR, static IP, VM, firewall, IAM.
3. `just build && just push` — image lands in AR.
4. `just deploy` — Ansible bootstraps Docker, syncs `.env` (with
   `BROKER=toss` + real TOSSSEC + Google OAuth creds), starts compose.
5. `just tunnel` — port-forward 8501; verify monitor login + tabs.
6. `just status` — both containers healthy; trader daemon sleeping until
   `DECISION_SCHEDULE_CRON`.
7. `just update` during market hours → must REJECT; with `--force` → succeeds.
8. `just rollback` → returns to the previous tag.

## 5. Remaining Open Points (non-blocking, defaults chosen)

- **KRX holidays**: the local market-hours guard checks weekday + time window
  only (the draft's "e.g. 09:00–15:30 KST"). The trader daemon itself still
  uses the broker market calendar; the guard is a safety net for deploys.
- **Terraform state**: local backend for now; migrate to GCS when a second
  operator appears.
- **AR repo name**: `quantitative-trading` (single repo, `app` image).
