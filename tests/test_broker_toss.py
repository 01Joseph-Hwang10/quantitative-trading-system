"""TossBroker day-bar extraction: today's in-progress daily candle → DayBar.

Pure-function tests over `day_bar_from_candles` (no network, no credentials).
The mapping must accept only a candle stamped today (KST): on weekends and
holidays the candles list's newest entry is the previous session's completed
bar and must never be mistaken for an in-progress bar.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tossinvest.model.market_data import Candle

from system.apps.trader.broker.tossinvest import day_bar_from_candles

KST = ZoneInfo("Asia/Seoul")


def make_candle(day: date, open_=12_200, high=12_350, low=12_100, close=12_300, volume=850_000):
    return Candle(
        timestamp=datetime(day.year, day.month, day.day, 0, 0, tzinfo=KST),
        open_price=open_,
        high_price=high,
        low_price=low,
        close_price=close,
        volume=volume,
        currency="KRW",
    )


def test_picks_todays_in_progress_candle():
    today = date(2026, 10, 2)  # Friday
    candles = [
        make_candle(today),
        make_candle(date(2026, 10, 1), open_=12_000, close=12_150),
    ]
    day_bar = day_bar_from_candles(candles, today, KST)
    assert day_bar is not None
    assert day_bar.date == today
    assert (day_bar.open, day_bar.high, day_bar.low, day_bar.close) == (12_200.0, 12_350.0, 12_100.0, 12_300.0)
    assert day_bar.volume == 850_000.0


def test_returns_none_when_newest_candle_is_older_than_today():
    # Weekend/holiday: newest candle is the previous session's completed bar.
    friday = date(2026, 10, 2)
    sunday = date(2026, 10, 4)
    candles = [make_candle(friday)]
    assert day_bar_from_candles(candles, sunday, KST) is None


def test_returns_none_on_empty_candles():
    assert day_bar_from_candles([], date(2026, 10, 2), KST) is None


def test_converts_candle_timestamp_to_target_timezone():
    # KRX daily candles are stamped at KST midnight, but the mapping must go
    # through the target timezone: a candle stamped 2026-10-01 15:30 UTC is
    # 2026-10-02 00:30 KST, so it IS today's (Oct 2, KST) bar — and it is NOT
    # an Oct 1 KST bar.
    utc = ZoneInfo("UTC")
    candle = Candle(
        timestamp=datetime(2026, 10, 1, 15, 30, tzinfo=utc),
        open_price=12_200,
        high_price=12_350,
        low_price=12_100,
        close_price=12_300,
        volume=1,
        currency="KRW",
    )
    assert day_bar_from_candles([candle], date(2026, 10, 2), KST) is not None
    assert day_bar_from_candles([candle], date(2026, 10, 1), KST) is None
