"""Deterministic, paper-only NQ/MNQ signal rules.

This module deliberately has no broker or order-submission capability. It
constructs a hypothetical order only after every required condition is true.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Optional, Sequence

from .models import Candle, CISDEvent, PDArray, PaperSignal, SMTEvent, SweepEvent


@dataclass(frozen=True)
class StrategyConfig:
    tick_size: float = 0.25
    target_r: float = 2.0
    liquidity_lookback: int = 20
    cisd_window: int = 3
    atr_period: int = 14
    min_body_atr: float = 0.5
    pd_lookback: int = 20
    htf_lookback: int = 60
    smt_lookback: int = 5
    smt_tolerance_ticks: int = 1
    max_hold_bars: int = 30


def _average_true_range(candles: Sequence[Candle], index: int, period: int) -> float:
    if index <= 0:
        return 0.0
    start = max(1, index - period + 1)
    ranges = []
    for i in range(start, index + 1):
        previous = candles[i - 1].close
        current = candles[i]
        ranges.append(max(current.high - current.low,
                         abs(current.high - previous),
                         abs(current.low - previous)))
    return sum(ranges) / len(ranges) if ranges else 0.0


def detect_liquidity_sweep(
    candles: Sequence[Candle], index: int, config: StrategyConfig
) -> Optional[SweepEvent]:
    """Detect a closed-bar raid of the prior rolling high or low."""
    if index < config.liquidity_lookback:
        return None
    current = candles[index]
    prior = candles[index - config.liquidity_lookback:index]
    prior_low = min(c.low for c in prior)
    prior_high = max(c.high for c in prior)
    if current.low < prior_low - config.tick_size and current.close > prior_low:
        return SweepEvent(
            direction="long",
            liquidity_side="sell_side",
            index=index,
            timestamp=current.timestamp,
            liquidity_level=prior_low,
            extreme=current.low,
        )
    if current.high > prior_high + config.tick_size and current.close < prior_high:
        return SweepEvent(
            direction="short",
            liquidity_side="buy_side",
            index=index,
            timestamp=current.timestamp,
            liquidity_level=prior_high,
            extreme=current.high,
        )
    return None


def detect_smt(
    primary: Sequence[Candle],
    secondary: Sequence[Candle],
    index: int,
    direction: str,
    config: StrategyConfig,
) -> Optional[SMTEvent]:
    """Detect non-confirmation between aligned NQ and MNQ extremes."""
    if index < config.smt_lookback or index >= len(primary) or index >= len(secondary):
        return None
    if primary[index].timestamp != secondary[index].timestamp:
        return None
    p_prev = primary[index - config.smt_lookback:index]
    s_prev = secondary[index - config.smt_lookback:index]
    tolerance = config.smt_tolerance_ticks * config.tick_size
    if direction == "long":
        p_level = min(c.low for c in p_prev)
        s_level = min(c.low for c in s_prev)
        p_break = primary[index].low < p_level - config.tick_size
        s_break = secondary[index].low < s_level - config.tick_size
        if p_break and not s_break and secondary[index].low >= s_level - tolerance:
            return SMTEvent("long", index, primary[index].timestamp, "NQ", "MNQ",
                            primary[index].low, secondary[index].low)
        if s_break and not p_break and primary[index].low >= p_level - tolerance:
            return SMTEvent("long", index, primary[index].timestamp, "MNQ", "NQ",
                            secondary[index].low, primary[index].low)
    elif direction == "short":
        p_level = max(c.high for c in p_prev)
        s_level = max(c.high for c in s_prev)
        p_break = primary[index].high > p_level + config.tick_size
        s_break = secondary[index].high > s_level + config.tick_size
        if p_break and not s_break and secondary[index].high <= s_level + tolerance:
            return SMTEvent("short", index, primary[index].timestamp, "NQ", "MNQ",
                            primary[index].high, secondary[index].high)
        if s_break and not p_break and primary[index].high <= p_level + tolerance:
            return SMTEvent("short", index, primary[index].timestamp, "MNQ", "NQ",
                            secondary[index].high, primary[index].high)
    return None


def detect_cisd(
    candles: Sequence[Candle],
    sweep: SweepEvent,
    index: int,
    config: StrategyConfig,
) -> Optional[CISDEvent]:
    """Require a later displacement close back through swept liquidity."""
    if index <= sweep.index or index - sweep.index > config.cisd_window:
        return None
    current = candles[index]
    atr = _average_true_range(candles, index, config.atr_period)
    body = abs(current.close - current.open)
    if atr <= 0 or body < config.min_body_atr * atr:
        return None
    if sweep.direction == "long":
        if current.close > sweep.liquidity_level + config.tick_size and current.close > current.open:
            return CISDEvent("long", index, current.timestamp, current.close, sweep.liquidity_level)
    elif sweep.direction == "short":
        if current.close < sweep.liquidity_level - config.tick_size and current.close < current.open:
            return CISDEvent("short", index, current.timestamp, current.close, sweep.liquidity_level)
    return None


def find_pd_array(
    candles: Sequence[Candle], index: int, direction: str, config: StrategyConfig
) -> Optional[PDArray]:
    """Find a touched FVG in the correct premium/discount half."""
    if index < 2:
        return None
    htf = candles[max(0, index - config.htf_lookback):index + 1]
    if not htf:
        return None
    equilibrium = (max(c.high for c in htf) + min(c.low for c in htf)) / 2
    start = max(2, index - config.pd_lookback + 1)
    current = candles[index]
    for j in range(index, start - 1, -1):
        left, middle, right = candles[j - 2], candles[j - 1], candles[j]
        kind = None
        low = high = None
        if left.high + config.tick_size <= right.low:
            kind, low, high = "bullish_fvg", left.high, right.low
        elif left.low - config.tick_size >= right.high:
            kind, low, high = "bearish_fvg", right.high, left.low
        if kind is None:
            continue
        zone_direction = "long" if kind.startswith("bullish") else "short"
        midpoint = (low + high) / 2
        in_correct_half = midpoint <= equilibrium if direction == "long" else midpoint >= equilibrium
        touched = current.low <= high and current.high >= low
        if zone_direction == direction and in_correct_half and touched:
            return PDArray(direction, kind, j, low, high, equilibrium)
    return None


def evaluate_setup(
    primary: Sequence[Candle],
    secondary: Sequence[Candle],
    symbol: str,
    index: int,
    config: StrategyConfig,
) -> Optional[PaperSignal]:
    """Evaluate the latest closed bar without looking past ``index``."""
    if index < 0 or index >= len(primary) or index >= len(secondary):
        return None
    for sweep_index in range(max(config.liquidity_lookback, index - config.cisd_window), index):
        sweep = detect_liquidity_sweep(primary, sweep_index, config)
        if sweep is None:
            continue
        smt = detect_smt(primary, secondary, sweep_index, sweep.direction, config)
        cisd = detect_cisd(primary, sweep, index, config)
        pd_array = find_pd_array(primary, sweep_index, sweep.direction, config)
        signal = assemble_paper_signal(
            symbol=symbol,
            entry_price=primary[index].close,
            sweep=sweep,
            smt=smt,
            cisd=cisd,
            pd_array=pd_array,
            config=config,
        )
        if signal is not None:
            return signal
    return None


def assemble_paper_signal(
    *,
    symbol: str,
    entry_price: float,
    sweep: Optional[SweepEvent],
    smt: Optional[SMTEvent],
    cisd: Optional[CISDEvent],
    pd_array: Optional[PDArray],
    config: StrategyConfig,
) -> Optional[PaperSignal]:
    """Build a hypothetical order only when all four gates are present."""
    requirements = {
        "smt": smt is not None,
        "liquidity_sweep": sweep is not None,
        "cisd": cisd is not None,
        "pd_array": pd_array is not None,
    }
    if not all(requirements.values()):
        return None
    assert sweep is not None
    assert smt is not None
    assert cisd is not None
    assert pd_array is not None
    if not (sweep.direction == smt.direction == cisd.direction == pd_array.direction):
        return None
    if cisd.index <= sweep.index:
        return None

    if sweep.direction == "long":
        stop_loss_price = sweep.extreme - config.tick_size
        risk_points = entry_price - stop_loss_price
        target_price = entry_price + config.target_r * risk_points
    elif sweep.direction == "short":
        stop_loss_price = sweep.extreme + config.tick_size
        risk_points = stop_loss_price - entry_price
        target_price = entry_price - config.target_r * risk_points
    else:
        return None
    if risk_points <= 0:
        return None

    identity = {
        "ruleset": "smt_liquidity_cisd_pd_v1",
        "symbol": symbol,
        "direction": sweep.direction,
        "sweep_timestamp": sweep.timestamp.isoformat(),
    }
    signal_id = sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    evidence = {
        "smt_leader": smt.leader,
        "smt_laggard": smt.laggard,
        "liquidity_level": sweep.liquidity_level,
        "sweep_extreme": sweep.extreme,
        "cisd_confirmation_level": cisd.confirmation_level,
        "pd_kind": pd_array.kind,
        "pd_low": pd_array.low,
        "pd_high": pd_array.high,
        "equilibrium": pd_array.equilibrium,
    }
    return PaperSignal(
        signal_id=signal_id,
        symbol=symbol,
        direction=sweep.direction,
        action="PAPER_ENTRY",
        order_mode="paper_only",
        signal_timestamp=cisd.timestamp,
        sweep_timestamp=sweep.timestamp,
        cisd_timestamp=cisd.timestamp,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        risk_points=risk_points,
        liquidity_level=sweep.liquidity_level,
        pd_kind=pd_array.kind,
        pd_low=pd_array.low,
        pd_high=pd_array.high,
        requirements=requirements,
        evidence=evidence,
    )
