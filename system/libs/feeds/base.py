"""DataFeed: a pandas DataFrame subclass backed by a table in feed.db.

A feed is pure market data — account/position state never lives here.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pandas as pd

from system.libs.db import feed_store

if TYPE_CHECKING:
    from system.apps.trader.broker.base import BrokerClient

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class DataFeed(pd.DataFrame):
    """Market-data feed loaded from (and persisted to) feed.db.

    Concrete subclasses declare KIND/SOURCE and implement `fetch_rows`.
    """

    # Keep custom attributes across pandas operations.
    _metadata = ["feed_name", "feed_kind", "feed_source", "feed_source_params"]

    KIND: str = "ohlcv"  # "ohlcv" | "scalar"
    SOURCE: str = "yfinance"
    # Earliest date fetched when the feed table is empty (notebook window start).
    START_DATE_DEFAULT = date(2022, 10, 1)

    def __init__(
        self,
        data=None,
        *args,
        name: str = "",
        kind: str | None = None,
        source: str | None = None,
        source_params: dict[str, Any] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(data, *args, **kwargs)
        self.feed_name = name
        self.feed_kind = kind if kind is not None else self.KIND
        self.feed_source = source if source is not None else self.SOURCE
        self.feed_source_params = dict(source_params) if source_params else {}

    @property
    def name(self) -> str:  # noqa: A003 - deliberate override per the DataFeed concept
        return self.feed_name

    @property
    def START_DATE(self) -> date | None:  # noqa: N802 - specified by the draft spec
        """Start date of the stored time series (None if the feed is empty)."""
        if len(self.index) == 0:
            return None
        first = self.index[0]
        if isinstance(first, pd.Timestamp):
            return first.date()
        if isinstance(first, date):
            return first
        return date.fromisoformat(str(first)[:10])

    # ── persistence ────────────────────────────────────────────────────────
    def load(self, conn) -> "DataFeed":
        """Hydrate this feed instance in place from feed.db (self is returned).

        An empty frame with the registry's source params if never fetched.
        """
        meta = feed_store.get_feed_meta(conn, self.feed_name)
        if meta is None:
            return self
        self.feed_source = meta["source"]
        self.feed_source_params.update(json.loads(meta["source_params_json"]))
        rows = feed_store.read_table(conn, self.feed_name)
        if rows:
            frame = pd.DataFrame([dict(row) for row in rows])
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.set_index("date")
            # Re-initialize the underlying DataFrame in place, keeping the
            # custom attributes (feed_name, feed_source_params, ...).
            super(DataFeed, self).__init__(frame)
        return self

    def persist(self, conn, rows: list[dict[str, Any]]) -> None:
        """Upsert rows into feed.db and refresh `_metadata`."""
        feed_store.upsert_rows(
            conn,
            self.feed_name,
            self.feed_kind,  # type: ignore[arg-type]
            rows,
            source=self.feed_source,
            source_params=self.feed_source_params,
            start_date=self.START_DATE_DEFAULT,
            now=now_utc(),
        )

    # ── data acquisition ───────────────────────────────────────────────────
    def fetch_rows(self, start: date, end: date) -> list[dict[str, Any]]:
        """Fetch rows for [start, end] from the upstream source (subclass hook)."""
        raise NotImplementedError

    def update(self, conn, *, end: date | None = None) -> int:
        """Fetch only the missing range from the source and upsert it.

        Returns the number of newly stored rows. Idempotent: a second call
        without new upstream data stores nothing.
        """
        last = feed_store.last_date(conn, self.feed_name)
        fetch_start = last + timedelta(days=1) if last else self.START_DATE_DEFAULT
        fetch_end = end or date.today()
        if fetch_start > fetch_end:
            return 0
        rows = self.fetch_rows(fetch_start, fetch_end)
        if last is not None:
            rows = [row for row in rows if date.fromisoformat(str(row["date"])[:10]) > last]
        if rows:
            self.persist(conn, rows)
        return len(rows)

    def snapshot(self, broker: "BrokerClient") -> pd.Series | None:
        """Provisional bar for the in-progress trading day (None if unavailable).

        yfinance daily bars only exist after the session closes, so the trader
        synthesizes today's row from the broker's market-data API. Base
        implementation returns None; concrete feeds override.
        """
        return None

    def with_row(self, row: pd.Series) -> "DataFeed":
        """Return a copy of this feed with one extra dated row appended."""
        frame = pd.concat([self, pd.DataFrame([row.to_dict()], index=pd.DatetimeIndex([row.name]))]).sort_index()
        return type(self)(
            frame,
            name=self.feed_name,
            kind=self.feed_kind,
            source=self.feed_source,
            source_params=self.feed_source_params,
        )
