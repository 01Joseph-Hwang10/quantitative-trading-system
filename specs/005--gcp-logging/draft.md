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
