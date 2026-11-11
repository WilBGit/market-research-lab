import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nq_monitor.journal import SignalJournal
from nq_monitor.models import Candle
from nq_monitor.outcomes import resolve_signal_on_bar
from nq_monitor.strategy import StrategyConfig


class JournalTests(unittest.TestCase):
    def _signal_row(self, signal_id="abc"):
        return {
            "signal_id": signal_id,
            "symbol": "NQ",
            "direction": "long",
            "signal_timestamp": "2026-11-06T17:00:00+00:00",
            "sweep_timestamp": "2026-11-06T16:58:00+00:00",
            "cisd_timestamp": "2026-11-06T17:00:00+00:00",
            "entry_price": 102.0,
            "stop_loss_price": 98.75,
            "target_price": 108.5,
            "risk_points": 3.25,
            "liquidity_level": 100.0,
            "pd_kind": "bullish_fvg",
            "pd_low": 99.5,
            "pd_high": 100.5,
            "requirements_json": '{"smt":true,"liquidity_sweep":true,"cisd":true,"pd_array":true}',
            "evidence_json": '{}',
            "order_mode": "paper_only",
        }

    def test_signal_insert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            journal = SignalJournal(Path(d) / "journal.sqlite3")
            self.assertTrue(journal.record_signal(self._signal_row()))
            self.assertFalse(journal.record_signal(self._signal_row()))
            self.assertEqual(journal.count_signals(), 1)

    def test_empty_summary_has_undefined_win_rate(self):
        with tempfile.TemporaryDirectory() as d:
            journal = SignalJournal(Path(d) / "journal.sqlite3")
            self.assertIsNone(journal.summary()["win_rate"])

    def test_outcome_is_immutable_and_summary_is_separate(self):
        with tempfile.TemporaryDirectory() as d:
            journal = SignalJournal(Path(d) / "journal.sqlite3")
            journal.record_signal(self._signal_row())
            outcome = {
                "signal_id": "abc",
                "outcome": "WIN",
                "exit_timestamp": "2026-11-06T17:03:00+00:00",
                "exit_price": 108.5,
                "r_multiple": 2.0,
                "reason": "target",
            }
            self.assertTrue(journal.record_outcome(outcome))
            self.assertFalse(journal.record_outcome(outcome))
            self.assertEqual(journal.summary(), {"wins": 1, "losses": 0, "timeouts": 0, "pending": 0, "win_rate": 1.0})

    def test_alert_delivery_outbox_can_be_retried_and_marked(self):
        with tempfile.TemporaryDirectory() as d:
            journal = SignalJournal(Path(d) / "journal.sqlite3")
            journal.record_alert("signal:abc", "NQ/MNQ alert", "abc")

            pending = journal.pending_alerts()
            self.assertEqual([(row["alert_id"], row["message"]) for row in pending], [("signal:abc", "NQ/MNQ alert")])
            self.assertTrue(journal.mark_alert_delivered("signal:abc"))
            self.assertEqual(journal.pending_alerts(), [])
            self.assertFalse(journal.mark_alert_delivered("signal:abc"))


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.signal = {
            "signal_id": "abc",
            "direction": "long",
            "entry_price": 102.0,
            "stop_loss_price": 98.75,
            "target_price": 108.5,
        }
        self.ts = datetime(2026, 11, 6, 17, 1, tzinfo=timezone.utc)

    def test_long_target_is_win(self):
        result = resolve_signal_on_bar(self.signal, Candle(self.ts, 102, 109, 101, 108.5), 1, 30)
        self.assertEqual(result.outcome, "WIN")
        self.assertEqual(result.reason, "target")

    def test_long_stop_is_loss(self):
        result = resolve_signal_on_bar(self.signal, Candle(self.ts, 102, 103, 98.5, 99), 1, 30)
        self.assertEqual(result.outcome, "LOSS")
        self.assertEqual(result.reason, "stop_loss")

    def test_same_bar_stop_and_target_is_conservative_loss(self):
        result = resolve_signal_on_bar(self.signal, Candle(self.ts, 102, 109, 98, 104), 1, 30)
        self.assertEqual(result.outcome, "LOSS")
        self.assertEqual(result.reason, "both_hit_stop_first")

    def test_timeout_is_not_counted_as_win_or_loss(self):
        result = resolve_signal_on_bar(self.signal, Candle(self.ts, 102, 104, 100, 103), 30, 30)
        self.assertEqual(result.outcome, "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
