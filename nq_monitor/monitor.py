"""One-shot paper monitor and alert formatter for NQ/MNQ."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Sequence

from .feed import YahooMinuteFeed, align_by_timestamp
from .instruments import resolve_instrument
from .journal import SignalJournal
from .models import PaperSignal
from .outcomes import resolve_signal_on_bar
from .session import session_status
from .strategy import StrategyConfig, evaluate_setup


@dataclass(frozen=True)
class MonitorConfig:
    requested_labels: tuple[str, ...] = ("NQ", "MNQ")
    minimum_bars: int = 80
    max_data_age_minutes: float = 20.0
    journal_path: Path = Path("<NQ_PD_MONITOR_ROOT>/data/journal.sqlite3")
    strategy: StrategyConfig = field(default_factory=StrategyConfig)


@dataclass(frozen=True)
class MarketContext:
    """Read-only context attached to every emitted paper alert."""

    timestamp: datetime
    nq_level: float
    mnq_level: float
    nq_trend: str
    mnq_trend: str
    nq_liquidity_low: float
    nq_liquidity_high: float
    mnq_liquidity_low: float
    mnq_liquidity_high: float
    pair_trend: str


@dataclass(frozen=True)
class ScanResult:
    status: str
    messages: tuple[str, ...] = ()
    data_status: str = "NOT_FETCHED"


def _signal_row(signal: PaperSignal) -> dict[str, object]:
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "signal_timestamp": signal.signal_timestamp.isoformat(),
        "sweep_timestamp": signal.sweep_timestamp.isoformat(),
        "cisd_timestamp": signal.cisd_timestamp.isoformat(),
        "entry_price": signal.entry_price,
        "stop_loss_price": signal.stop_loss_price,
        "target_price": signal.target_price,
        "risk_points": signal.risk_points,
        "liquidity_level": signal.liquidity_level,
        "pd_kind": signal.pd_kind,
        "pd_low": signal.pd_low,
        "pd_high": signal.pd_high,
        "requirements_json": json.dumps(dict(signal.requirements), sort_keys=True),
        "evidence_json": json.dumps(dict(signal.evidence), sort_keys=True),
        "order_mode": signal.order_mode,
    }


def _trend_label(candles: Sequence, index: int, lookback: int = 20, slope_bars: int = 5) -> str:
    current = candles[index].close
    start = max(0, index - lookback + 1)
    baseline = sum(candle.close for candle in candles[start:index + 1]) / (index - start + 1)
    prior = candles[max(0, index - slope_bars)].close
    if current > baseline and current > prior:
        return "BULLISH"
    if current < baseline and current < prior:
        return "BEARISH"
    return "NEUTRAL"


def _liquidity_bounds(candles: Sequence, index: int, lookback: int) -> tuple[float, float]:
    start = max(0, index - lookback)
    prior = candles[start:index]
    if not prior:
        prior = candles[start:index + 1]
    return min(candle.low for candle in prior), max(candle.high for candle in prior)


def build_market_context(
    nq: Sequence,
    mnq: Sequence,
    index: int,
    config: StrategyConfig,
) -> MarketContext:
    """Build informational NQ/MNQ levels without changing entry rules."""
    if index < 0 or index >= len(nq) or index >= len(mnq):
        raise ValueError("market context index is outside aligned bars")
    nq_trend = _trend_label(nq, index)
    mnq_trend = _trend_label(mnq, index)
    if nq_trend == mnq_trend:
        pair_trend = f"ALIGNED_{nq_trend}"
    else:
        pair_trend = "DIVERGENT"
    nq_low, nq_high = _liquidity_bounds(nq, index, config.liquidity_lookback)
    mnq_low, mnq_high = _liquidity_bounds(mnq, index, config.liquidity_lookback)
    return MarketContext(
        timestamp=nq[index].timestamp,
        nq_level=nq[index].close,
        mnq_level=mnq[index].close,
        nq_trend=nq_trend,
        mnq_trend=mnq_trend,
        nq_liquidity_low=nq_low,
        nq_liquidity_high=nq_high,
        mnq_liquidity_low=mnq_low,
        mnq_liquidity_high=mnq_high,
        pair_trend=pair_trend,
    )


def format_market_context(context: MarketContext, data_status: str) -> str:
    return (
        f"MARKET CONTEXT | NQ level {context.nq_level:.2f} | trend {context.nq_trend} | "
        f"NQ liquidity low {context.nq_liquidity_low:.2f} / high {context.nq_liquidity_high:.2f} | "
        f"MNQ level {context.mnq_level:.2f} | trend {context.mnq_trend} | "
        f"MNQ liquidity low {context.mnq_liquidity_low:.2f} / high {context.mnq_liquidity_high:.2f} | "
        f"pair trend {context.pair_trend} | {data_status}"
    )


def format_signal_alert(
    signal: PaperSignal,
    data_status: str,
    context: MarketContext | None = None,
) -> str:
    context_line = f"{format_market_context(context, data_status)}\n\n" if context else ""
    liquidity_side = "sell_side" if signal.direction == "long" else "buy_side"
    sweep_extreme = float(signal.evidence.get("sweep_extreme", signal.stop_loss_price))
    return context_line + (
        f"PAPER ONLY | {signal.symbol} {signal.direction.upper()} | "
        f"SMT+LIQUIDITY_SWEEP+CISD+PD_ARRAY confirmed | "
        f"swept {liquidity_side} liquidity {signal.liquidity_level:.2f} "
        f"(extreme {sweep_extreme:.2f}) | PD {signal.pd_kind} "
        f"{signal.pd_low:.2f}-{signal.pd_high:.2f} | "
        f"entry {signal.entry_price:g} | stop-loss {signal.stop_loss_price:g} "
        f"(under/over sweep extreme) | target {signal.target_price:g} "
        f"({signal.risk_points:g} pts risk, {data_status}) | no broker order"
    )


def format_outcome_alert(row, result, data_status: str, context: MarketContext | None = None) -> str:
    context_line = f"{format_market_context(context, data_status)}\n\n" if context else ""
    return context_line + (
        f"PAPER RESULT | {row['symbol']} {row['direction'].upper()} | "
        f"{result.outcome} | exit {result.exit_price:g} | "
        f"{result.r_multiple:.2f}R | {result.reason} | {data_status}"
    )


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _closed_bars(bars, now: datetime):
    cutoff = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return [bar for bar in bars if bar.timestamp < cutoff]


def _resolve_open_outcomes(
    journal: SignalJournal,
    bars_by_symbol: dict[str, list],
    max_hold: int,
    data_status: str,
    context: MarketContext | None = None,
) -> list[str]:
    messages: list[str] = []
    for row in journal.open_signals():
        bars = bars_by_symbol.get(row["symbol"], [])
        entry_time = _parse_ts(row["signal_timestamp"])
        future_bars = [bar for bar in bars if bar.timestamp > entry_time]
        for bars_held, bar in enumerate(future_bars, start=1):
            result = resolve_signal_on_bar(row, bar, bars_held, max_hold)
            if result.outcome == "PENDING":
                continue
            outcome_row = {
                "signal_id": result.signal_id,
                "outcome": result.outcome,
                "exit_timestamp": bar.timestamp.isoformat(),
                "exit_price": result.exit_price,
                "r_multiple": result.r_multiple,
                "reason": result.reason,
            }
            if journal.record_outcome(outcome_row):
                messages.append(format_outcome_alert(row, result, data_status, context))
            break
    return messages


def scan_once(
    *,
    now: datetime,
    feed=None,
    journal: SignalJournal | None = None,
    config: MonitorConfig | None = None,
) -> ScanResult:
    config = config or MonitorConfig()
    status = session_status(now)
    if status != "OPEN":
        return ScanResult(status=status)
    feed = feed or YahooMinuteFeed()
    owns_journal = journal is None
    journal = journal or SignalJournal(config.journal_path)
    try:
        instruments = [resolve_instrument(label) for label in config.requested_labels]
        active = [instrument for instrument in instruments if instrument.source_symbol]
        if len(active) < 2:
            return ScanResult(status="UNRESOLVED_SYMBOL", data_status="NO_DATA")
        bars_by_symbol = {}
        for instrument in active:
            bars_by_symbol[instrument.requested_label] = feed.fetch(
                instrument.source_symbol, instrument.requested_label, now=now
            )
        closed = {key: _closed_bars(value, now) for key, value in bars_by_symbol.items()}
        if any(len(value) < config.minimum_bars for value in closed.values()):
            return ScanResult(status="INSUFFICIENT_DATA", data_status="NO_DATA")
        latest = min(value[-1].timestamp for value in closed.values())
        age_minutes = (now.astimezone(timezone.utc) - latest).total_seconds() / 60
        if age_minutes < 0 or age_minutes > config.max_data_age_minutes:
            return ScanResult(status="STALE_DATA", data_status=f"STALE_{age_minutes:.1f}M")
        data_status = "DELAYED_PROXY"
        n, m = align_by_timestamp(closed["NQ"], closed["MNQ"])
        if len(n) < config.minimum_bars:
            return ScanResult(status="MISALIGNED_DATA", data_status=data_status)
        index = len(n) - 1
        context = build_market_context(n, m, index, config.strategy)
        messages = _resolve_open_outcomes(
            journal,
            {"NQ": n, "MNQ": m},
            config.strategy.max_hold_bars,
            data_status,
            context,
        )
        for primary, secondary, symbol in ((n, m, "NQ"), (m, n, "MNQ")):
            signal = evaluate_setup(primary, secondary, symbol, index, config.strategy)
            if signal is None:
                continue
            if journal.record_signal(_signal_row(signal)):
                message = format_signal_alert(signal, data_status, context)
                if journal.record_alert(f"signal:{signal.signal_id}", message, signal.signal_id):
                    messages.append(message)
        return ScanResult(status="OPEN", messages=tuple(messages), data_status=data_status)
    except Exception as exc:
        return ScanResult(status="DATA_ERROR", messages=(f"NQ/MNQ paper monitor blocked: {type(exc).__name__}: {exc}",), data_status="ERROR")
    finally:
        if owns_journal:
            journal.close()
