"""MockBroker: deterministic mock of the tossinvest broker client.

No real orders are ever issued. State (cash + positions) persists in
`positions.json` under the data dir so the daemon survives restarts. Fills
happen at the last known price provided by the injected `price_lookup`.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from system.apps.trader.broker.base import AccountState, OrderResult, Position

SEED_CASH = 20_000_000.0  # KRW seed, matching the notebook's scale of a ~₩20M account


class MockBroker:
    def __init__(
        self,
        state_path: Path,
        price_lookup: Callable[[str], float | None],
        timezone_name: str = "Asia/Seoul",
    ) -> None:
        self.state_path = state_path
        self.price_lookup = price_lookup
        self.timezone = ZoneInfo(timezone_name)
        self._state = self._load_state()

    # ── state persistence ──────────────────────────────────────────────────
    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"cash": SEED_CASH, "positions": []}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2))

    # ── BrokerClient protocol ─────────────────────────────────────────────
    def get_account(self) -> AccountState:
        positions = [
            Position(
                symbol=item["symbol"],
                quantity=int(item["quantity"]),
                avg_price=float(item["avg_price"]),
                last_price=self.price_lookup(item["symbol"]) or float(item["avg_price"]),
            )
            for item in self._state["positions"]
        ]
        return AccountState(cash=float(self._state["cash"]), positions=positions)

    def get_last_price(self, symbol: str) -> float | None:
        return self.price_lookup(symbol)

    def is_market_open(self) -> bool:
        # Mock approximation: KRX regular session, weekdays only.
        now = datetime.now(self.timezone)
        if now.weekday() >= 5:
            return False
        return now.hour >= 9 and (now.hour < 15 or (now.hour == 15 and now.minute <= 30))

    def buy(self, symbol: str, quantity: int) -> OrderResult:
        price = self.price_lookup(symbol)
        if price is None:
            return OrderResult(symbol, "BUY", quantity, 0.0, None, "REJECTED", "no_price")
        cost = price * quantity
        if cost > float(self._state["cash"]):
            return OrderResult(symbol, "BUY", quantity, 0.0, None, "REJECTED", "insufficient_cash")
        self._state["cash"] = float(self._state["cash"]) - cost
        self._merge_position(symbol, quantity, price)
        self._save_state()
        return OrderResult(
            symbol, "BUY", quantity, price, f"mock-{datetime.now(self.timezone).timestamp():.0f}", "FILLED"
        )

    def sell(self, symbol: str, quantity: int) -> OrderResult:
        price = self.price_lookup(symbol)
        if price is None:
            return OrderResult(symbol, "SELL", quantity, 0.0, None, "REJECTED", "no_price")
        held = next((item for item in self._state["positions"] if item["symbol"] == symbol), None)
        if held is None or int(held["quantity"]) < quantity:
            return OrderResult(symbol, "SELL", quantity, 0.0, None, "REJECTED", "insufficient_quantity")
        proceeds = price * quantity
        self._state["cash"] = float(self._state["cash"]) + proceeds
        held["quantity"] = int(held["quantity"]) - quantity
        if held["quantity"] == 0:
            self._state["positions"].remove(held)
        self._save_state()
        return OrderResult(
            symbol, "SELL", quantity, price, f"mock-{datetime.now(self.timezone).timestamp():.0f}", "FILLED"
        )

    # ── helpers ────────────────────────────────────────────────────────────
    def _merge_position(self, symbol: str, quantity: int, price: float) -> None:
        held = next((item for item in self._state["positions"] if item["symbol"] == symbol), None)
        if held is None:
            self._state["positions"].append({"symbol": symbol, "quantity": quantity, "avg_price": price})
            return
        total_quantity = int(held["quantity"]) + quantity
        held["avg_price"] = (float(held["avg_price"]) * int(held["quantity"]) + price * quantity) / total_quantity
        held["quantity"] = total_quantity
