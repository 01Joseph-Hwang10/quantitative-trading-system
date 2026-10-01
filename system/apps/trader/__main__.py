"""CLI entrypoint: `python -m system.apps.trader` (daemon) or `--once`."""

from __future__ import annotations

import argparse
import logging

from system.apps.trader.runner import Runner
from system.apps.trader.trader import Trader
from system.config.gateways import build_broker, open_connections
from system.config.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="trader")
    parser.add_argument("--once", action="store_true", help="Run a single decision cycle and exit")
    args = parser.parse_args()

    settings = Settings()
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
        )
        runner.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
