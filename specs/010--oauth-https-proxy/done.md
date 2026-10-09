# Done — OAuth HTTPS proxy (Cloud Run nginx → VM monitor)

Implementation record for `plan.md` (motivation, architecture, and owner
decisions live there — not repeated here). Everything below is what actually
shipped, on 2026-10-09.

## Surface

| File | Change |
|---|---|
| `terraform/main.tf` | The whole proxy: `google_project_service.run` API enable, `google_cloud_run_v2_service.monitor_proxy` (service `quantitative-trading-monitor`), `google_cloud_run_v2_service_iam_member.public` (`roles/run.invoker` → `allUsers`), `google_compute_firewall.monitor_proxy` (TCP 8501 from `0.0.0.0/0` → VM tag). |
| `terraform/outputs.tf` | `monitor_proxy_url` output — the OAuth redirect domain. |
| `ansible/templates/docker-compose.yml.j2` | Monitor port re-published from `127.0.0.1:8501` to `8501:8501` (the one VM-side change; the firewall rule alone was not enough). |
| `.env.production` (gitignored) | `GOOGLE_REDIRECT_URI=https://quantitative-trading-monitor-yzx63kahaa-uc.a.run.app/oauth2callback`; applied to the VM via `ansible-playbook update.yml`. |
| `README.md` | "Accessing the monitor (HTTPS)" subsection under Deployment; updated monitor-access note in Notes & Warnings (both paths, both redirect URIs). |

No Python app changes, no Dockerfile, no justfile changes, no auxiliary cloud
resources — the nginx config is written in-line in the container's startup
command (Terraform `args` heredoc → `nginx -t` → `exec nginx`), against the
stock `docker.io/library/nginx:1.27-alpine` image.

## Live resources (as deployed)

- **Cloud Run service** `quantitative-trading-monitor` (us-central1):
  `https://quantitative-trading-monitor-yzx63kahaa-uc.a.run.app`, 1 vCPU /
  512 Mi, `min 0 / max 1`, request timeout 3600s. Upstream is the VM's
  **static** IP interpolated from Terraform state — an IP change propagates
  on the next `terraform apply`, no manual edits. Fire-and-forget after this
  deploy: `just update` touches only the VM.
- **nginx config (in-line)**: websocket support (`map $http_upgrade
  $connection_upgrade` + Upgrade/Connection headers), forwarded headers
  (Host, X-Real-IP, X-Forwarded-For/-Proto/-Port), `proxy_read/send_timeout
  3600s`, `proxy_buffering off`, `client_max_body_size 10m`, plus two local
  locations: `/healthz` (200) and `/privacy` (minimal privacy policy page).
- **Google console (manual, one-time)**: consent screen **published to
  production**; authorized domain + homepage + privacy policy set on
  branding; OAuth client now has both redirect URIs
  (`https://…run.app/oauth2callback` and the original
  `http://localhost:8501/oauth2callback` for tunnel use).

## Deviations from the plan (all discovered during deploy)

1. **512 Mi instead of 256 Mi** — Cloud Run rejects < 512 Mi for
   CPU-always-allocated instances. Nginx uses a fraction of it; free tier
   still covers the profile.
2. **`/healthz` is unreachable via run.app** — Google's frontend intercepts
   the path (404, never reaches the container). End-to-end health check is
   Streamlit's `/_stcore/health` → `ok`; the in-container `/healthz` location
   stays as a harmless local fallback.
3. **Monitor port binding change required** — the plan assumed "zero VM-side
   changes beyond the firewall rule", but the monitor was bound to loopback
   only. `docker-compose.yml.j2` now publishes `8501:8501`; the loopback
   tunnel path is unchanged.
4. **Privacy policy URL is mandatory for production publishing** — the
   audience page's publish button stays disabled until branding has app
   name, support email, homepage URL, and privacy policy URL. Served the
   minimal policy from the proxy itself (`/privacy`) rather than standing up
   new infrastructure, per the "everything contained in the Cloud Run
   service" decision.
5. **No image build** — the plan's earlier drafts had an AR image + build
   recipe; the final design uses the stock nginx image directly, so no
   build/push step exists at all.

## Verification status

All plan §6 acceptance items exercised live on 2026-10-09:

1. `/_stcore/health` → 200 `ok` through the proxy ✅
2. Full Google login via the run.app URL → authenticated dashboard
   (redirect URI match, consent, `/oauth2callback` round-trip) ✅
3. Allowlist: signed in as an `AUTHORIZED_USERS` email → full dashboard with
   live data (websockets working through nginx) ✅
4. Regression: direct IAP tunnel to `:8501` → Streamlit 200 ✅
5. Trader untouched: same image tag (`21783fa` at deploy time), healthy,
   heartbeat current; both container restarts happened outside KRX market
   hours ✅

Committed as `f639ec4` ("feat: OAuth HTTPS proxy on Cloud Run (spec 010)").
