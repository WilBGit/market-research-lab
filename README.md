# Market Research Lab

A reproducible, research-only workspace for NQ/MNQ liquidity-sweep studies, PD-array and CISD experiments, TradingView Pine prototypes, deterministic backtests, and V7.4.23 historical replay analysis.

## Research areas

### NQ/MNQ paper monitor

The independent monitor in `nq_monitor/` evaluates closed one-minute bars using:

1. SMT non-confirmation between NQ and MNQ.
2. A prior-high or prior-low liquidity sweep with rejection.
3. CISD displacement after the sweep.
4. A touched FVG PD array in the correct premium/discount half.

The hypothetical stop is placed beyond the sweep extreme. Signals are journal events only. The monitor has no broker adapter and cannot submit orders. `NMQ` remains unresolved and is never silently mapped to MNQ or NQ.

The public Yahoo Finance feed is a delayed proxy and is not exchange-authoritative. Any paper result must be labeled as research and not as a live trading signal.

### TradingView and deterministic backtests

The `tradingview/` directory contains Pine research implementations, a deterministic Python backtest engine, regression tests, and saved research outputs. The methodology uses explicit chronological splits, conservative same-bar stop-first handling, documented slippage assumptions, and no invented fills.

The aggressive 200-plus-trade study produced:

- MNQ: 292 trades, 52.4% wins, `-30.56R`, profit factor `0.78`.
- NQ: 299 trades, 52.5% wins, `-30.17R`, profit factor `0.79`.

Higher frequency did not produce a profitable configuration. These figures are preserved for auditability and must not be presented as evidence of a live edge.

### V7.4.23 historical replay

`v7423_historical_trade_replay.py` audits stored signal structure and replay coverage without writing to V7 state, ledgers, release controls, or broker endpoints. It separates entry-gate replay from a full counterfactual exit backtest and reports missing quote coverage rather than inventing exit prices.

## Reproducibility and limits

- Keep raw market data, local journals, databases, logs, and downloaded caches outside Git.
- Record symbol mapping, timeframe, timezone, data source, date range, split rule, slippage, commissions, and same-bar policy for every run.
- Separate training, validation, and out-of-sample results.
- Do not select a configuration after seeing the test result and call it independent validation.
- Yahoo Finance and public datasets are proxy sources, not exchange-authoritative feeds.
- This repository does not authorize live orders and contains no broker credentials or transactional path.

## Example checks

```text
python -m unittest discover -s tests -v
python -m unittest discover -s tradingview -p 'test_*.py' -v
python v7423_historical_trade_replay.py --help
```

Some optional historical replay commands require local data or a separately configured research environment. Missing data must be reported, not replaced with fabricated results.
