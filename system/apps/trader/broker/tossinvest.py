"""TossBroker: sync facade over the async tossinvest client.

Implements BrokerClient using tossinvest's TossClient/AccountClient. This
adapter is never exercised against the live API by tests or by default
(`BROKER=mock`); a mock Transport can be injected for unit tests.

KR market hours: the KRX regular session runs 09:00–15:30 KST, so on a
business day the decision at 15:00 KST is inside the session.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from tossinvest import (
    AccountClient,
    ClientConfig,
    Currency,
    OrderCreateRequest,
    Side,
    TossClient,
)

from system.apps.trader.broker.base import AccountState, OrderResult, Position
from system.config.settings import Settings

REGULAR_SESSION_END = (15, 30)  # 15:30 KST


class TossBroker:
    def __init__(self, settings: Settings) -> None:
        if not settings.toss_client_id or not settings.toss_client_secret:
            raise ValueError("TOSSSEC_CLIENT_ID and TOSSSEC_CLIENT_SECRET are required when BROKER=toss")
        self.settings = settings
        self.timezone = ZoneInfo(settings.timezone)
        self._config = ClientConfig(
            client_id=settings.toss_client_id,
            client_secret=settings.toss_client_secret,
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
                avg_price=float(item.average_purchase_price),
                last_price=float(item.last_price),
            )
            for item in holdings.items
        ]
        return AccountState(cash=float(buying_power.cash_buying_power), positions=positions)

    def get_last_price(self, symbol: str) -> float | None:
        prices = self._run(self._client.prices([symbol]))
        if not prices:
            return None
        return float(prices[0].last_price)

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
