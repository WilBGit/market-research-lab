# Security Policy

## Research-only boundary

This repository contains market-research code, Pine studies, tests, and sanitized reports. It is not a broker integration, live alert authorization, or trading account record.

## Never commit

- Broker credentials, API keys, account numbers, order tokens, webhooks, passwords, OAuth files, cookies, or private keys
- Trade ledgers, journals, SQLite databases, runtime state, scheduler state, logs, local authorization state, or raw personal data
- Unredacted provider responses containing account or customer information
- Large downloaded datasets unless they are explicitly reviewed and cleared for redistribution

## Data handling

Use environment variables or a local secret manager for any optional provider access. Keep raw data, caches, and journals outside the repository. Treat Yahoo Finance and other public feeds as proxies and document their limitations in every result.

## Pre-push checklist

- Inspect staged paths and contents for secrets and runtime artifacts.
- Run `git diff --cached --check`.
- Run high-confidence secret scans against the staged tree and reachable history.
- Run the deterministic tests that do not require private data.
- Verify that research outputs distinguish training, validation, and out-of-sample periods.
- Confirm repository visibility and branch before pushing.

## Incident response

If a credential or private record is found, stop distribution, revoke or rotate the credential, remove the material from current files and reachable history, and re-scan the remote repository. Never paste the exposed value into an issue or chat.
