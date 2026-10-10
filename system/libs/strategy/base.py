"""Strategy protocol and decision vocabulary.

A strategy is a pure decision function: it sees market data and position state
and returns a Signal. It must never touch a broker client or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class MarketView:
    """Everything a strategy may look at for one decision.

    Position state (quantity, entry price) is sourced from the broker by the
    Trader — never from a DataFeed, which is market data only.
    """

    symbol: str
    ohlcv: pd.DataFrame  # trading-stock feed, including today's snapshot row
    macro: pd.DataFrame  # unified macro frame with delayed series + D_t_{n} columns
    position_quantity: int = 0  # current held units (0 = flat)
    position_entry_price: float | None = None  # average entry price; None if flat
    extra: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    """Decision interface implemented by concrete strategies."""

    @property
    def description(self) -> str:
        """Markdown white paper of the strategy (rendered by the monitor)."""
        ...

    def decide(self, view: MarketView) -> tuple[Signal, dict[str, Any]]:
        """Return the signal plus indicator values for logging."""
        ...
