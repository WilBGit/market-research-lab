import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from nq_monitor.instruments import resolve_instrument
from nq_monitor.session import session_status


CT = ZoneInfo("America/Chicago")


class SessionTests(unittest.TestCase):
    def test_saturday_is_closed(self):
        now = datetime(2026, 11, 7, 10, 30, tzinfo=CT)
        self.assertEqual(session_status(now), "WEEKEND")

    def test_sunday_open_starts_at_five_pm_central(self):
        before = datetime(2026, 11, 8, 16, 59, tzinfo=CT)
        after = datetime(2026, 11, 8, 17, 0, tzinfo=CT)
        self.assertEqual(session_status(before), "WEEKEND")
        self.assertEqual(session_status(after), "OPEN")

    def test_daily_break_is_closed(self):
        now = datetime(2026, 11, 9, 16, 30, tzinfo=CT)
        self.assertEqual(session_status(now), "DAILY_BREAK")

    def test_friday_after_four_pm_is_closed(self):
        now = datetime(2026, 11, 6, 16, 1, tzinfo=CT)
        self.assertEqual(session_status(now), "WEEKEND")


class InstrumentTests(unittest.TestCase):
    def test_standard_contracts_are_explicit(self):
        nq = resolve_instrument("NQ")
        mnq = resolve_instrument("MNQ")
        self.assertEqual(nq.source_symbol, "NQ=F")
        self.assertEqual(mnq.source_symbol, "MNQ=F")
        self.assertEqual(nq.status, "VERIFIED_SYMBOL")
        self.assertEqual(mnq.status, "VERIFIED_SYMBOL")

    def test_nmq_is_not_silently_rewritten(self):
        nmq = resolve_instrument("NMQ")
        self.assertIsNone(nmq.source_symbol)
        self.assertEqual(nmq.status, "UNRESOLVED_SYMBOL")
        self.assertIn("MNQ", nmq.note)


if __name__ == "__main__":
    unittest.main()
