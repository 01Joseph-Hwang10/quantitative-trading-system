"""Root logging configuration: stdout always, Google Cloud Logging in prod.

`setup_logging` always attaches a stderr StreamHandler (what `just logs` /
`docker logs` see). When `GOOGLE_CLOUD_PROJECT` is set — docker compose
injects it on the deployed VM — a Cloud Logging handler is attached too, so
every log record is shipped to GCP Logging with proper severity and a
`service` label. Credentials come from the VM service account via the
metadata server (ADC), which Terraform grants `roles/logging.logWriter`;
no key file is involved. Locally the variable is unset, so nothing is
shipped and behavior is unchanged.
"""

from __future__ import annotations

import logging

from system.config.settings import Settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(settings: Settings, service: str) -> None:
    """Configure root logging for the given service ('trader' | 'monitor')."""
    level = settings.log_level.upper()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    cloud_handler = _cloud_handler(settings, service, level)
    if cloud_handler is not None:
        handlers.append(cloud_handler)
    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers, force=True)


def _cloud_handler(settings: Settings, service: str, level: str) -> logging.Handler | None:
    """Cloud Logging handler, or None when not running on the deployed VM."""
    if not settings.google_cloud_project:
        return None
    # Imported lazily: keeps the heavyweight google-cloud stack off the
    # healthcheck path and out of local runs entirely.
    from google.cloud import logging as gcp_logging
    from google.cloud.logging.handlers import CloudLoggingHandler

    client = gcp_logging.Client(project=settings.google_cloud_project)
    handler = CloudLoggingHandler(client, name=service, labels={"service": service})
    handler.setLevel(level)
    return handler
