# Implementation Plan — OAuth HTTPS proxy (Cloud Run nginx → VM monitor)

Authoritative plan for `specs/010--oauth-https-proxy`. Infra baseline:
`specs/002--deployment` (VM runs both containers; monitor binds
`127.0.0.1:8501`; SSH-tunnel access).
Status: **implemented** (2026-10-09) — deployed and verified live. Known
run-time deviations from the original plan, all discovered during deploy:

- Cloud Run requires ≥512 Mi with CPU-always-allocated (256 Mi rejected) —
  the service runs 1 vCPU / 512 Mi, free tier still covers it.
- Google's run.app frontend intercepts `/healthz` (404, never reaches the
  container); end-to-end health check is Streamlit's `/_stcore/health`.
- The monitor container had to be re-published from `127.0.0.1:8501` to
  `0.0.0.0:8501` (`ansible/templates/docker-compose.yml.j2`) — the firewall
  rule alone was not enough.
- Production publishing requires a public privacy-policy URL (app name,
  support email, homepage URL, privacy policy URL); the proxy serves a
  minimal one at `/privacy` — no extra infrastructure.

Verified live (2026-10-09): full Google login through the proxy →
authenticated dashboard (allowlist pass, websockets working);
`/_stcore/health` → 200 via both the proxy and the IAP tunnel.

## 0. Ground rules

- **`BROKER=toss` in production means REAL orders.** Never start the trader,
  never touch trader-side state. This spec only affects the monitor's network
  path and OAuth configuration.
- All GCP-touching commands run under `ctx use quantitative-trading`
  (shebang recipes in the justfile call it first — per AGENTS.md).
- Follow existing code style (black line-length 120, isort, no abbreviations).
- The SSH-tunnel access path (`just tunnel` → `localhost:8501`) must keep
  working unchanged; this spec is additive.

## 0b. Owner decisions (binding — confirmed, final)

1. **Deploy surface** — Deploy via **Terraform**: a single
   `google_cloud_run_v2_service` resource in `terraform/main.tf`, applied
   through the existing `just provision` flow under `ctx use
   quantitative-trading`. The container is the **stock nginx image** — no
   Dockerfile, no custom image, no Artifact Registry push, no justfile
   changes. After the first deploy the proxy is **never touched again**:
   `just update` keeps updating only the underlying Compute Engine VM (no
   behavior changes for the proxy), and the proxy target is the VM's
   **static** external IP (already Terraform-managed via
   `google_compute_address.vm`, interpolated into the config), so an IP
   change propagates on the next (rare) `terraform apply` without manual edits.
2. **Cloud Run → VM hop** — Plaintext public-internet hop is **tolerable**
   (unchanged): VM firewall opens TCP 8501 to `0.0.0.0/0`, nginx proxies over
   HTTP; Streamlit's own auth gates the app. Hardening path in §5 below.
3. **Consent screen publishing** — **Publish to production.** Scopes are
   OIDC-only (`openid email`) — non-sensitive; the `AUTHORIZED_USERS` allowlist
   still restricts logins; brand verification is not required for this use.
4. **Naming** — Cloud Run service: **`quantitative-trading-monitor`**.
   No Artifact Registry image exists (stock nginx image is used directly).
5. **Cost** — `min-instances 0`, `max-instances 1` is good. Cold starts
   acceptable; expected cost ≈ $0 (free tier covers a single-user dashboard).
6. **`nginx.conf` in-line, no auxiliary resources** — the nginx configuration
   is written **in-line in the container's startup command** (shell heredoc
   inside the Terraform `args`, validated with `nginx -t` before start),
   against the stock `docker.io/library/nginx:1.27-alpine` image. **No
   Dockerfile, no `cloudrun-proxy/` directory, no Google Cloud Storage, no
   Secret Manager, no other auxiliary resource**: the Terraform side is the
   Cloud Run service (+ its IAM member, the `run` API enable, and the VM-side
   firewall rule); everything the proxy needs lives inside the service
   resource itself.

## 1. Background — why this is plausible (verified 2026-10)

Google OAuth blocks production use of the current setup:

- Authorized redirect URIs must be **HTTPS** and **cannot be raw IP
  addresses** (localhost exempt). Source: Google support
  `support.google.com/cloud/answer/15549257` ("OAuth clients").
