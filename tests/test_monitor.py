import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nq_monitor.journal import SignalJournal
from nq_monitor.models import CISDEvent, PDArray, SMTEvent, SweepEvent
from nq_monitor.monitor import (
    MarketContext,
    MonitorConfig,
    ScanResult,
    format_signal_alert,
    scan_once,
)
from nq_monitor.strategy import StrategyConfig, assemble_paper_signal


class FakeFeed:
    def __init__(self):
        self.calls = []

    def fetch(self, source_symbol, display_symbol, now=None):
        self.calls.append((source_symbol, display_symbol))
        return []


class MonitorTests(unittest.TestCase):
    def test_weekend_scan_is_fail_closed_and_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            feed = FakeFeed()
            journal = SignalJournal(Path(d) / "journal.sqlite3")
            result = scan_once(
                now=datetime(2026, 11, 7, 10, 30, tzinfo=timezone.utc),
                feed=feed,
                journal=journal,
                config=MonitorConfig(),
            )
            self.assertIsInstance(result, ScanResult)
            self.assertEqual(result.status, "WEEKEND")
            self.assertEqual(feed.calls, [])

    def test_signal_alert_is_explicitly_paper_only_and_contains_stop(self):
        ts = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)
        signal = assemble_paper_signal(
            symbol="NQ",
            entry_price=102.0,
            sweep=SweepEvent("long", "sell_side", 20, ts, 100, 99),
            smt=SMTEvent("long", 20, ts, "NQ", "MNQ", 99, 100.5),
            cisd=CISDEvent("long", 22, ts, 102, 101),
            pd_array=PDArray("long", "bullish_fvg", 19, 99.5, 100.5, 105),
            config=StrategyConfig(tick_size=0.25),
        )
        message = format_signal_alert(signal, data_status="DELAYED_PROXY")
        self.assertIn("PAPER ONLY", message)
        self.assertIn("NQ LONG", message)
        self.assertIn("stop-loss 98.75", message)
        self.assertIn("DELAYED_PROXY", message)
        self.assertNotIn("submit", message.lower())

    def test_signal_alert_includes_both_contract_levels_liquidity_and_trend(self):
        ts = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)
        signal = assemble_paper_signal(
            symbol="NQ",
            entry_price=23456.25,
            sweep=SweepEvent("long", "sell_side", 20, ts, 23400.0, 23395.0),
            smt=SMTEvent("long", 20, ts, "NQ", "MNQ", 23395.0, 23402.0),
            cisd=CISDEvent("long", 22, ts, 23456.25, 23400.0),
            pd_array=PDArray("long", "bullish_fvg", 19, 23405.0, 23410.0, 23450.0),
            config=StrategyConfig(tick_size=0.25),
        )
        context = MarketContext(
            timestamp=ts,
            nq_level=23456.25,
            mnq_level=23452.75,
            nq_trend="BULLISH",
            mnq_trend="BULLISH",
            nq_liquidity_low=23400.0,
            nq_liquidity_high=23500.0,
            mnq_liquidity_low=23398.0,
            mnq_liquidity_high=23496.0,
            pair_trend="ALIGNED_BULLISH",
        )

        message = format_signal_alert(signal, data_status="DELAYED_PROXY", context=context)

        self.assertIn("NQ level 23456.25", message)
        self.assertIn("MNQ level 23452.75", message)
        self.assertIn("NQ liquidity low 23400.00 / high 23500.00", message)
        self.assertIn("MNQ liquidity low 23398.00 / high 23496.00", message)
        self.assertIn("trend BULLISH", message)
        self.assertIn("ALIGNED_BULLISH", message)
        self.assertIn("swept sell_side liquidity 23400.00", message)
        self.assertIn("PD bullish_fvg 23405.00-23410.00", message)


if __name__ == "__main__":
    unittest.main()
