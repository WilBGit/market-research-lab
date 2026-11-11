import unittest
from datetime import datetime, timezone

from nq_monitor.feed import bars_from_records, align_by_timestamp


class FeedNormalizationTests(unittest.TestCase):
    def test_normalizes_sorts_and_deduplicates_records(self):
        records = [
            {"timestamp": "2026-11-06T17:01:00+00:00", "open": 101, "high": 102, "low": 100, "close": 101.5, "volume": 2},
            {"timestamp": "2026-11-06T17:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1},
            {"timestamp": "2026-11-06T17:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1},
        ]
        bars = bars_from_records(records, "NQ")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].timestamp, datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(bars[1].close, 101.5)

    def test_align_by_timestamp_keeps_only_common_closed_bars(self):
        records_a = [
            {"timestamp": "2026-11-06T17:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"timestamp": "2026-11-06T17:01:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        ]
        records_b = [
            {"timestamp": "2026-11-06T17:01:00+00:00", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1},
            {"timestamp": "2026-11-06T17:02:00+00:00", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1},
        ]
        a = bars_from_records(records_a, "NQ")
        b = bars_from_records(records_b, "MNQ")
        aa, bb = align_by_timestamp(a, b)
        self.assertEqual(len(aa), 1)
        self.assertEqual(aa[0].timestamp, bb[0].timestamp)
        self.assertEqual(aa[0].symbol, "NQ")
        self.assertEqual(bb[0].symbol, "MNQ")

    def test_rejects_nan_and_bad_ohlc(self):
        with self.assertRaises(ValueError):
            bars_from_records([{"timestamp": "2026-11-06T17:00:00+00:00", "open": float("nan"), "high": 1, "low": 0, "close": 1, "volume": 1}], "NQ")
        with self.assertRaises(ValueError):
            bars_from_records([{"timestamp": "2026-11-06T17:00:00+00:00", "open": 2, "high": 1, "low": 0, "close": 1, "volume": 1}], "NQ")


if __name__ == "__main__":
    unittest.main()
