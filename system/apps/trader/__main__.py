"""CLI entrypoint: `python -m system.apps.trader` (daemon), `--once`, or `healthcheck`."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from system.apps.trader.runner import Runner
from system.apps.trader.trader import Trader
from system.config.gateways import build_broker, open_connections
from system.config.settings import Settings

HEARTBEAT_MAX_AGE_SECONDS = 600  # runner wakes every 30 s; 10 min = 20 missed wakes


def run_healthcheck(settings: Settings) -> int:
    """Exit 0 iff the trader heartbeat file is fresh (compose healthcheck)."""
    heartbeat_path = settings.data_dir / "trader_heartbeat"
    if not heartbeat_path.exists():
        print(f"healthcheck failed: {heartbeat_path} does not exist", file=sys.stderr)
        return 1
    age = time.time() - heartbeat_path.stat().st_mtime
    if age > HEARTBEAT_MAX_AGE_SECONDS:
        print(f"healthcheck failed: heartbeat is {age:.0f}s old", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="trader")
    parser.add_argument("--once", action="store_true", help="Run a single decision cycle and exit")
    parser.add_argument(
        "healthcheck", nargs="?", choices=["healthcheck"], help="Exit 0 iff the daemon heartbeat is fresh"
    )
    args = parser.parse_args()

    settings = Settings()
    if args.healthcheck == "healthcheck":
        return run_healthcheck(settings)

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings.ensure_data_dir()
    with open_connections(settings) as connections:
        broker = build_broker(settings, feed_conn=connections.feed)
        trader = Trader(
            broker=broker,
            feed_conn=connections.feed,
            metadata_conn=connections.metadata,
            timezone_name=settings.timezone,
        )
        if args.once:
            trader.run_cycle()
            return 0
        runner = Runner(
            trader,
            decision_schedule_cron=settings.decision_schedule_cron,
            feed_update_schedule_cron=settings.feed_update_schedule_cron,
            timezone_name=settings.timezone,
            heartbeat_path=settings.data_dir / "trader_heartbeat",
        )
        runner.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
