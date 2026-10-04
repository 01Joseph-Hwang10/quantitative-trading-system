"""TossBroker: sync facade over the async tossinvest client.

Implements BrokerClient using tossinvest's TossClient/AccountClient. This
adapter is never exercised against the live API by tests or by default
(`BROKER=mock`); a mock Transport can be injected for unit tests.

KR market hours: the KRX regular session runs 09:00–15:30 KST, so on a
business day the decision at 15:00 KST is inside the session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tossinvest import (
    AccountClient,
    ClientConfig,
    Currency,
    OrderCreateRequest,
    Side,
    TossClient,
)

from system.apps.trader.broker.base import AccountState, DayBar, OrderResult, Position
from system.config.settings import Settings

REGULAR_SESSION_END = (15, 30)  # 15:30 KST

# Verified against the live API on 2026-10-04: "1d" returns daily candles
# newest-first; the current session's in-progress candle carries real
# intraday open/high/low and cumulative volume. Minute candles ("1m") exist
# but carry volume=0 for this product, so daily candles are the right source.
DAILY_CANDLE_INTERVAL = "1d"

logger = logging.getLogger(__name__)


def day_bar_from_candles(candles, today: date, timezone: ZoneInfo) -> DayBar | None:
    """Pick today's in-progress daily candle and map it to a DayBar.

    `candles` are tossinvest Candle models, newest-first. Only a candle whose
    timestamp is **today (KST)** is accepted — on weekends/holidays the newest
    candle is the previous session's completed bar and must NOT be mistaken
    for an in-progress bar. Returns None when there is no candle for today.
    """
    for candle in candles:
        candle_date = candle.timestamp.astimezone(timezone).date()
        if candle_date == today:
            return DayBar(
                date=today,
                open=_dec_to_float(candle.open_price),
                high=_dec_to_float(candle.high_price),
                low=_dec_to_float(candle.low_price),
                close=_dec_to_float(candle.close_price),
                volume=_dec_to_float(candle.volume),
            )
        if candle_date < today:
            break  # newest-first: a strictly older candle means no bar today
    return None


def _dec_to_float(value) -> float:
    """tossinvest Dec wraps a Decimal with a `.value` accessor (no __float__)."""
    return float(value.value)


class TossBroker:
    def __init__(self, settings: Settings) -> None:
        if not settings.tosssec_client_id or not settings.tosssec_client_secret:
            raise ValueError("TOSSSEC_CLIENT_ID and TOSSSEC_CLIENT_SECRET are required when BROKER=toss")
        self.settings = settings
        self.timezone = ZoneInfo(settings.timezone)
        self._config = ClientConfig(
            client_id=settings.tosssec_client_id,
            client_secret=settings.tosssec_client_secret,
        )
        self._client = TossClient(self._config)
        self._account: AccountClient | None = None
        self._loop = asyncio.new_event_loop()

    def close(self) -> None:
        self._loop.run_until_complete(self._client.aclose())
        self._loop.close()

    # ── async → sync facade ───────────────────────────────────────────────
    def _run(self, coroutine):
        return self._loop.run_until_complete(coroutine)

    def _get_account(self) -> AccountClient:
        if self._account is None:
            accounts = self._run(self._client.accounts())
            if not accounts:
                raise RuntimeError("No tossinvest accounts available for the credentials")
            self._account = self._client.account(int(accounts[0].account_seq))
        return self._account

    # ── BrokerClient protocol ─────────────────────────────────────────────
    def get_account(self) -> AccountState:
        account = self._get_account()
        buying_power = self._run(account.buying_power(Currency.KRW))
        holdings = self._run(account.holdings())
        positions = [
            Position(
                symbol=item.symbol,
                quantity=int(item.quantity),
                avg_price=_dec_to_float(item.average_purchase_price),
                last_price=_dec_to_float(item.last_price),
            )
            for item in holdings.items
        ]
        return AccountState(cash=_dec_to_float(buying_power.cash_buying_power), positions=positions)

    def get_last_price(self, symbol: str) -> float | None:
        prices = self._run(self._client.prices([symbol]))
        if not prices:
            return None
        return _dec_to_float(prices[0].last_price)

    def get_day_bar(self, symbol: str) -> DayBar | None:
        """Today's in-progress daily bar, or None when unavailable.

        Degrades to None whenever the bar can't be determined (API error, no
        candle for today — e.g. weekend/holiday/market not yet open) so the
        caller falls back to the flat last-price snapshot bar.
        """
        try:
            page = self._run(
                self._client.candles(symbol, DAILY_CANDLE_INTERVAL, count=1)
            )
        except Exception as error:  # noqa: BLE001 - degrade, never break the cycle
            logger.warning("get_day_bar(%s) candles call failed: %s", symbol, error)
            return None
        today = datetime.now(self.timezone).date()
        day_bar = day_bar_from_candles(page.candles, today, self.timezone)
        if day_bar is None:
            return None
        # Overlay the freshest last price so close is not stale by one candle.
        last_price = self.get_last_price(symbol)
        if last_price is not None and last_price > 0:
            day_bar.close = last_price
        return day_bar

    def is_market_open(self) -> bool:
        calendar = self._run(self._client.kr_market_calendar())
        if calendar.today.integrated is None:
            return False  # full holiday
        session = calendar.today.integrated.regular_market
        if session is None:
            return False
        now = datetime.now(self.timezone)
        end = session.end_time
        session_end = (end.hour, end.minute) if end else REGULAR_SESSION_END
        return (now.hour, now.minute) <= session_end

    def buy(self, symbol: str, quantity: int) -> OrderResult:
        return self._submit(symbol, Side.BUY, quantity)

    def sell(self, symbol: str, quantity: int) -> OrderResult:
        return self._submit(symbol, Side.SELL, quantity)

    # ── helpers ────────────────────────────────────────────────────────────
    def _submit(self, symbol: str, side: Side, quantity: int) -> OrderResult:
        account = self._get_account()
        request = OrderCreateRequest.market(symbol, side, quantity)
        try:
            response = self._run(account.create_order(request))
        except Exception as error:  # noqa: BLE001 - surface broker errors as REJECTED
            return OrderResult(symbol, side.value, quantity, 0.0, None, "REJECTED", str(error))
        # Market orders are filled asynchronously; report the order id and let
        # the next cycle's account snapshot reflect the actual fill.
        return OrderResult(
            symbol,
            side.value,
            quantity,
            self.get_last_price(symbol) or 0.0,
            str(response.order_id),
            "PENDING",
        )
