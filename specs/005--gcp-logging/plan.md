# Plan — GCP Logging implementation record

Source: original agent draft — merged into this plan (see Appendix)
Status: **implemented, deployed, and verified on 2026-10-01** (tag
`d853ff8-dirty`, both services healthy, log entries confirmed via
`gcloud logging read`).

## Approach

In-app Cloud Logging via the `google-cloud-logging` Python client instead of
the Ops Agent:

- Structured output for free: real severities, `gce_instance` resource
  attribution, per-service labels.
- Credentials come from the VM service account through the metadata server
  (Application Default Credentials) — Terraform already granted
  `roles/logging.logWriter` and the instance has the `cloud-platform` scope,
  so no key files and no new IAM bindings were needed.
- Memory footprint is negligible compared to the Ops Agent, which matters on
  the 1 GB e2-micro (README note 9).

## Gating

The Cloud Logging handler is attached only when `GOOGLE_CLOUD_PROJECT` is
set. Docker compose injects it on the VM only; local `.env` files leave it
unset, so local runs keep plain stdout logging and make no GCP calls.

## Changes

| File | Change |
|---|---|
| `system/config/logging_setup.py` | **New.** `setup_logging(settings, service)`: stderr StreamHandler (always) + `CloudLoggingHandler` (when gated on), with a `service` label (`trader` / `monitor`). Handler import is lazy to keep it off the healthcheck path. |
| `system/config/settings.py` | Added `google_cloud_project: str \| None` (reads `GOOGLE_CLOUD_PROJECT`). |
| `system/apps/trader/__main__.py` | Replaced raw `logging.basicConfig` with `setup_logging(settings, service="trader")`. |
| `system/apps/monitor/__main__.py` | Calls `setup_logging(..., service="monitor")` before Streamlit boots, so root handlers cover `app.py` (executed in-process by the Streamlit CLI). |
| `pyproject.toml` / `uv.lock` | Added `google-cloud-logging` dependency. |
| `terraform/main.tf` | Explicitly enable `logging.googleapis.com` (`google_project_service.logging`, `disable_on_destroy = false`). |
| `ansible/group_vars/vm.yml` | Added `gcp_project: quantitative-trading-510302`. |
| `ansible/templates/docker-compose.yml.j2` | Inject `GOOGLE_CLOUD_PROJECT={{ gcp_project }}` into both services (compose `environment:` takes precedence over `env_file`). |
| `tests/test_logging_setup.py` | **New.** Two tests: fallback (no project → stdout handlers only) and handler wiring (fake client/handler; asserts project, handler name, and `service` label). Root-logger state is restored via an autouse fixture — without it the fake handlers leak into later tests and break them (`NotImplementedError` on emit). |
| `README.md` | Documented GCP Logging under Deployment with Logs Explorer links. |

## Deployment & verification

```bash
just provision   # terraform apply — enabled logging.googleapis.com (1 added)
just update      # built + pushed linux/amd64 image, rolled the stack
```

Ansible reported `Deploy healthy: d853ff8-dirty running (trader, monitor)`.
Observability was verified directly:

```
$ gcloud logging read 'labels.service="trader"' --project quantitative-trading-510302 --limit 1 --format=json
{
  "labels": { "python_logger": "system.apps.trader.runner", "service": "trader" },
  "severity": "INFO",
  "resource": { "type": "gce_instance", "labels": { "zone": ".../us-central1-a", ... } },
  "textPayload": "2026-10-01 13:38:45,750 INFO system.apps.trader.runner Trader daemon started (...)"
}
```

The monitor has no entries until it logs something (Streamlit only logs on
events such as logins/errors) — expected, not a fault.

## Logs Explorer deep links

- Trader:
  `https://console.cloud.google.com/logs/query;query=labels.service=trader?project=quantitative-trading-510302`
- Monitor:
  `https://console.cloud.google.com/logs/query;query=labels.service=monitor?project=quantitative-trading-510302`
- Both:
  `https://console.cloud.google.com/logs/query;query=labels.service=trader%20OR%20labels.service=monitor?project=quantitative-trading-510302`

Note: URL-encoding quoted values (`%22...%22`) in these console deep links
made the Logs Explorer report `Invalid filter` — bare (unquoted) values in
the link work; quoted values are fine when pasted directly into the query
editor.

## Residual notes

- The deployed tag is `d853ff8-dirty` (logging changes were uncommitted at
  deploy time). The next `just update` after committing produces a clean sha
  tag.
- Log retention follows the project default bucket (30 days); no sink or
  bucket customization was added.

## Appendix — Original draft (agent-authored, merged from `draft.md`)

# Draft — GCP Logging for the deployed application

Ensure the application (trader + monitor containers on the deployed GCE VM)
ships its logs to Google Cloud Logging, and provide Logs Explorer deep links
so log observability in GCP can be verified by clicking a URL.

## Requirements

1. All application log records (trader daemon and monitor UI) must be
   observable in GCP Logging for the project `quantitative-trading-510302`.
2. Logs must carry proper severity (`INFO`/`WARNING`/`ERROR`/…) and be
   attributable to the VM (resource type `gce_instance`).
3. Logs must be filterable per service (trader vs monitor).
4. Local development behavior must be unchanged (stdout only, no GCP calls).
5. Must fit the e2-micro (1 GB RAM) — avoid heavyweight infra like the
   Ops Agent.
6. Provide Logs Explorer URLs to visually confirm observability.
