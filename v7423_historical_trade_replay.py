#!/usr/bin/env python3
"""Read-only V7.4.23 replay over archived paper-trade entries.

The script intentionally separates a reproducible *entry-gate replay* from a
full counterfactual backtest.  It uses the current V7.4.23 signal code on
historical five-minute Yahoo bars at each archived signal-bar timestamp and
uses recorded entry/exit prices only as an outcome proxy.  It never writes to
V7 state, ledgers, release controls, or broker endpoints.

A full exit counterfactual requires every option quote observed while a
position was open.  The archive currently has only partial quote paths, so the
script measures that coverage instead of inventing alternative exit prices.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCRIPTS_DIR = Path.home() / ".hermes" / "scripts"
DEFAULT_STATE_DIR = Path.home() / ".hermes" / "state"
DEFAULT_DB_DIR = Path.home() / ".hermes" / "trading-db"


def parse_timestamp(value: Any) -> datetime | None:
    """Return an ET-aware datetime for an ISO-8601 event field."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_engine(scripts_dir: Path = DEFAULT_SCRIPTS_DIR) -> Any:
    scripts_path = str(scripts_dir)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    return importlib.import_module("v7_scalp_paper")


def evaluate_stored_entry(entry: dict[str, Any], engine: Any | None = None) -> dict[str, Any]:
    """Apply V7.4.23's structural post-signal gates to a recorded signal.

    This is a deterministic ledger-field audit only.  It does not replace the
    bar-based replay, but provides a data-complete fallback where the archived
    signal has the fields V7.4.23 must inspect.
    """
    if engine is None:
        engine = load_engine()
    signal = entry.get("signal")
    expected_direction = entry.get("direction")
    symbol = str(entry.get("symbol") or "")
    if not isinstance(signal, dict) or expected_direction not in {"bullish", "bearish"}:
        return {"status": "unobservable", "reason": "missing_recorded_signal"}
    if signal.get("direction") != expected_direction:
        return {"status": "rejected", "reason": "recorded_direction_mismatch"}

    turtle = engine._actual_turtle_breakout(signal)
    if turtle is None:
        return {"status": "unobservable", "reason": "signal_turtle_unavailable"}
    if turtle is False:
        return {"status": "rejected", "reason": "signal_nonstructural_turtle"}

    direction_checks = signal.get("bullChecks" if expected_direction == "bullish" else "bearChecks")
    if not isinstance(direction_checks, dict):
        return {"status": "unobservable", "reason": "signal_checks_unavailable"}
    relative_volume = finite_number(signal.get("relativeVolume"))
    if relative_volume is None:
        return {"status": "unobservable", "reason": "signal_relative_volume_unavailable"}
    if relative_volume < 1.0:
        return {"status": "rejected", "reason": "relative_volume_below_1"}

    gated = engine.apply_symbol_entry_score_gate(dict(signal), symbol=symbol)
    gated = engine.apply_structural_entry_gate(gated)
    if gated.get("direction") not in {"bullish", "bearish"}:
        reasons = gated.get("rejectionReasons") or []
        return {
            "status": "rejected",
            "reason": str(reasons[-1]) if reasons else "entry_gate_rejected",
            "rejectionReasons": reasons,
        }
    if gated.get("direction") != expected_direction:
        return {"status": "rejected", "reason": "current_direction_mismatch"}
    return {"status": "accepted", "reason": "accepted"}


