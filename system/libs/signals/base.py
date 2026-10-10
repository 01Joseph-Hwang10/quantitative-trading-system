"""Signals: a pandas DataFrame subclass backed by a table in signals.db.

A signal is derived data — computed from the stored data feeds by a pure
function, never fetched from an upstream source. Unlike `DataFeed`, whose
`update` fetches the missing date range from the network (swallowing flaky
upstream errors), `Signals.update` recomputes the full series locally and
stores only the dates after the last stored one; a raised exception is a
bug, not a retryable network hiccup.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

from system.libs.db import signals_store

if TYPE_CHECKING:
    from system.libs.feeds.base import DataFeed

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Signals(pd.DataFrame):
    """Derived daily signal loaded from (and persisted to) signals.db.

    The base class also serves as the wide, in-memory macro frame
    (raw + delayed series + `D_t_{n}` columns) passed to strategies via
    `MarketView.signals`; only scalar registered signals are persisted.
    """

    # Keep custom attributes across pandas operations.
    _metadata = ["signal_name", "signal_source", "signal_source_params"]

    SOURCE: str = "derived"

    @property
    def _constructor(self) -> type["Signals"]:
        # Return the subclass (not plain DataFrame) so pandas operations keep
        # the custom attributes via `_metadata` + `__finalize__`.
        return type(self)

    def __init__(
        self,
        data=None,
        *args,
        name: str = "",
        source: str | None = None,
        source_params: dict[str, Any] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(data, *args, **kwargs)
        self.signal_name = name
        self.signal_source = source if source is not None else self.SOURCE
        self.signal_source_params = dict(source_params) if source_params else {}

    @property
    def name(self) -> str:  # noqa: A003 - deliberate override per the Signals concept
        return self.signal_name

    @property
    def START_DATE(self) -> date | None:  # noqa: N802 - mirrors the DataFeed concept
        """Start date of the stored time series (None if the signal is empty)."""
        if len(self.index) == 0:
            return None
        first = self.index[0]
        if isinstance(first, pd.Timestamp):
            return first.date()
        if isinstance(first, date):
            return first
        return date.fromisoformat(str(first)[:10])

    def to_series(self) -> pd.Series:
        """The stored values as a date-indexed series (scalar signals only)."""
        return self["value"] if "value" in self.columns else pd.Series(dtype=float)

    # ── persistence ────────────────────────────────────────────────────────
    def load(self, conn) -> "Signals":
        """Hydrate this signal instance in place from signals.db (self is returned).

        An empty frame with the registry's source params if never computed.
        """
        meta = signals_store.get_signal_meta(conn, self.signal_name)
        if meta is None:
            return self
        self.signal_source = meta["source"]
        self.signal_source_params.update(json.loads(meta["source_params_json"]))
        rows = signals_store.read_table(conn, self.signal_name)
        if rows:
            frame = pd.DataFrame([dict(row) for row in rows])
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.set_index("date")
            # Re-initialize the underlying DataFrame in place, keeping the
            # custom attributes (signal_name, signal_source_params, ...).
            super(Signals, self).__init__(frame)
        return self

    def persist(self, conn, rows: list[dict[str, Any]], start_date: date | None = None) -> None:
        """Upsert rows into signals.db and refresh `_metadata`."""
        signals_store.upsert_rows(
            conn,
            self.signal_name,
            rows,
            source=self.signal_source,
            source_params=self.signal_source_params,
            start_date=start_date,
            now=now_utc(),
        )

    # ── data derivation ────────────────────────────────────────────────────
    def compute(self, inputs: dict[str, "DataFeed"]) -> pd.Series:
        """Compute the signal series from the loaded input feeds (subclass hook)."""
        raise NotImplementedError

    def update(self, conn_signals, feed_conn) -> int:
        """Recompute the series from the input feeds and store the missing range.

        Returns the number of newly stored rows. Idempotent: a second call
        with unchanged inputs stores nothing. Warm-up NaN heads are never
        stored; `start_date` is the first finite date of the computed series.
        """
        from system.libs.feeds.registry import load_feed

        last = signals_store.last_date(conn_signals, self.signal_name)
        inputs = {feed_name: load_feed(feed_conn, feed_name) for feed_name in self.signal_inputs}
        series = self.compute(inputs)
        finite = series.dropna()
        if finite.empty:
            return 0
        rows = [
            {"date": index.date().isoformat(), "value": float(value)}
            for index, value in finite.items()
            if last is None or index.date() > last
        ]
        if rows:
            self.persist(conn_signals, rows, start_date=finite.index[0].date())
        return len(rows)


class DerivedSignal(Signals):
    """Registered signal computed by a pure function over its input feeds.

    The inputs (feed names) and the compute callable are injected by the
    signal registry; `signal_inputs` must survive pandas operations because
    `update` re-reads it on fresh instances built by `build_signal`.
    """

    _metadata = Signals._metadata + ["signal_inputs", "signal_compute"]

    def __init__(
        self,
        data=None,
        *args,
        inputs: tuple[str, ...] = (),
        compute: Callable[[dict[str, "DataFeed"]], pd.Series] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(data, *args, **kwargs)
        self.signal_inputs = tuple(inputs)
        self.signal_compute = compute

    def compute(self, inputs: dict[str, "DataFeed"]) -> pd.Series:
        if self.signal_compute is None:
            raise NotImplementedError(f"Signal {self.signal_name!r} has no compute function")
        return self.signal_compute(inputs)