- Cloud Run's default domain **satisfies every rule**: `https://...run.app`
  is HTTPS, not a raw IP, and `*.run.app` is an entry on the
  [Public Suffix List](https://publicsuffix.org/list/) (verified in the PSL
  data file), so the full service URL is a valid "top private domain" for the
  OAuth consent screen's Authorized domains — no Search Console domain
  verification of a run.app URL is possible or needed. Google's own IAP for
  Cloud Run flow uses run.app URLs as authorized redirect URIs.

So: deploy a minimal nginx container to **Cloud Run** that reverse-proxies
everything to the VM's public IP; Google OAuth sees a valid HTTPS domain,
users see `https://quantitative-trading-monitor-<hash>-uc.a.run.app`, and the
Streamlit app on the VM is unchanged.

## 2. Architecture

```
Browser ──HTTPS──▶ Cloud Run (nginx, run.app domain)
                        │ proxy_pass http://
                        ▼ (public internet, plaintext hop)
                   VM :8501 ──▶ monitor container (Streamlit, unchanged)
                                   │ Google OIDC redirect_uri =
                                   │ https://<run-app-url>/oauth2callback
                                   ▼ local data (SQLite/parquet)
```

- Google OAuth only ever talks to the browser and `https://…run.app` — valid
  redirect URI (HTTPS, non-IP, `*.run.app` on the Public Suffix List).
- Streamlit's `redirect_uri` is taken from `GOOGLE_REDIRECT_URI` env (existing
  plumbing in `system/config/settings.py` + `system/apps/monitor/support.py`),
  so switching from `localhost:8501` to the run.app URL is config-only.

## 3. Deliverables

### 3.1 Terraform (`terraform/main.tf` — the whole feature)

Everything lives in `main.tf`; no new directories, no justfile changes.

- **`google_project_service.run`** — `run.googleapis.com` API enable (same
  pattern as the existing compute/artifactregistry entries).
- **`google_cloud_run_v2_service.monitor_proxy`** — service
  `quantitative-trading-monitor`, region `us-central1`,
  `deletion_protection = false`, `ingress = INGRESS_TRAFFIC_ALL`:
  - Container: stock `docker.io/library/nginx:1.27-alpine` (pinned, fully
    qualified), `container_port = 8080`, 1 vCPU / 512 Mi (Cloud Run's floor
    for CPU-always-allocated instances; free tier still covers it),
    `command = ["/bin/sh"]` and an inline startup script in `args`:
    write the nginx config via heredoc → `nginx -t` → `exec nginx -g
    'daemon off;'`. The config interpolates
    `${google_compute_address.vm.address}` (static VM IP, from Terraform
    state — never hardcoded) and proxies to port `8501`.
  - The in-line config is a single server block:
    - `proxy_pass http://<vm-ip>:8501;`
    - WebSocket upgrade support (Streamlit is websocket-first):
      `map $http_upgrade $connection_upgrade` + `proxy_http_version 1.1`,
      `Upgrade`/`Connection` hop-by-hop headers.
    - Forwarded headers: `Host`, `X-Real-IP`, `X-Forwarded-For`,
      `X-Forwarded-Proto` (passthrough — Cloud Run terminates TLS),
      `X-Forwarded-Port 443`.
    - `proxy_connect_timeout 10s`; `proxy_read_timeout`/`proxy_send_timeout`
      3600s (idle dashboards hold the websocket for the full Cloud Run cap);
      `proxy_buffering off` for streamed responses;
      `client_max_body_size 10m` — dashboard only.
    - `location = /healthz` returns 200 locally.
  - `template.timeout = "3600s"` (Cloud Run's max — websockets are long-lived
    requests) and `scaling { min_instance_count = 0, max_instance_count = 1 }`
    (§0b.5).
- **`google_cloud_run_v2_service_iam_member.public`** — `roles/run.invoker`
  → `allUsers` (required: the v2 service is private by default; Streamlit
  enforces Google OIDC + the email allowlist itself).
- **`google_compute_firewall.monitor_proxy`** — allow TCP 8501 ingress to the
  VM tag from `0.0.0.0/0` (per §0b.2; Cloud Run egress IPs are not stable).
  The SSH-via-IAP rule stays untouched.
- **`outputs.tf`** — `monitor_proxy_url` = the service URI (used for the
  OAuth console config and the redirect URI on the VM).

### 3.2 OAuth / monitor configuration changes

