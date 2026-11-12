# NQ/MNQ liquidity sweep + PD-array paper monitor

This is an independent, read-only test monitor. It does not import V7, Robinhood, or any transactional broker tool, and it cannot submit orders.

Active inputs:

- `NQ=F` through Yahoo Finance, displayed as `NQ`.
- `MNQ=F` through Yahoo Finance, displayed as `MNQ`.
- The requested label `NMQ` is not a verified standard futures symbol. It is kept unresolved rather than silently rewritten. `MNQ` is the provisional counterpart used for the pair comparison; `NNQ` is another distinct CME product and is not substituted. Both possibilities are called out rather than silently mapped.

The Yahoo feed is a delayed proxy. Alerts are paper-only and must not be treated as executable live entries.

## Order gate

A hypothetical order is created only when all four conditions are true on closed 1-minute bars:

1. SMT non-confirmation between NQ and MNQ.
2. A prior-high/prior-low liquidity sweep with rejection.
3. CISD displacement close after the sweep.
4. A touched FVG PD array in the correct premium/discount half.

For a long, the stop is one tick below the sweep extreme. For a short, it is one tick above the sweep extreme. The target is fixed at 2R. If an OHLC bar hits both stop and target, the outcome is conservatively recorded as a loss because intrabar ordering is unknowable.

## Commands

From this directory:

```text
/Volumes/REDACTED/alpha-trading-floor/venv/bin/python scripts/scan_once.py --verbose
/Volumes/REDACTED/alpha-trading-floor/venv/bin/python scripts/scan_once.py --report
/Volumes/REDACTED/alpha-trading-floor/venv/bin/python -m unittest discover -s tests -v
```

The journal is `data/journal.sqlite3`. Signals and outcomes are deduplicated by stable IDs. New paper signal/result alerts are posted to the Alpha Trading Floor Discord `#general` channel by the Hermes wrapper, with a retryable delivery outbox. Each emitted alert includes the latest NQ and MNQ levels, each contract's rolling liquidity low/high, pair trend state, and any swept liquidity or PD-array levels. Win rate is wins divided by resolved wins plus losses. Pending and timeouts are reported separately.

## Safety boundary

The monitor does not have a broker adapter, order endpoint, credentials, or live mode. `PAPER_ENTRY` is a journal event only. `stop_loss_price` is a hypothetical cut-loss level under/over the liquidity sweep, not a real stop order.
