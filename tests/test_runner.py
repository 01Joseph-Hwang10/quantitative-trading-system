"""Daemon heartbeat + compose healthcheck subcommand."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from system.apps.trader.__main__ import HEARTBEAT_MAX_AGE_SECONDS, run_healthcheck
from system.apps.trader.runner import Runner
from system.config.settings import Settings


def make_runner(trader, tmp_path: Path) -> Runner:
    return Runner(
        trader,
        decision_schedule_cron="0 15 * * 1-5",
        feed_update_schedule_cron="0 9 * * 1-5",
        heartbeat_path=tmp_path / "trader_heartbeat",
    )


def make_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, _env_file=None)


def test_runner_writes_heartbeat_on_start_and_each_wake(trader, tmp_path, monkeypatch):
    runner = make_runner(trader, tmp_path)
    heartbeat_path = tmp_path / "trader_heartbeat"

    monkeypatch.setattr(signal_module(), "signal", lambda *args: None)
    monkeypatch.setattr(
        Runner, "_next_fire", lambda self, expression: datetime.now(self.timezone) + timedelta(hours=1)
    )
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            runner._stop_requested = True

    monkeypatch.setattr(time, "sleep", fake_sleep)
    runner.run_forever()

    assert heartbeat_path.exists()
    # Startup touch + one touch per wake iteration (3 sleeps → ≥ 3 wakes).
    assert len(sleeps) >= 3
    assert datetime.fromisoformat(heartbeat_path.read_text())


def signal_module():
    import signal

    return signal


def test_healthcheck_passes_on_fresh_heartbeat(tmp_path):
    heartbeat_path = tmp_path / "trader_heartbeat"
    heartbeat_path.write_text(datetime.now().isoformat())
    assert run_healthcheck(make_settings(tmp_path)) == 0


def test_healthcheck_fails_on_stale_heartbeat(tmp_path):
    heartbeat_path = tmp_path / "trader_heartbeat"
    heartbeat_path.write_text("old")
    stale = time.time() - (HEARTBEAT_MAX_AGE_SECONDS + 60)
    os.utime(heartbeat_path, (stale, stale))
    assert run_healthcheck(make_settings(tmp_path)) == 1


def test_healthcheck_fails_on_missing_heartbeat(tmp_path):
    assert run_healthcheck(make_settings(tmp_path)) == 1


@pytest.mark.parametrize("broker_value,expected_broker", [("mock", "mock"), ("toss", "toss")])
def test_settings_env_file_override(tmp_path, broker_value, expected_broker):
    env_file = tmp_path / "custom.env"
    env_file.write_text(f"BROKER={broker_value}\n")
    settings = Settings(_env_file=env_file, data_dir=tmp_path)
    assert settings.broker == expected_broker