- `.env.production` on the VM (via Ansible, no file-format change):
  `GOOGLE_REDIRECT_URI=https://<run-app-url>/oauth2callback`.
  `system/config/settings.py` already plumbs this into
  `.streamlit/secrets.toml` `[auth].redirect_uri` — **no code change**.
- Google Cloud console (manual, one-time, documented in this spec and the
  repo README):
  1. OAuth client → add Authorized redirect URI
     `https://<run-app-url>/oauth2callback`.
  2. OAuth consent screen → Authorized domains → add the run.app URL.
  3. Publishing status → **publish to production** (binding per §0b.3).
- Console changes take 5 min – a few hours to propagate (per Google docs).

### 3.3 README

- "Setup → Accessing the monitor": add the HTTPS URL path alongside the
  existing SSH-tunnel instructions; note the Google OAuth console steps.

## 4. Implementation steps

1. **Terraform** — §3.1 resources already written into `main.tf`/
   `outputs.tf` (validated with `terraform validate` + a local Docker smoke
   test of the rendered startup script). Apply via `just provision` under
   `ctx use quantitative-trading`; read the service URL from the
   `monitor_proxy_url` output.
2. **Local smoke test (done)** — stock nginx container + the rendered startup
   script against a local upstream on 8501: page load 200,
   `/healthz` 200, upstream access log shows the forwarded headers.
3. **Deploy** — from here on the proxy is fire-and-forget: `just update`
   keeps updating only the VM.
4. **Ansible/env** — set `GOOGLE_REDIRECT_URI=https://<run-app-url>/oauth2callback`
   in `.env.production`; `just update` (or deploy_tasks re-render) to apply on VM.
5. **Console (manual)** — add the redirect URI + authorized domain (§3.2);
   wait for propagation (5 min – hours).
6. **README** — document the HTTPS access path and console steps.

## 5. Constraints & known trade-offs (document, don't hide)

1. **Plaintext hop Cloud Run → VM** over the public internet (HTTP to
   port 8501). Streamlit content is dashboard-only, but cookies (OIDC session)
   ride this hop unencrypted. Accepted per §0b.2; hardening below.
2. **Cloud Run egress IPs are not stable** — the VM firewall cannot reliably
   allow-list Cloud Run's source. This is why the public hop is `0.0.0.0/0`
   unless Direct VPC egress is adopted later.
3. **Cloud Run request timeout** caps websocket lifetime (deployed value:
   3600s = max). Fine for interactive dashboard use; a hung tab reconnects.
4. `--allow-unauthenticated` on Cloud Run is safe: the only app behind it is
   Streamlit's own Google-OIDC + allowlist gate. Do not proxy anything else
   through this service.

### Hardening follow-ups (out of MVP scope, ordered by value)

1. **Direct VPC egress** — attach Cloud Run to the VM's VPC; nginx proxies to
   the VM's *internal* IP; firewall allows 8501 only from the Cloud Run
   subnet range. Kills the public 8501 exposure entirely; removes trade-off §5.1.
2. **TLS on the hop** — self-signed cert on the VM's nginx/Streamlit front,
   `proxy_ssl_verify off` with pinned cert, or ACME on the VM.
3. **Cloud Run IAM** — require Cloud IAM auth on the service and front it with
   IAP-style identity (redundant with Streamlit's OIDC; only if we ever proxy
   more than Streamlit).

## 6. Acceptance

1. `curl https://<run-app-url>/_stcore/health` → 200 `ok` (end-to-end through
   the proxy). Note: `/healthz` is intercepted by Google's run.app frontend
   (404) and never reaches the container — the in-container `/healthz`
   location stays as a harmless local fallback only.
2. Browser: open the run.app URL → Streamlit loads over HTTPS, "Continue with
   Google" → consent → lands back on the dashboard authenticated
   (`/oauth2callback` flow completes against the new redirect URI).
3. Unauthorized email → existing allowlist error still shown.
4. `just tunnel` + `localhost:8501` still works (regression guard).
5. Trader untouched: no code, no env, no firewall path change beyond monitor
   port ingress; no real-order risk introduced.

### Test plan

- Local (done): stock nginx container + the Terraform-rendered startup script
  against a local upstream on 8501 — page load 200, `/healthz` 200, forwarded
  headers visible in the upstream access log.
- Live: acceptance items 1–4 of §6 (`/healthz`, full Google login, allowlist
  rejection, `just tunnel` regression). No trader-side verification needed —
  trader code and env are untouched by this spec.
