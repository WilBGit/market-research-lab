from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_engine import SimConfig, run_strategy, summarize, config_dict  # noqa: E402
from run_100_more_backtests import download, load_csv, URLS, DATA, pt  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "backtest_aggressive_200_results.json"


def main() -> None:
    bars = {}
    hashes = {}
    for name in ("MNQ", "NQ", "ES"):
        path, digest = download(name, URLS[name])
        hashes[name] = digest
        bars[name] = load_csv(path)

    start = max(min(bar.ts for bar in bars[name]) for name in ("MNQ", "NQ", "ES"))
    end = min(max(bar.ts for bar in bars[name]) for name in ("MNQ", "NQ", "ES"))
    split = start + int((end - start) * 0.65)

    # Aggressive variant still requires SMT, liquidity sweep, CISD, and PD
    # equilibrium. It relaxes FVG, lowers the displacement threshold, expands
    # the entry window to full RTH, and uses a closer target.
    cfg = SimConfig(
        require_smt=True,
        require_fvg=False,
        require_pd=True,
        body_atr=0.0,
        fvg_atr=0.0,
        wait_bars=60,
        stop_ticks=1,
        target_r=0.75,
        be_r=0.0,
        max_hold=30,
        max_entries_day=10,
        session_open=8 * 60 + 30,
        session_close=16 * 60,
    )

    output = {
        "variant": "aggressive_200_trade_research",
        "data_source": "axb0306/cme-futures-ohlc GitHub CSV, TopstepX API source",
        "source_urls": URLS,
        "sha256": hashes,
        "timeframe": "1m",
        "htf": "15m",
        "window_start_pt": pt(start),
        "window_end_pt": pt(end),
        "split_pt": pt(split),
        "costs": {
            "slippage_ticks_per_side": 1,
            "commission": "not included; results reported in R",
            "same_bar_conflict": "stop first",
        },
        "gates": {
            "smt": True,
            "liquidity_sweep": True,
            "cisd": True,
            "pd_equilibrium": True,
            "fvg_required": False,
            "session": "08:30-16:00 America/New_York",
        },
        "config": config_dict(cfg),
        "symbols": {},
    }
    for symbol in ("MNQ", "NQ"):
        trades = run_strategy(bars[symbol], bars["ES"], cfg)
        output["symbols"][symbol] = {
            "trade_count": len(trades),
            "summary": summarize(trades, split_ts=split),
            "trades": [asdict(trade) for trade in trades],
        }
        print(symbol, len(trades), output["symbols"][symbol]["summary"], flush=True)

    OUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
