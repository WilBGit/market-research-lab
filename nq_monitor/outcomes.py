"""Conservative OHLC outcome resolution for paper-only signals."""

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from .models import Candle


@dataclass(frozen=True)
class OutcomeResult:
    signal_id: str
    outcome: str
    exit_price: float
    r_multiple: float
    reason: str


def resolve_signal_on_bar(
    signal: Mapping[str, object], bar: Candle, bars_held: int, max_hold_bars: int
) -> OutcomeResult:
    signal_id = str(signal["signal_id"])
    direction = str(signal["direction"])
    entry = float(signal["entry_price"])
    stop = float(signal["stop_loss_price"])
    target = float(signal["target_price"])
    risk = abs(entry - stop)
    if not isfinite(risk) or risk <= 0:
        raise ValueError("signal has invalid risk")
    if direction == "long":
        stop_hit = bar.low <= stop
        target_hit = bar.high >= target
        if stop_hit and target_hit:
            return OutcomeResult(signal_id, "LOSS", stop, -1.0, "both_hit_stop_first")
        if stop_hit:
            return OutcomeResult(signal_id, "LOSS", stop, -1.0, "stop_loss")
        if target_hit:
            return OutcomeResult(signal_id, "WIN", target, (target - entry) / risk, "target")
    elif direction == "short":
        stop_hit = bar.high >= stop
        target_hit = bar.low <= target
        if stop_hit and target_hit:
            return OutcomeResult(signal_id, "LOSS", stop, -1.0, "both_hit_stop_first")
        if stop_hit:
            return OutcomeResult(signal_id, "LOSS", stop, -1.0, "stop_loss")
        if target_hit:
            return OutcomeResult(signal_id, "WIN", target, (entry - target) / risk, "target")
    else:
        raise ValueError(f"unsupported direction: {direction}")
    if bars_held >= max_hold_bars:
        return OutcomeResult(signal_id, "TIMEOUT", bar.close, (bar.close - entry) / risk if direction == "long" else (entry - bar.close) / risk, "max_hold")
    return OutcomeResult(signal_id, "PENDING", bar.close, 0.0, "pending")
