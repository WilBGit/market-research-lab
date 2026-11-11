import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_engine import Bar, SimConfig, Trade, simulate_exit  # noqa: E402


class BacktestIntegrityTests(unittest.TestCase):
    def test_same_bar_stop_and_target_is_conservative_loss(self):
        cfg = SimConfig(target_r=1.0, slippage_ticks=0)
        trade = Trade(direction=1, entry=100.0, stop=99.0, target=101.0, entry_index=0)
        result = simulate_exit(trade, [Bar(1, 100.0, 101.0, 99.0, 100.0)], 0, cfg)
        self.assertEqual(result.reason, "stop")
        self.assertLess(result.r_multiple, 0.0)

    def test_entry_is_not_filled_on_signal_bar(self):
        from backtest_engine import schedule_market_entry

        bars = [
            Bar(1, 100.0, 110.0, 90.0, 105.0),
            Bar(2, 101.0, 103.0, 100.0, 102.0),
        ]
        order = schedule_market_entry(bars, signal_index=0, direction=1, stop=99.0, target_r=1.0)
        self.assertEqual(order.fill_index, 1)
        self.assertEqual(order.entry, 101.0)

    def test_session_filter_uses_new_york_clock(self):
        from backtest_engine import in_new_york_session

        # 08:30 PST on Nov 5, 2026 is 11:30 ET, outside the 08:30-11:30 window.
        self.assertFalse(in_new_york_session(1793899800))
        # 05:30 PST is 08:30 ET, the inclusive open boundary.
        self.assertTrue(in_new_york_session(1793889000))


if __name__ == "__main__":
    unittest.main()
