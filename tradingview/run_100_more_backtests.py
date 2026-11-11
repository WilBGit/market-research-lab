from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import itertools
import json
import random
import requests
from zoneinfo import ZoneInfo

from backtest_engine import Bar, SimConfig, run_strategy, summarize, config_dict

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "github_1m"
OUT = ROOT / "backtest_100_more_results.json"
BASE = "https://raw.githubusercontent.com/axb0306/cme-futures-ohlc/main"
URLS = {
    "MNQ": f"{BASE}/MNQ/MNQ_1min_20260120_20260415.csv",
    "NQ": f"{BASE}/NQ/NQ_1min_20260120_20260415.csv",
    "ES": f"{BASE}/ES/ES_1min_20260120_20260415.csv",
}


def download(name: str, url: str) -> tuple[Path, str]:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{name}_1min_20260120_20260415.csv"
    if not path.exists():
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        path.write_bytes(response.content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def load_csv(path: Path) -> list[Bar]:
    rows: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dt = datetime.fromisoformat(row["datetime"]).replace(tzinfo=timezone.utc)
            rows.append(Bar(
                int(dt.timestamp()),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row.get("volume") or 0),
            ))
    return rows


def pt(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).isoformat()


def pct(value):
    return None if value is None else round(value, 4)


def main() -> None:
    paths = {}
    hashes = {}
    bars = {}
    for name, url in URLS.items():
        paths[name], hashes[name] = download(name, url)
        bars[name] = load_csv(paths[name])
        print(name, len(bars[name]), pt(bars[name][0].ts), pt(bars[name][-1].ts), flush=True)

    start = max(min(bar.ts for bar in bars[name]) for name in ("MNQ", "NQ", "ES"))
    end = min(max(bar.ts for bar in bars[name]) for name in ("MNQ", "NQ", "ES"))
    split = start + int((end - start) * 0.65)

    # 480 possible combinations, sampled deterministically to add exactly 100
    # configurations beyond the earlier 135-configuration Yahoo run.
    full_grid = list(itertools.product(
        (0.75, 1.00, 1.25, 1.50, 2.00),
        (0.25, 0.50, 0.75),
        (0.00, 0.05, 0.10, 0.20),
        (10, 20, 30, 45),
        (1, 2, 4),
        (0.00, 1.00),
        (30, 60, 90),
    ))
    configs = random.Random(20261108).sample(full_grid, 100)
    output = {
        "run": "100 additional deterministic parameter backtests",
        "selection_seed": 20261108,
        "data_source": "axb0306/cme-futures-ohlc GitHub CSV, TopstepX API source",
        "source_urls": URLS,
        "sha256": hashes,
        "timeframe": "1m",
        "htf": "15m",
        "window_start_pt": pt(start),
        "window_end_pt": pt(end),
        "split_pt": pt(split),
        "costs": {"slippage_ticks_per_side": 1, "commission": "not included; results reported in R"},
        "gates": {"require_smt": True, "require_fvg": True, "require_pd": True, "session": "08:30-11:30 America/New_York"},
        "symbols": {},
    }

    for symbol in ("MNQ", "NQ"):
        rows = []
        for target_r, body_atr, fvg_atr, wait_bars, stop_ticks, be_r, max_hold in configs:
            cfg = SimConfig(
                target_r=target_r,
                body_atr=body_atr,
                fvg_atr=fvg_atr,
                wait_bars=wait_bars,
                stop_ticks=stop_ticks,
                be_r=be_r,
                max_hold=max_hold,
            )
            trades = run_strategy(bars[symbol], bars["ES"], cfg)
            rows.append({"config": config_dict(cfg), "summary": summarize(trades, split_ts=split)})

        def train_wr_key(row):
            stat = row["summary"]["in_sample"]
            return (-1 if stat["win_rate_pct"] is None else -stat["win_rate_pct"], -stat["net_r"], -stat["trades"])

        def train_net_key(row):
            stat = row["summary"]["in_sample"]
            return (-stat["net_r"], -1 if stat["win_rate_pct"] is None else -stat["win_rate_pct"], -stat["trades"])

        train_2 = [row for row in rows if row["summary"]["in_sample"]["trades"] >= 2]
        train_5 = [row for row in rows if row["summary"]["in_sample"]["trades"] >= 5]
        all_5 = [row for row in rows if row["summary"]["all"]["trades"] >= 5]
        oos_5 = [row for row in rows if row["summary"]["out_of_sample"]["trades"] >= 5]
        output["symbols"][symbol] = {
            "config_count": len(rows),
            "train_selected_wr_min2": min(train_2, key=train_wr_key) if train_2 else None,
            "train_selected_net_min5": min(train_5, key=train_net_key) if train_5 else None,
            "highest_all_wr_min5": sorted(all_5, key=lambda row: (-row["summary"]["all"]["win_rate_pct"], -row["summary"]["all"]["net_r"]))[:10],
            "highest_oos_wr_min5": sorted(oos_5, key=lambda row: (-row["summary"]["out_of_sample"]["win_rate_pct"], -row["summary"]["out_of_sample"]["net_r"]))[:10],
            "all_config_summaries": rows,
        }
        train_best = output["symbols"][symbol]["train_selected_net_min5"]
        print(symbol, "configs", len(rows), "train-net", train_best["summary"] if train_best else "none", flush=True)

    OUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
