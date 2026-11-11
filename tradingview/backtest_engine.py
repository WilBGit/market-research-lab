"""Deterministic, walk-forward-safe 1-minute research backtest for the Pine companion."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo
import json
import math
import time

import requests


NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class SimConfig:
    htf_minutes: int = 15
    wait_bars: int = 20
    body_atr: float = 0.50
    fvg_atr: float = 0.10
    stop_ticks: int = 1
    target_r: float = 1.50
    be_r: float = 1.00
    max_hold: int = 60
    max_entries_day: int = 3
    require_smt: bool = True
    require_fvg: bool = True
    require_pd: bool = True
    tick: float = 0.25
    slippage_ticks: int = 1
    session_open: int = 8 * 60 + 30
    session_close: int = 11 * 60 + 30


@dataclass
class Trade:
    direction: int
    entry: float
    stop: float
    target: float
    entry_index: int
    signal_index: int = -1
    signal_ts: int = 0


@dataclass(frozen=True)
class TradeResult:
    direction: int
    signal_ts: int
    entry_ts: int
    exit_ts: int
    entry: float
    exit: float
    stop: float
    target: float
    r_multiple: float
    reason: str


@dataclass(frozen=True)
class ScheduledOrder:
    fill_index: int
    direction: int
    entry: float
    stop: float
    target_r: float
    signal_index: int
    signal_ts: int


def in_new_york_session(ts: int, open_minute: int = 510, close_minute: int = 690) -> bool:
    dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(NY)
    minute = dt.hour * 60 + dt.minute
    return open_minute <= minute <= close_minute


def _clean_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_yahoo_chart(payload: dict) -> list[Bar]:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"yahoo_chart_error:{chart['error']}")
    result = chart.get("result") or []
    if not result:
        raise ValueError("yahoo_chart_empty")
    item = result[0]
    timestamps = item.get("timestamp") or []
    quotes = ((item.get("indicators") or {}).get("quote") or [{}])[0]
    fields = {name: quotes.get(name) or [] for name in ("open", "high", "low", "close", "volume")}
    bars: list[Bar] = []
    for i, ts in enumerate(timestamps):
        values = [_clean_number(fields[name][i] if i < len(fields[name]) else None) for name in fields]
        if any(value is None for value in values[:4]):
            continue
        volume = values[4] or 0.0
        bars.append(Bar(int(ts), values[0], values[1], values[2], values[3], volume))
    if not bars:
        raise ValueError("yahoo_chart_missing_quote_series")
    return bars


def fetch_yahoo_1m(symbol: str, days: int = 7, cache_dir: str | Path | None = None) -> list[Bar]:
    now = int(time.time())
    start = now - days * 86400
    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir) / f"{symbol.replace('=', '_')}_1m_{days}d.json"
        if cache_path.exists():
            return parse_yahoo_chart(json.loads(cache_path.read_text()))
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?period1={start}&period2={now}&interval=1m&events=history&includePrePost=true"
    )
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
    response.raise_for_status()
    payload = response.json()
    bars = parse_yahoo_chart(payload)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return bars


def _true_range(current: Bar, previous_close: float | None) -> float:
    if previous_close is None:
        return current.high - current.low
    return max(current.high - current.low, abs(current.high - previous_close), abs(current.low - previous_close))


def atr_series(bars: list[Bar], period: int = 14) -> list[float | None]:
    values: list[float] = []
    result: list[float | None] = [None] * len(bars)
    previous_close: float | None = None
    for bar in bars:
        values.append(_true_range(bar, previous_close))
        previous_close = bar.close
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    for i in range(period, len(values)):
        current = (current * (period - 1) + values[i]) / period
        result[i] = current
    return result


def _bucket(ts: int, minutes: int) -> int:
    seconds = minutes * 60
    return ts - (ts % seconds)


def aggregate_htf(bars: list[Bar], minutes: int) -> dict[int, Bar]:
    groups: dict[int, list[Bar]] = {}
    for bar in bars:
        groups.setdefault(_bucket(bar.ts, minutes), []).append(bar)
    result: dict[int, Bar] = {}
    for bucket, group in groups.items():
        result[bucket] = Bar(
            bucket,
            group[0].open,
            max(x.high for x in group),
            min(x.low for x in group),
            group[-1].close,
            sum(x.volume for x in group),
        )
    return result


def previous_htf_map(htf: dict[int, Bar]) -> dict[int, Bar]:
    keys = sorted(htf)
    return {keys[i]: htf[keys[i - 1]] for i in range(1, len(keys))}


def schedule_market_entry(
    bars: list[Bar], signal_index: int, direction: int, stop: float, target_r: float, tick: float = 0.25, slippage_ticks: int = 0
) -> ScheduledOrder:
    fill_index = signal_index + 1
    if fill_index >= len(bars):
        raise IndexError("no_future_bar_for_entry")
    slip = tick * slippage_ticks * direction
    entry = bars[fill_index].open + slip
    return ScheduledOrder(fill_index, direction, entry, stop, target_r, signal_index, bars[signal_index].ts)


def simulate_exit(trade: Trade, bars: list[Bar], start_index: int, cfg: SimConfig) -> TradeResult:
    risk = abs(trade.entry - trade.stop)
    if risk <= 0:
        raise ValueError("nonpositive_trade_risk")
    stop = trade.stop
    target = trade.target
    be_armed = False
    slip = cfg.tick * cfg.slippage_ticks
    for i in range(start_index, len(bars)):
        bar = bars[i]
        hit_stop = bar.low <= stop if trade.direction == 1 else bar.high >= stop
        hit_target = bar.high >= target if trade.direction == 1 else bar.low <= target
        # OHLC cannot reveal intrabar ordering. Count a double-touch as the stop.
        if hit_stop:
            exit_price = stop - slip if trade.direction == 1 else stop + slip
            r = (exit_price - trade.entry) / risk * trade.direction
            return TradeResult(trade.direction, trade.signal_ts, bars[trade.entry_index].ts, bar.ts, trade.entry, exit_price, trade.stop, target, r, "stop")
        if hit_target:
            exit_price = target - slip if trade.direction == 1 else target + slip
            r = (exit_price - trade.entry) / risk * trade.direction
            return TradeResult(trade.direction, trade.signal_ts, bars[trade.entry_index].ts, bar.ts, trade.entry, exit_price, trade.stop, target, r, "target")
        if cfg.be_r > 0 and not be_armed:
            be_armed = bar.high >= trade.entry + risk * cfg.be_r if trade.direction == 1 else bar.low <= trade.entry - risk * cfg.be_r
            if be_armed:
                stop = trade.entry
        if i - trade.entry_index >= cfg.max_hold:
            exit_price = bar.close - slip if trade.direction == 1 else bar.close + slip
            r = (exit_price - trade.entry) / risk * trade.direction
            return TradeResult(trade.direction, trade.signal_ts, bars[trade.entry_index].ts, bar.ts, trade.entry, exit_price, trade.stop, target, r, "time")
    bar = bars[-1]
    exit_price = bar.close - slip if trade.direction == 1 else bar.close + slip
    r = (exit_price - trade.entry) / risk * trade.direction
    return TradeResult(trade.direction, trade.signal_ts, bars[trade.entry_index].ts, bar.ts, trade.entry, exit_price, trade.stop, target, r, "end")


def run_strategy(target_bars: list[Bar], smt_bars: list[Bar], cfg: SimConfig) -> list[TradeResult]:
    if not target_bars:
        return []
    target_bars = sorted(target_bars, key=lambda x: x.ts)
    smt_by_ts = {bar.ts: bar for bar in smt_bars}
    target_htf = aggregate_htf(target_bars, cfg.htf_minutes)
    smt_htf = aggregate_htf(smt_bars, cfg.htf_minutes)
    prior_target = previous_htf_map(target_htf)
    prior_smt = previous_htf_map(smt_htf)
    atr = atr_series(target_bars)

    results: list[TradeResult] = []
    index_by_ts = {bar.ts: idx for idx, bar in enumerate(target_bars)}
    direction = 0
    sweep_index = -10**9
    extreme: float | None = None
    cisd: float | None = None
    smt_ok = False
    fvg_ok = False
    last_htf: int | None = None
    last_bear_open: float | None = None
    last_bull_open: float | None = None
    day_key = None
    entries_today = 0
    i = 0
    while i < len(target_bars):
        bar = target_bars[i]
        bucket = _bucket(bar.ts, cfg.htf_minutes)
        p = prior_target.get(bucket)
        es_p = prior_smt.get(bucket)
        es_bar = smt_by_ts.get(bar.ts)
        current_atr = atr[i]
        if p is None or es_p is None or es_bar is None or current_atr is None:
            if bar.close < bar.open:
                last_bear_open = bar.open
            elif bar.close > bar.open:
                last_bull_open = bar.open
            i += 1
            continue

        ny_date = datetime.fromtimestamp(bar.ts, timezone.utc).astimezone(NY).date()
        if day_key != ny_date:
            day_key = ny_date
            entries_today = 0

        long_raw = bar.low < p.low and bar.close > p.low
        short_raw = bar.high > p.high and bar.close < p.high
        long_sweep = long_raw and (last_htf is None or p.ts != last_htf)
        short_sweep = short_raw and (last_htf is None or p.ts != last_htf)
        if long_sweep or short_sweep:
            last_htf = p.ts
        smt_long = long_sweep and es_bar.low >= es_p.low
        smt_short = short_sweep and es_bar.high <= es_p.high

        if long_sweep:
            direction = 1
            sweep_index = i
            extreme = bar.low
            cisd = bar.open if bar.close < bar.open else last_bear_open
            smt_ok = smt_long
            fvg_ok = False
        if short_sweep:
            direction = -1
            sweep_index = i
            extreme = bar.high
            cisd = bar.open if bar.close > bar.open else last_bull_open
            smt_ok = smt_short
            fvg_ok = False
        if direction and (i - sweep_index > cfg.wait_bars or (direction == 1 and bar.close < (extreme or -math.inf)) or (direction == -1 and bar.close > (extreme or math.inf))):
            direction = 0
        if direction == 1 and cisd is not None and bar.close > cisd and bar.close > bar.open and abs(bar.close - bar.open) >= cfg.body_atr * current_atr:
            # The Pine version stores CISD confirmation, so this remains true for the context.
            cisd_confirmed = True
        else:
            cisd_confirmed = locals().get("cisd_confirmed", False) if direction == 1 else False
        if direction == -1 and cisd is not None and bar.close < cisd and bar.close < bar.open and abs(bar.close - bar.open) >= cfg.body_atr * current_atr:
            cisd_confirmed_short = True
        else:
            cisd_confirmed_short = locals().get("cisd_confirmed_short", False) if direction == -1 else False
        if direction == 1 and i >= 2 and bar.low > target_bars[i - 2].high and bar.low - target_bars[i - 2].high >= cfg.fvg_atr * current_atr:
            fvg_ok = True
        if direction == -1 and i >= 2 and bar.high < target_bars[i - 2].low and target_bars[i - 2].low - bar.high >= cfg.fvg_atr * current_atr:
            fvg_ok = True

        equilibrium = (p.high + p.low) / 2.0
        core_long = direction == 1 and cisd_confirmed and (not cfg.require_smt or smt_ok) and (not cfg.require_fvg or fvg_ok) and (not cfg.require_pd or bar.close < equilibrium)
        core_short = direction == -1 and cisd_confirmed_short and (not cfg.require_smt or smt_ok) and (not cfg.require_fvg or fvg_ok) and (not cfg.require_pd or bar.close > equilibrium)
        in_session = in_new_york_session(bar.ts, cfg.session_open, cfg.session_close)
        if in_session and entries_today < cfg.max_entries_day and (core_long or core_short) and i + 1 < len(target_bars):
            d = 1 if core_long and not core_short else -1 if core_short and not core_long else 0
            planned_stop = (extreme - cfg.stop_ticks * cfg.tick) if d == 1 else (extreme + cfg.stop_ticks * cfg.tick) if d == -1 else None
            if d and planned_stop is not None:
                order = schedule_market_entry(target_bars, i, d, planned_stop, cfg.target_r, cfg.tick, cfg.slippage_ticks)
                risk = abs(order.entry - order.stop)
                if risk > 0:
                    target = order.entry + d * risk * order.target_r
                    trade = Trade(d, order.entry, order.stop, target, order.fill_index, order.signal_index, order.signal_ts)
                    result = simulate_exit(trade, target_bars, order.fill_index, cfg)
                    results.append(result)
                    entries_today += 1
                    direction = 0
                    # No overlapping position. Preserve rolling ta.valuewhen state
                    # across the bars consumed by the position simulation.
                    exit_index = index_by_ts[result.exit_ts]
                    for skipped_bar in target_bars[i + 1 : exit_index + 1]:
                        if skipped_bar.close < skipped_bar.open:
                            last_bear_open = skipped_bar.open
                        elif skipped_bar.close > skipped_bar.open:
                            last_bull_open = skipped_bar.open
                    i = exit_index + 1
                    continue
        if bar.close < bar.open:
            last_bear_open = bar.open
        elif bar.close > bar.open:
            last_bull_open = bar.open
        i += 1
    return results


def summarize(results: Iterable[TradeResult], split_ts: int | None = None) -> dict:
    rows = list(results)
    def stats(items: list[TradeResult]) -> dict:
        wins = sum(x.r_multiple > 0 for x in items)
        losses = sum(x.r_multiple < 0 for x in items)
        flats = len(items) - wins - losses
        gross_profit = sum(x.r_multiple for x in items if x.r_multiple > 0)
        gross_loss = sum(x.r_multiple for x in items if x.r_multiple < 0)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in items:
            equity += x.r_multiple
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        resolved = wins + losses
        return {
            "trades": len(items),
            "wins": wins,
            "losses": losses,
            "flats": flats,
            "win_rate_pct": (wins / resolved * 100.0) if resolved else None,
            "net_r": sum(x.r_multiple for x in items),
            "profit_factor": (gross_profit / abs(gross_loss)) if gross_loss else None,
            "max_drawdown_r": max_dd,
            "avg_r": (sum(x.r_multiple for x in items) / len(items)) if items else None,
        }
    out = {"all": stats(rows)}
    if split_ts is not None:
        out["in_sample"] = stats([x for x in rows if x.signal_ts < split_ts])
        out["out_of_sample"] = stats([x for x in rows if x.signal_ts >= split_ts])
    return out


def config_dict(cfg: SimConfig) -> dict:
    return asdict(cfg)
