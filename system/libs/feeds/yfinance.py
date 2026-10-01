"""OHLCV feeds sourced from yfinance daily bars."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from system.libs.feeds.base import OHLCV_COLUMNS, DataFeed


class OhlcvFeed(DataFeed):
    """Daily OHLCV feed for a single yfinance ticker.

    Completed bars come from yfinance, which publishes them only after the
    session closes; `snapshot()` fills the in-progress day from the broker.
    """

    KIND = "ohlcv"
    SOURCE = "yfinance"

    def __init__(self, data=None, *args, **kwargs) -> None:
        kwargs.setdefault("kind", self.KIND)
        super().__init__(data, *args, **kwargs)

    @classmethod
    def for_ticker(cls, name: str, ticker: str) -> "OhlcvFeed":
        return cls(name=name, source_params={"ticker": ticker})

    @property
    def ticker(self) -> str:
        return self.feed_source_params["ticker"]

    def fetch_rows(self, start: date, end: date) -> list[dict]:
        # yfinance `end` is exclusive; add one day to make ours inclusive.
        raw = yf.Ticker(self.ticker).history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=True
        )
        rows: list[dict] = []
        for index, series in raw.iterrows():
            index_date = index.date() if hasattr(index, "date") else index
            # yfinance column casing varies across versions (Open vs open).
            values = {column: _as_float(_lookup(series, column)) for column in OHLCV_COLUMNS}
            rows.append({"date": index_date.isoformat(), **values})
        return rows

    def snapshot(self, broker) -> pd.Series | None:
        """Today's provisional bar: OHLC = last broker price, volume = 0."""
        price = broker.get_last_price(self.ticker)
        if price is None:
            return None
        today = date.today()
        return pd.Series(
            {"open": price, "high": price, "low": price, "close": price, "volume": 0},
            name=pd.Timestamp(today),
        )


def _lookup(series: pd.Series, column: str):
    """Case-insensitive column lookup for cross-version yfinance frames."""
    if column in series.index:
        return series[column]
    for key in series.index:
        if str(key).lower() == column:
            return series[key]
    return None


def _as_float(value) -> float:
    return float(value) if value is not None and value == value else 0.0
