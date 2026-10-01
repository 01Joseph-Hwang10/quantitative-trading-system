"""Daemon loop: fire cycles on configurable cron schedules (Asia/Seoul).

Two schedules are evaluated with croniter:
- decision_schedule_cron: run a full Trader cycle (default 15:00 KST weekdays)
- feed_update_schedule_cron: update data feeds only (default 09:00 KST)

The loop wakes on a small interval so SIGTERM (Docker stop) is honored within
seconds. Cron controls *when we attempt*; the cycle's market-open guard still
decides whether a decision runs on KRX holidays.
"""

from __future__ import annotations

import logging
import signal
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from system.apps.trader.trader import Trader

logger = logging.getLogger(__name__)

WAKE_INTERVAL_SECONDS = 30


class Runner:
    def __init__(
        self,
        trader: Trader,
        *,
        decision_schedule_cron: str,
        feed_update_schedule_cron: str,
        timezone_name: str = "Asia/Seoul",
    ) -> None:
        self.trader = trader
        self.decision_schedule_cron = decision_schedule_cron
        self.feed_update_schedule_cron = feed_update_schedule_cron
        self.timezone = ZoneInfo(timezone_name)
        self._stop_requested = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        logger.info(
            "Trader daemon started (decision=%r, feed_update=%r, tz=%s)",
            self.decision_schedule_cron,
            self.feed_update_schedule_cron,
            self.timezone,
        )
        while not self._stop_requested:
            next_decision = self._next_fire(self.decision_schedule_cron)
            next_feed = self._next_fire(self.feed_update_schedule_cron)
            target, is_decision_cycle = (next_decision, True) if next_decision <= next_feed else (next_feed, False)
            logger.info("Next %s at %s", "cycle" if is_decision_cycle else "feed update", target)
            while not self._stop_requested and datetime.now(self.timezone) < target:
                time.sleep(WAKE_INTERVAL_SECONDS)
            if self._stop_requested:
                break
            try:
                if is_decision_cycle:
                    self.trader.run_cycle()
                else:
                    updated = self.trader.update_feeds()
                    logger.info("Feed update complete: %s", updated)
            except Exception:  # noqa: BLE001 - the daemon must survive cycle errors
                logger.exception("Scheduled run failed; continuing")
        logger.info("Trader daemon stopped")

    # ── helpers ────────────────────────────────────────────────────────────
    def _next_fire(self, expression: str) -> datetime:
        now = datetime.now(self.timezone)
        return croniter(expression, now).get_next(datetime)

    def _handle_stop(self, signum, frame) -> None:  # noqa: ARG002 - signal API
        logger.info("Received signal %s; stopping", signum)
        self._stop_requested = True
