# V7.4.23 historical trade replay

## Result

- Closed raw paper-trade pairs: **73**
- V7.4.23 entry replay: **36 accepted**, **36 rejected**, **1 unobservable**
- All-recorded realized historical P&L: **$120.00** across 73 trades
- Accepted-entry recorded-exit proxy: **$371.00** across 36 trades (58.33% wins; PF 4.9053)

## Interpretation

The accepted-entry number re-runs the current V7.4.23 completed-bar signal and structural entry gates at each archived trade's signal timestamp. It then uses that trade's recorded historical option exit price. It is a selection-conditioned in-sample proxy, not a full counterfactual or a future P&L prediction.

A full V7.4.23 dollar replay requires every option quote while each trade was open. The archive coverage below is reported instead of inventing exit prices.

## Option-quote observability

- endpoint_only: **52** positions
- no_option_quote: **9** positions
- partial_path: **4** positions
- path_available: **8** positions

## Guardrails

- No trade ledger, release manifest, runtime position, authorization, or broker state was modified.
- No synthetic options model, BSM approximation, or fabricated quote was used.
- This output is not exact-release paper evidence and must not be used to promote or enable live trading.

## Input integrity

- Engine: `7.4.23-evidence-gate-early-reversal` / `v7423-evidence-gate-early-reversal`
- Engine SHA-256: `feb06506239503951682acab40d2d9571aee79ad4c63599d0cfc060e39e0ed0c`
- Raw-ledger closed pairs: 73
