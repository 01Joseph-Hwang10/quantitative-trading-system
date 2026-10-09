# Done — GCP Logging for the deployed application

Implementation record for `plan.md` (motivation and requirements from the
original draft — not repeated here). Shipped in `0a30705` (2026-10-01);
implemented, deployed, and verified the same day (both services healthy,
log entries confirmed via `gcloud logging read`).

## Surface

| File | Change |
|---|---|
| `system/config/logging_setup.py` | **New.** `setup_logging(settings, service)`: stderr StreamHandler (always) + `CloudLoggingHandler` (when gated on), with a `service` label (`trader` / `monitor`). Handler import is lazy to keep it off the healthcheck path. |
| `system/config/settings.py` | Added `google_cloud_project: str \| None` (reads `GOOGLE_CLOUD_PROJECT`). |
| `system/apps/trader/__main__.py` / `monitor/__main__.py` | Replaced raw `logging.basicConfig` with `setup_logging(...)` (`service="trader"` / `"monitor"`); the monitor wires it before Streamlit boots so root handlers cover `app.py`. |
| `pyproject.toml` / `uv.lock` | Added `google-cloud-logging`. |
| `terraform/main.tf` | Explicitly enable `logging.googleapis.com` (`disable_on_destroy = false`). |
| `ansible/templates/docker-compose.yml.j2` + `group_vars/vm.yml` | Inject `GOOGLE_CLOUD_PROJECT` into both services on the VM only — local runs keep plain stdout and make no GCP calls. |
| `tests/test_logging_setup.py` | **New.** Fallback (no project → stdout only) and handler wiring (fake client/handler); autouse fixture restores root-logger state so fake handlers don't leak into other tests. |
| `README.md` | Deployment section documents GCP Logging + Logs Explorer links. |

## Verification status

`just provision` (API enabled) + `just update` rolled the stack; Ansible
reported `Deploy healthy: d853ff8-dirty (trader, monitor)`. Observability
confirmed via `gcloud logging read 'labels.service="trader"'` — entries
carry proper severity, `gce_instance` resource, and the `service` label.
Logs Explorer deep links are in `plan.md`. Known residual: the deployed tag
was `-dirty` at the time (clean from the next `just update`); retention
follows the project default bucket (30 days).