def load_closed_positions(state_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair raw entry and exit events without altering their source ledgers."""
    entries: dict[str, dict[str, Any]] = {}
    exits: dict[str, dict[str, Any]] = {}
    ledger_hashes: dict[str, str] = {}
    for ledger in sorted(state_dir.glob("v7_scalp_trades_*.jsonl")):
        ledger_hashes[str(ledger)] = sha256_file(ledger)
        with ledger.open() as source:
            for raw in source:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                position_id = event.get("positionId")
                if not isinstance(position_id, str) or not position_id:
                    continue
                if event.get("event") == "PAPER_ENTRY_REAL_ASK":
                    entries[position_id] = event
                elif event.get("event") in {"PAPER_EXIT_REAL_BID", "PAPER_EXIT"}:
                    exits[position_id] = event

    positions: list[dict[str, Any]] = []
    missing_exit: list[str] = []
    for position_id, entry in sorted(entries.items(), key=lambda item: item[1].get("entryTime", "")):
        exit_event = exits.get(position_id)
        if exit_event is None:
            missing_exit.append(position_id)
            continue
        entry_time = parse_timestamp(entry.get("entryTime") or entry.get("timestamp"))
        exit_time = parse_timestamp(exit_event.get("exitTime") or exit_event.get("timestamp"))
        if entry_time is None or exit_time is None:
            continue
        positions.append(
            {
                "positionId": position_id,
                "entry": entry,
                "exit": exit_event,
                "entryTime": entry_time,
                "exitTime": exit_time,
            }
        )
    return positions, {
        "ledgers": ledger_hashes,
        "entryEvents": len(entries),
        "exitEvents": len(exits),
        "closedPairs": len(positions),
        "missingExitPositionIds": missing_exit,
    }


def replay_time(position: dict[str, Any]) -> datetime:
    """Use the captured completed signal-bar end to prevent look-ahead bias."""
    signal = position["entry"].get("signal")
    if isinstance(signal, dict):
        bar_end = parse_timestamp(signal.get("signalBarEnd"))
        if bar_end is not None and bar_end <= position["entryTime"]:
            return bar_end
    return position["entryTime"]


def yahoo_chart_url(symbol: str, start: datetime, end: datetime) -> str:
    start_epoch = int(start.astimezone(UTC).timestamp())
    end_epoch = int(end.astimezone(UTC).timestamp())
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={start_epoch}&period2={end_epoch}&interval=5m"
    )


def normalize_yahoo_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise ValueError("yahoo_chart_missing_container")
    if chart.get("error"):
        raise ValueError(f"yahoo_chart_error:{chart['error']}")
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError("yahoo_chart_missing_result")
    result = results[0]
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    quote = quotes[0] if isinstance(quotes, list) and quotes else None
    if not isinstance(timestamps, list) or not isinstance(quote, dict):
        raise ValueError("yahoo_chart_missing_quote_series")
    bars: list[dict[str, Any]] = []
    for index, epoch in enumerate(timestamps):
        try:
            timestamp = int(epoch)
            values = {field: quote[field][index] for field in ("open", "high", "low", "close", "volume")}
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        numbers = {name: finite_number(value) for name, value in values.items()}
        if any(value is None for value in numbers.values()):
            continue
        dt = datetime.fromtimestamp(timestamp, tz=UTC).astimezone(ET)
        bars.append(
            {
                "dt": dt,
                "date": dt.date().isoformat(),
                "time": dt.hour * 100 + dt.minute,
                "open": numbers["open"],
                "high": numbers["high"],
                "low": numbers["low"],
                "close": numbers["close"],
                "volume": numbers["volume"],
            }
        )
    if not bars:
        raise ValueError("yahoo_chart_empty_bars")
    return sorted(bars, key=lambda bar: bar["dt"])


def load_or_fetch_yahoo_bars(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    refresh: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and freeze one public historical five-minute chart response."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = f"{symbol.replace('^', 'index_').replace('=', '_')}_{int(start.timestamp())}_{int(end.timestamp())}_5m.json"
    cache_path = cache_dir / cache_name
    source = "cache"
    if refresh or not cache_path.exists():
        request = urllib.request.Request(yahoo_chart_url(symbol, start, end), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache_path.write_text(json.dumps(payload, separators=(",", ":")))
        source = "yahoo"
    else:
        payload = json.loads(cache_path.read_text())
    bars = normalize_yahoo_bars(payload)
    return bars, {
        "symbol": symbol,
        "source": source,
        "cachePath": str(cache_path),
        "sha256": sha256_file(cache_path),
        "bars": len(bars),
        "first": bars[0]["dt"].isoformat() if bars else None,
        "last": bars[-1]["dt"].isoformat() if bars else None,
    }


def recompute_entry(position: dict[str, Any], bars: list[dict[str, Any]], engine: Any) -> dict[str, Any]:
    """Run V7.4.23's historical completed-bar signal path at one trade time."""
    entry = position["entry"]
    symbol = str(entry.get("symbol") or "")
    expected_direction = entry.get("direction")
    now = replay_time(position)
    if expected_direction not in {"bullish", "bearish"}:
        return {"status": "unobservable", "reason": "entry_direction_unavailable", "at": now.isoformat()}
    if not bars:
        return {"status": "unobservable", "reason": "historical_bars_unavailable", "at": now.isoformat()}

    day = engine.completed_day_bars(bars, now=now, symbol=symbol)
    ready, freshness_reason = engine.validate_completed_bar_freshness(day, now=now, symbol=symbol)
    if not ready:
        return {
            "status": "unobservable",
            "reason": f"completed_bar_{freshness_reason}",
            "at": now.isoformat(),
            "sessionBars": len(day),
        }
    warmup, relative_volume, relative_volume_source, premarket_context = engine.session_signal_inputs(
        bars, day, now=now, symbol=symbol,
    )
    setup = engine.detect_setup_for_recheck(
        day,
        warmup_bars=warmup,
        relative_volume=relative_volume,
        premarket_context=premarket_context,
        symbol=symbol,
    )
    setup = engine.apply_symbol_entry_score_gate(setup, symbol=symbol)
    setup = engine.apply_structural_entry_gate(setup)
    replay_direction = setup.get("direction")
    result = {
        "at": now.isoformat(),
        "sessionBars": len(day),
        "warmupBars": len(warmup),
        "relativeVolume": relative_volume,
        "relativeVolumeSource": relative_volume_source,
        "replayedDirection": replay_direction,
        "score": setup.get("score"),
        "bullScore": setup.get("bullScore"),
        "bearScore": setup.get("bearScore"),
        "rejectionReasons": setup.get("rejectionReasons") or [],
    }
    if replay_direction is None:
        result.update(status="rejected", reason=(result["rejectionReasons"][-1] if result["rejectionReasons"] else "no_v7423_signal"))
    elif replay_direction != expected_direction:
        result.update(status="rejected", reason="replayed_direction_mismatch")
    else:
        result.update(status="accepted", reason="accepted")
    return result


def validated_recorded_pnl(position: dict[str, Any]) -> tuple[float | None, str | None]:
    """Use only a ledger P&L that reconciles to its recorded option prices."""
    entry_ask = finite_number(position["entry"].get("entryAsk"))
    exit_bid = finite_number(position["exit"].get("exitBid"))
    logged_pnl = finite_number(position["exit"].get("pnl"))
    if entry_ask is None or exit_bid is None or logged_pnl is None:
        return None, "recorded_price_or_pnl_unavailable"
    expected = round((exit_bid - entry_ask) * 100.0, 2)
    if abs(expected - logged_pnl) > 0.02:
        return None, "recorded_pnl_price_mismatch"
    return logged_pnl, None


def pnl_summary(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = round(sum(wins), 2)
    gross_loss = round(sum(losses), 2)
    profit_factor: float | None
    if gross_loss:
        profit_factor = round(gross_profit / abs(gross_loss), 4)
    elif gross_profit:
        profit_factor = None
    else:
        profit_factor = 0.0
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(values) - len(wins) - len(losses),
        "winRatePct": round(len(wins) / len(values) * 100, 2) if values else None,
        "netPnl": round(sum(values), 2),
        "grossProfit": gross_profit,
        "grossLoss": gross_loss,
        "profitFactor": profit_factor,
        "averagePnl": round(sum(values) / len(values), 2) if values else None,
    }


def load_archive_quote_coverage(
    positions: list[dict[str, Any]], db_dir: Path = DEFAULT_DB_DIR,
) -> dict[str, Any]:
    """Count archived option snapshots per position without reading broker data."""
    db_path = str(db_dir)
    if db_path not in sys.path:
        sys.path.insert(0, db_path)
    try:
        db_manager = importlib.import_module("db_manager")
        connection = db_manager.get_connection()
    except Exception as exc:  # archive availability is informative, not required
        return {"available": False, "reason": f"archive_connection_failed:{type(exc).__name__}"}

    snapshots: dict[str, list[datetime]] = defaultdict(list)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM snapshots WHERE snapshot_type = %s", ("quote",))
            rows = cursor.fetchall()
        for (payload,) in rows:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                continue
            occ_symbol = payload.get("occSymbol") or payload.get("occ_symbol") or payload.get("optionSymbol")
            observed = (
                parse_timestamp(payload.get("timestamp"))
                or parse_timestamp(payload.get("quoteTimestamp"))
                or parse_timestamp(payload.get("capturedAt"))
            )
            if isinstance(occ_symbol, str) and observed is not None:
                snapshots[occ_symbol].append(observed)
    except Exception as exc:
        return {"available": False, "reason": f"archive_query_failed:{type(exc).__name__}"}
    finally:
        connection.close()

    tiers: Counter[str] = Counter()
    per_position: dict[str, int] = {}
    for position in positions:
        entry = position["entry"]
        occ_symbol = entry.get("occSymbol")
        captured = [
            timestamp for timestamp in snapshots.get(occ_symbol, [])
            if position["entryTime"] - timedelta(seconds=30) <= timestamp <= position["exitTime"] + timedelta(seconds=30)
        ]
        count = len(captured)
        per_position[position["positionId"]] = count
        if count == 0:
            tiers["no_option_quote"] += 1
        elif count == 1:
            tiers["endpoint_only"] += 1
        elif count < 4:
            tiers["partial_path"] += 1
        else:
            tiers["path_available"] += 1
    return {
        "available": True,
        "positions": len(positions),
        "tiers": dict(sorted(tiers.items())),
        "perPosition": per_position,
        "note": "Snapshot counts measure whether an exit-path replay is possible; they do not create missing quotes.",
    }


def build_report(
    positions: list[dict[str, Any]],
    ledger_provenance: dict[str, Any],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    cache_provenance: dict[str, Any],
    engine: Any,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    baseline_values: list[float] = []
    proxy_values: list[float] = []
    rejection_reasons: Counter[str] = Counter()
    stored_statuses: Counter[str] = Counter()

    for position in positions:
        entry = position["entry"]
        exit_event = position["exit"]
        symbol = str(entry.get("symbol") or "")
        stored = evaluate_stored_entry(entry, engine)
        stored_statuses[stored["status"]] += 1
        replay = recompute_entry(position, bars_by_symbol.get(symbol, []), engine)
        pnl, pnl_error = validated_recorded_pnl(position)
        if pnl is not None:
            baseline_values.append(pnl)
        if replay["status"] == "accepted" and pnl is not None:
            proxy_values.append(pnl)
        elif replay["status"] == "rejected":
            rejection_reasons[replay["reason"]] += 1

        records.append(
            {
                "positionId": position["positionId"],
                "symbol": symbol,
                "engineVersion": entry.get("engineVersion") or "legacy-unversioned",
                "direction": entry.get("direction"),
                "entryTime": position["entryTime"].isoformat(),
                "signalBarTime": replay_time(position).isoformat(),
                "exitTime": position["exitTime"].isoformat(),
                "recordedPnl": pnl,
                "recordedPnlValidation": pnl_error or "ok",
                "storedSignalGate": stored,
                "v7423EntryReplay": replay,
                "outcomeUse": (
                    "included_in_recorded_exit_proxy" if replay["status"] == "accepted" and pnl is not None
                    else "not_included"
                ),
            }
        )

    accepted = [record for record in records if record["v7423EntryReplay"]["status"] == "accepted"]
    unobservable = [record for record in records if record["v7423EntryReplay"]["status"] == "unobservable"]
    report = {
        "reportType": "v7423_historical_entry_gate_replay",
        "generatedAt": datetime.now(ET).isoformat(),
        "release": {
            "engineVersion": getattr(engine, "ENGINE_VERSION", None),
            "strategyId": getattr(engine, "STRATEGY_ID", None),
            "enginePath": str(Path(engine.__file__).resolve()),
            "engineSha256": sha256_file(Path(engine.__file__).resolve()),
        },
        "scope": {
            "mode": "read_only",
            "input": "all closed raw V7 paper-trade pairs",
            "entryDecision": "V7.4.23 code recomputed at archived completed signal-bar timestamps",
            "pnl": "recorded entry/exit option prices for accepted historical trades only",
            "excluded": [
                "unrecorded scan opportunities",
                "alternative contract selection",
                "alternative exit prices where intermediate option quotes are absent",
                "historical macro/news calendar reconstruction",
                "historical live-underlying quote confirmation where not archived",
            ],
        },
        "provenance": {
            "rawLedgers": ledger_provenance,
            "historicalBars": cache_provenance,
        },
        "coverage": {
            "closedTradePairs": len(positions),
            "entryReplayAccepted": len(accepted),
            "entryReplayRejected": sum(record["v7423EntryReplay"]["status"] == "rejected" for record in records),
            "entryReplayUnobservable": len(unobservable),
            "storedSignalGateStatuses": dict(sorted(stored_statuses.items())),
            "rejectionReasons": dict(rejection_reasons.most_common()),
        },
        "realizedHistoricalPnl": pnl_summary(baseline_values),
        "recordedExitProxyForV7423AcceptedEntries": pnl_summary(proxy_values),
        "interpretation": {
            "isFullCounterfactualBacktest": False,
            "isForwardPrediction": False,
            "why": "The archive lacks a complete historical option-quote path for most positions, so exit-time/exit-price decisions cannot be replayed without fabricating data.",
            "validUse": "A selection-conditioned, in-sample entry-gate outcome proxy. It is not promotion evidence and not a forecast of future P&L.",
        },
        "positions": records,
    }
    report["archiveOptionQuoteCoverage"] = load_archive_quote_coverage(positions)
    return report


def markdown_report(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    baseline = report["realizedHistoricalPnl"]
    proxy = report["recordedExitProxyForV7423AcceptedEntries"]
    quote_coverage = report.get("archiveOptionQuoteCoverage", {})
    lines = [
        "# V7.4.23 historical trade replay",
        "",
        "## Result",
        "",
        f"- Closed raw paper-trade pairs: **{coverage['closedTradePairs']}**",
        f"- V7.4.23 entry replay: **{coverage['entryReplayAccepted']} accepted**, "
        f"**{coverage['entryReplayRejected']} rejected**, **{coverage['entryReplayUnobservable']} unobservable**",
        f"- All-recorded realized historical P&L: **${baseline['netPnl']:.2f}** across {baseline['trades']} trades",
        f"- Accepted-entry recorded-exit proxy: **${proxy['netPnl']:.2f}** across {proxy['trades']} trades "
        f"({proxy['winRatePct']}% wins; PF {proxy['profitFactor']})",
        "",
        "## Interpretation",
        "",
        "The accepted-entry number re-runs the current V7.4.23 completed-bar signal and structural entry gates at each archived trade's signal timestamp. It then uses that trade's recorded historical option exit price. It is a selection-conditioned in-sample proxy, not a full counterfactual or a future P&L prediction.",
        "",
        "A full V7.4.23 dollar replay requires every option quote while each trade was open. The archive coverage below is reported instead of inventing exit prices.",
        "",
        "## Option-quote observability",
        "",
    ]
    if quote_coverage.get("available"):
        for tier, count in quote_coverage.get("tiers", {}).items():
            lines.append(f"- {tier}: **{count}** positions")
    else:
        lines.append(f"- Unavailable: {quote_coverage.get('reason', 'unknown')}")
    lines.extend([
        "",
        "## Guardrails",
        "",
        "- No trade ledger, release manifest, runtime position, authorization, or broker state was modified.",
        "- No synthetic options model, BSM approximation, or fabricated quote was used.",
        "- This output is not exact-release paper evidence and must not be used to promote or enable live trading.",
        "",
        "## Input integrity",
        "",
        f"- Engine: `{report['release']['engineVersion']}` / `{report['release']['strategyId']}`",
        f"- Engine SHA-256: `{report['release']['engineSha256']}`",
        f"- Raw-ledger closed pairs: {report['provenance']['rawLedgers']['closedPairs']}",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--scripts-dir", type=Path, default=DEFAULT_SCRIPTS_DIR)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "analysis" / ".cache" / "v7423_yahoo_5m")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis" / "reports")
    parser.add_argument("--refresh", action="store_true", help="refresh the frozen public Yahoo responses")
    args = parser.parse_args()

    positions, ledger_provenance = load_closed_positions(args.state_dir)
    if not positions:
        raise SystemExit("No closed V7 paper-trade pairs found.")
    engine = load_engine(args.scripts_dir)
    start = min(replay_time(position) for position in positions) - timedelta(days=1)
    end = max(position["exitTime"] for position in positions) + timedelta(days=1)
    if end - start >= timedelta(days=60):
        end = start + timedelta(days=57)

    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    cache_provenance: dict[str, Any] = {}
    xsp_signal = "ES=F"
    for symbol in sorted({str(position["entry"].get("symbol") or "") for position in positions}):
        if not symbol:
            continue
        fetch_symbol = xsp_signal if symbol == "XSP" else symbol
        try:
            bars, provenance = load_or_fetch_yahoo_bars(
                fetch_symbol, start=start, end=end, cache_dir=args.cache_dir, refresh=args.refresh,
            )
        except (ValueError, urllib.error.HTTPError, urllib.error.URLError):
            bars, provenance = [], {"symbol": fetch_symbol, "source": "unavailable", "error": "fetch_failed"}
        except Exception as exc:
            bars, provenance = [], {"symbol": fetch_symbol, "source": "unavailable", "error": f"{type(exc).__name__}:{exc}"}
        provenance["symbol"] = symbol
        bars_by_symbol[symbol] = bars
        cache_provenance[symbol] = {k: v for k, v in provenance.items() if k != "bars"}

    report = build_report(positions, ledger_provenance, bars_by_symbol, cache_provenance, engine)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ET).strftime("%Y%m%dT%H%M%S%z")
    json_path = args.output_dir / f"v7423_historical_trade_replay_{stamp}.json"
    markdown_path = args.output_dir / f"v7423_historical_trade_replay_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    markdown_path.write_text(markdown_report(report))

    proxy = report["recordedExitProxyForV7423AcceptedEntries"]
    print(json.dumps({
        "jsonReport": str(json_path),
        "markdownReport": str(markdown_path),
        "closedPairs": report["coverage"]["closedTradePairs"],
        "accepted": report["coverage"]["entryReplayAccepted"],
        "rejected": report["coverage"]["entryReplayRejected"],
        "unobservable": report["coverage"]["entryReplayUnobservable"],
        "recordedExitProxyNetPnl": proxy["netPnl"],
        "proxyTrades": proxy["trades"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
