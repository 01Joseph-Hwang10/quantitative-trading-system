"""Tests for system.config.logging_setup."""

from __future__ import annotations

import logging

import pytest

from system.config.logging_setup import setup_logging
from system.config.settings import Settings


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """setup_logging(force=True) replaces root handlers; put them back after."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers = handlers
    root.setLevel(level)


def _root_handlers() -> list[logging.Handler]:
    return logging.getLogger().handlers


def test_without_project_logs_stay_on_stdout_only(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)

    setup_logging(Settings(google_cloud_project=None), service="trader")

    handlers = _root_handlers()
    assert handlers, "root logger must keep at least the stream handler"
    assert all(not type(h).__name__ == "CloudLoggingHandler" for h in handlers)


def test_with_project_attaches_cloud_handler(monkeypatch):
    calls: dict = {}

    class FakeClient:
        def __init__(self, project=None):
            calls["project"] = project

    class FakeHandler(logging.Handler):
        def __init__(self, client, name=None, labels=None):
            calls["name"] = name
            calls["labels"] = labels
            super().__init__()

    import google.cloud.logging as gcp_logging
    import google.cloud.logging.handlers as gcp_handlers

    # The lazy `from ... import CloudLoggingHandler` re-resolves the module
    # attribute on every call, so patching there is enough.
    monkeypatch.setattr(gcp_logging, "Client", FakeClient)
    monkeypatch.setattr(gcp_handlers, "CloudLoggingHandler", FakeHandler)

    setup_logging(Settings(google_cloud_project="test-project"), service="monitor")

    assert calls["project"] == "test-project"
    assert calls["name"] == "monitor"
    assert calls["labels"] == {"service": "monitor"}
    cloud = [h for h in _root_handlers() if isinstance(h, FakeHandler)]
    assert len(cloud) == 1, "exactly one Cloud Logging handler expected"
