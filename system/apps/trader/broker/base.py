"""BrokerClient protocol and shared value types.

Implementations: MockBroker (default, no real orders) and TossBroker (the
tossinvest adapter — never exercised against the live API unless BROKER=toss).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass
class DayBar:
    """In-progress daily bar (session OHLC so far + cumulative volume).

    Used to build the trader's same-day provisional bar (see
    `OhlcvFeed.snapshot`) with real intraday open/high/low instead of a flat
    last-price bar.
    """

    date: date
    open: float
    high: float
    low: float
    close: float  # freshest last traded price
    volume: float  # cumulative session volume


@dataclass
class Position:
    symbol: str
    quantity: int  # whole units only
    avg_price: float
    last_price: float


@dataclass
class AccountState:
    cash: float
    positions: list[Position] = field(default_factory=list)

    def position(self, symbol: str) -> Position | None:
        for position in self.positions:
            if position.symbol == symbol:
                return position
        return None

    @property
    def market_value(self) -> float:
        return sum(position.quantity * position.last_price for position in self.positions)

    @property
    def total(self) -> float:
        return self.cash + self.market_value


@dataclass
class OrderResult:
    symbol: str
    side: str  # BUY | SELL
    quantity: int
    price: float  # fill price (0 if unfilled)
    order_id: str | None
    status: str  # FILLED | REJECTED | PENDING
    note: str | None = None


@runtime_checkable
class BrokerClient(Protocol):
    """The only surface the Trader is allowed to use for execution."""

    def get_account(self) -> AccountState: ...

    def get_last_price(self, symbol: str) -> float | None: ...

    def get_day_bar(self, symbol: str) -> DayBar | None:
        """Today's in-progress daily bar, or None if unavailable.

        Concrete brokers may degrade to None (e.g. outside a trading session,
        market-data endpoint failure); callers must fall back gracefully.
        """
        ...

    def is_market_open(self) -> bool: ...

    def buy(self, symbol: str, quantity: int) -> OrderResult: ...

    def sell(self, symbol: str, quantity: int) -> OrderResult: ...
