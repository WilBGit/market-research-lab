from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from v7423_historical_trade_replay import (
    evaluate_stored_entry,
    load_closed_positions,
    normalize_yahoo_bars,
)


def _entry(*, turtle_breakout: bool, score: int = 7) -> dict:
    checks = {
        "ema_trend": True,
        "vwap": True,
        "supertrend": True,
        "macd": True,
        "rsi": True,
        "momentum": True,
        "relative_volume": True,
        "turtle_breakout": turtle_breakout,
    }
    return {
        "symbol": "SPY",
        "direction": "bullish",
        "signal": {
            "direction": "bullish",
            "score": score,
            "bullScore": score,
            "bearScore": 0,
            "relativeVolume": 1.2,
            "signalBarEnd": "2026-09-09T11:05:00-04:00",
            "bullChecks": checks,
            "bearChecks": {key: False for key in checks},
            "turtle": {"longBreakout": turtle_breakout, "shortBreakout": False},
        },
    }


def test_current_structural_signal_is_admissible() -> None:
    result = evaluate_stored_entry(_entry(turtle_breakout=True))

    assert result["status"] == "accepted"
    assert result["reason"] == "accepted"


def test_nonstructural_signal_is_rejected() -> None:
    result = evaluate_stored_entry(_entry(turtle_breakout=False))

    assert result["status"] == "rejected"
    assert result["reason"] == "signal_nonstructural_turtle"


def test_pairs_real_bid_exit_events(tmp_path: Path) -> None:
    ledger = tmp_path / "v7_scalp_trades_SPY.jsonl"
    events = [
        {
            "event": "PAPER_ENTRY_REAL_ASK",
            "positionId": "paper-1",
            "entryTime": "2026-09-09T11:05:30-04:00",
        },
        {
            "event": "PAPER_EXIT_REAL_BID",
            "positionId": "paper-1",
            "timestamp": "2026-09-09T11:10:30-04:00",
        },
    ]
    ledger.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    positions, provenance = load_closed_positions(tmp_path)

    assert provenance["closedPairs"] == 1
    assert positions[0]["positionId"] == "paper-1"


def test_yahoo_error_payload_is_not_treated_as_empty_history() -> None:
    with pytest.raises(ValueError, match="yahoo_chart_error"):
        normalize_yahoo_bars({"chart": {"error": {"description": "No data found"}}})


def test_yahoo_empty_result_is_not_treated_as_history() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [],
                    "indicators": {"quote": [{}]},
                }
            ]
        }
    }

    with pytest.raises(ValueError, match="yahoo_chart_empty_bars"):
        normalize_yahoo_bars(payload)


def test_yahoo_missing_quote_series_is_not_treated_as_history() -> None:
    with pytest.raises(ValueError, match="yahoo_chart_missing_quote_series"):
        normalize_yahoo_bars({"chart": {"result": [{"timestamp": []}]}})
