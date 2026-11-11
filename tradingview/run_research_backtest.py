from __future__ import annotations

from datetime import datetime, timezone
from itertools import product
from pathlib import Path
import json
import sys

from backtest_engine import SimConfig, config_dict, fetch_yahoo_1m, run_strategy, summarize

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "yahoo_cache"
OUT = ROOT / "backtest_results.json"


def pt(ts: int) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).isoformat()


def main() -> None:
    print("Fetching 1-minute Yahoo proxy bars (MNQ=F, NQ=F, ES=F)...", flush=True)
    es = fetch_yahoo_1m("ES=F", days=7, cache_dir=CACHE)
    datasets = {symbol: fetch_yahoo_1m(symbol, days=7, cache_dir=CACHE) for symbol in ("MNQ=F", "NQ=F")}
    print("bars", {k: len(v) for k, v in {**datasets, "ES=F": es}.items()}, flush=True)
    common_start = max(min(x.ts for x in bars) for bars in datasets.values())
    common_end = min(max(x.ts for x in bars) for bars in datasets.values())
    split = common_start + int((common_end - common_start) * 0.65)
    print("window", pt(common_start), "to", pt(common_end), "split", pt(split), flush=True)

    grid = list(product(
        (0.75, 1.00, 1.25, 1.50, 2.00),
        (0.25, 0.50, 0.75),
        (0.00, 0.10, 0.20),
        (10, 20, 30),
    ))
    output: dict = {
        "data_source": "Yahoo Finance chart API delayed proxy",
        "symbols": ["MNQ=F", "NQ=F"],
        "comparison_symbol": "ES=F",
        "timeframe": "1m",
        "htf": "15m",
        "window_start_pt": pt(common_start),
        "window_end_pt": pt(common_end),
        "split_pt": pt(split),
        "grid_size": len(grid),
        "configs": {},
    }
    for symbol, target in datasets.items():
        rows = []
        for target_r, body_atr, fvg_atr, wait_bars in grid:
            cfg = SimConfig(target_r=target_r, body_atr=body_atr, fvg_atr=fvg_atr, wait_bars=wait_bars)
            results = run_strategy(target, es, cfg)
            summary = summarize(results, split_ts=split)
            rows.append({"config": config_dict(cfg), "summary": summary})
        rows.sort(key=lambda row: (
            -1 if row["summary"]["out_of_sample"]["win_rate_pct"] is None else -row["summary"]["out_of_sample"]["win_rate_pct"],
            -row["summary"]["out_of_sample"]["net_r"],
            -row["summary"]["all"]["trades"],
        ))
        eligible = [
            row for row in rows
            if row["summary"]["out_of_sample"]["trades"] >= 5
            and row["summary"]["all"]["trades"] >= 10
        ]
        train_wr = [r for r in rows if r["summary"]["in_sample"]["trades"] >= 2]
        train_net = [r for r in rows if r["summary"]["in_sample"]["trades"] >= 3]
        output["configs"][symbol] = {
            "eligible_count": len(eligible),
            "best_selected_on_train_wr_min_2": sorted(
                train_wr,
                key=lambda row: (
                    -1 if row["summary"]["in_sample"]["win_rate_pct"] is None else -row["summary"]["in_sample"]["win_rate_pct"],
                    -row["summary"]["in_sample"]["net_r"],
                ),
            )[0] if train_wr else None,
            "best_selected_on_train_net_min_3": sorted(
                train_net,
                key=lambda row: (
                    -row["summary"]["in_sample"]["net_r"],
                    -1 if row["summary"]["in_sample"]["win_rate_pct"] is None else -row["summary"]["in_sample"]["win_rate_pct"],
                ),
            )[0] if train_net else None,
            "top_out_of_sample_wr": rows[:10],
            "top_all_sample_wr_min_10": sorted(
                [r for r in rows if r["summary"]["all"]["trades"] >= 10],
                key=lambda row: (
                    -1 if row["summary"]["all"]["win_rate_pct"] is None else -row["summary"]["all"]["win_rate_pct"],
                    -row["summary"]["all"]["net_r"],
                ),
            )[:10],
            "top_all_sample_wr_min_1": sorted(
                rows,
                key=lambda row: (
                    -1 if row["summary"]["all"]["win_rate_pct"] is None else -row["summary"]["all"]["win_rate_pct"],
                    -row["summary"]["all"]["trades"],
                    -row["summary"]["all"]["net_r"],
                ),
            )[:10],
        }
        best = output["configs"][symbol]["best_selected_on_train_net_min_3"]
        print(symbol, "eligible", len(eligible), "train-selected", best["summary"] if best else "none", flush=True)
    OUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
