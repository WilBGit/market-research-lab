"""Immutable domain objects for the separate NQ/MNQ paper monitor."""

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    symbol: str = ""


@dataclass(frozen=True)
class SweepEvent:
    direction: str
    liquidity_side: str
    index: int
    timestamp: datetime
    liquidity_level: float
    extreme: float


@dataclass(frozen=True)
class SMTEvent:
    direction: str
    index: int
    timestamp: datetime
    leader: str
    laggard: str
    leader_extreme: float
    laggard_extreme: float


@dataclass(frozen=True)
class CISDEvent:
    direction: str
    index: int
    timestamp: datetime
    close: float
    confirmation_level: float


@dataclass(frozen=True)
class PDArray:
    direction: str
    kind: str
    index: int
    low: float
    high: float
    equilibrium: float


@dataclass(frozen=True)
class PaperSignal:
    signal_id: str
    symbol: str
    direction: str
    action: str
    order_mode: str
    signal_timestamp: datetime
    sweep_timestamp: datetime
    cisd_timestamp: datetime
    entry_price: float
    stop_loss_price: float
    target_price: float
    risk_points: float
    liquidity_level: float
    pd_kind: str
    pd_low: float
    pd_high: float
    requirements: Mapping[str, bool]
    evidence: Mapping[str, object]
    outcome: Optional[str] = None
