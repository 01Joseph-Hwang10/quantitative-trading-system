"""Scalar macro feeds (single value per day) sourced from yfinance."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from system.libs.feeds.base import DataFeed


class ScalarFeed(DataFeed):
    """Daily scalar series (e.g. ^TNX, DX-Y.NYB, USDKRW=X close)."""

    KIND = "scalar"
    SOURCE = "yfinance"

    def __init__(self, data=None, *args, **kwargs) -> None:
        kwargs.setdefault("kind", self.KIND)
        super().__init__(data, *args, **kwargs)

    @classmethod
    def for_ticker(cls, name: str, ticker: str) -> "ScalarFeed":
        return cls(name=name, source_params={"ticker": ticker})

    @property
    def ticker(self) -> str:
        return self.feed_source_params["ticker"]

    def fetch_rows(self, start: date, end: date) -> list[dict]:
        raw = yf.Ticker(self.ticker).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=True
        )
        close = _find_close_column(raw)
        if close is None:
            return []
        rows: list[dict] = []
        for index, value in close.items():
            index_date = index.date() if hasattr(index, "date") else index
            if value is None or value != value:  # None or NaN
                continue
            rows.append({"date": index_date.isoformat(), "value": float(value)})
        return rows

    def snapshot(self, broker) -> pd.Series | None:
        price = broker.get_last_price(self.ticker)
        if price is None:
            return None
        return pd.Series({"value": price}, name=pd.Timestamp(date.today()))

    def to_series(self) -> pd.Series:
        """The stored values as a date-indexed series."""
        return self["value"] if "value" in self.columns else pd.Series(dtype=float)


def _find_close_column(raw: pd.DataFrame) -> pd.Series | None:
    """Case-insensitive lookup of the Close column (yfinance varies)."""
    for column in raw.columns:
        if str(column).lower() == "close":
            return raw[column]
    return None
