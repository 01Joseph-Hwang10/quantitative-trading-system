"""Registry of all known signals (name → compute over input feeds)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from system.libs.feeds.base import DataFeed
from system.libs.feeds.registry import TRADING_STOCK_FEED
from system.libs.signals.base import DerivedSignal
from system.libs.signals.macro import (
    N_MACRO,
    compute_d_t,
    compute_s_dxy,
    compute_s_fx,
    compute_s_tnx,
)
from system.libs.signals.technical import compute_adx_series, compute_t_t_series

MACRO_FEEDS = ("macro_tnx", "macro_dxy", "macro_usdkrw")


@dataclass(frozen=True)
class SignalDefinition:
    """How to compute one persisted signal from the stored data feeds."""

    inputs: tuple[str, ...]
    source: str  # "macro" | "technical"
    compute: Callable[[dict[str, DataFeed]], pd.Series]


def _macro_series(feeds: dict[str, DataFeed]) -> tuple[pd.Series, pd.Series, pd.Series]:
    """The three macro scalar series in TNX / DXY / FX order."""
    return (
        feeds["macro_tnx"].to_series(),
        feeds["macro_dxy"].to_series(),
        feeds["macro_usdkrw"].to_series(),
    )


def _compute_s_tnx(feeds: dict[str, DataFeed]) -> pd.Series:
    return compute_s_tnx(*_macro_series(feeds))


def _compute_s_dxy(feeds: dict[str, DataFeed]) -> pd.Series:
    return compute_s_dxy(*_macro_series(feeds))


def _compute_s_fx(feeds: dict[str, DataFeed]) -> pd.Series:
    return compute_s_fx(*_macro_series(feeds))


def _make_d_t_computer(window: int) -> Callable[[dict[str, DataFeed]], pd.Series]:
    def compute(feeds: dict[str, DataFeed]) -> pd.Series:
        return compute_d_t(*_macro_series(feeds), window=window)

    return compute


def _compute_adx(feeds: dict[str, DataFeed]) -> pd.Series:
    return compute_adx_series(feeds[TRADING_STOCK_FEED])


def _compute_t_t(feeds: dict[str, DataFeed]) -> pd.Series:
    return compute_t_t_series(feeds[TRADING_STOCK_FEED])


SIGNAL_DEFINITIONS: dict[str, SignalDefinition] = {
    # Component scores of D_t at the active macro window (sum / 3 = D_t_10).
    "s_tnx": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_compute_s_tnx),
    "s_dxy": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_compute_s_dxy),
    "s_fx": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_compute_s_fx),
    # Macro direction score per notebook window (strategy uses d_t_10).
    "d_t_10": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_make_d_t_computer(10)),
    "d_t_20": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_make_d_t_computer(20)),
    "d_t_40": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_make_d_t_computer(40)),
    "d_t_60": SignalDefinition(inputs=MACRO_FEEDS, source="macro", compute=_make_d_t_computer(60)),
    # Technical timing layer over the trading-stock feed.
    "adx_14": SignalDefinition(inputs=(TRADING_STOCK_FEED,), source="technical", compute=_compute_adx),
    "t_t": SignalDefinition(inputs=(TRADING_STOCK_FEED,), source="technical", compute=_compute_t_t),
}


def signal_names() -> list[str]:
    return list(SIGNAL_DEFINITIONS)


def build_signal(name: str) -> DerivedSignal:
    """Construct the (empty) signal instance for a registered signal name."""
    if name not in SIGNAL_DEFINITIONS:
        raise KeyError(f"Unknown signal: {name!r}")
    definition = SIGNAL_DEFINITIONS[name]
    # N_MACRO is exposed as a source param for observability in the monitor.
    source_params = {"n_macro": N_MACRO} if definition.source == "macro" else {}
    return DerivedSignal(
        name=name,
        source=definition.source,
        inputs=definition.inputs,
        compute=definition.compute,
        source_params=source_params,
    )


def load_signal(conn_signals: sqlite3.Connection, name: str) -> DerivedSignal:
    """Build the signal and hydrate it from signals.db."""
    return build_signal(name).load(conn_signals)
