#!/usr/bin/env python3
"""One read-only, paper-only NQ/MNQ scan for the Hermes scheduler."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nq_monitor.journal import SignalJournal
from nq_monitor.monitor import MonitorConfig, scan_once

DISCORD_CHANNEL_ID = "1519232614056333315"  # Alpha Trading Floor #general
DISCORD_HELPER = Path("$HOME/.hermes/scripts/discord-send-message.mjs")


def split_discord(content: str, max_len: int = 1900) -> list[str]:
    """Split content before Discord's 2,000-character API limit."""
    chunks: list[str] = []
    current = ""
    for part in content.split("\n\n"):
        candidate = part if not current else current + "\n\n" + part
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(part) <= max_len:
            current = part
        else:
            for start in range(0, len(part), max_len):
                chunks.append(part[start:start + max_len])
            current = ""
    if current:
        chunks.append(current)
    return chunks or [content[:max_len]]


def post_discord_alerts(messages: tuple[str, ...], *, dry_run: bool = False) -> list[str]:
    """Post alert messages directly to Discord, or validate in dry-run mode."""
    chunks = [chunk for message in messages for chunk in split_discord(message)]
    if dry_run:
        return [f"dry-run {len(chunks)} chunk(s)"]
    if not DISCORD_HELPER.exists():
        raise RuntimeError(f"missing Discord helper: {DISCORD_HELPER}")
    urls: list[str] = []
    for chunk in chunks:
        proc = subprocess.run(
            ["/usr/local/bin/node", str(DISCORD_HELPER), DISCORD_CHANNEL_ID, chunk],
            cwd=str(DISCORD_HELPER.parent),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "unknown Discord error").strip()
            raise RuntimeError(detail[-500:])
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("url"):
            urls.append(payload["url"])
    return urls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--journal", type=Path, default=ROOT / "data" / "journal.sqlite3")
    parser.add_argument("--discord", action="store_true", help="post emitted alerts to Discord #general")
    parser.add_argument("--discord-dry-run", action="store_true", help="validate Discord delivery without posting")
    args = parser.parse_args()
    journal = SignalJournal(args.journal)
    try:
        if args.report:
            print(json.dumps(journal.summary(), sort_keys=True))
            return 0
        result = scan_once(
            now=datetime.now(timezone.utc),
            journal=journal,
            config=MonitorConfig(journal_path=args.journal),
        )
        if args.discord or args.discord_dry_run:
            pending_alerts = journal.pending_alerts()
            dry_run = args.discord_dry_run or os.environ.get("NQ_PD_MONITOR_DISCORD_DRY_RUN") == "1"
            for alert in pending_alerts:
                try:
                    urls = post_discord_alerts((alert["message"],), dry_run=dry_run)
                    if not dry_run:
                        journal.mark_alert_delivered(alert["alert_id"])
                    if urls:
                        print(f"Discord #general delivery: {', '.join(urls)}")
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    print(f"Discord #general delivery failed: {exc}", file=sys.stderr)
                    return 1
        if args.verbose:
            print(json.dumps({"status": result.status, "data_status": result.data_status}, sort_keys=True))
        for message in result.messages:
            print(message)
        return 0
    finally:
        journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
