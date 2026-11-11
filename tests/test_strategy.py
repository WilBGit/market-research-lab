import unittest
from datetime import datetime, timezone, timedelta

from nq_monitor.models import Candle, CISDEvent, PDArray, SMTEvent, SweepEvent
from nq_monitor.strategy import StrategyConfig, assemble_paper_signal


class StrategyRequirementTests(unittest.TestCase):
    def setUp(self):
        self.config = StrategyConfig(tick_size=0.25, target_r=2.0)
        ts = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)
        self.sweep = SweepEvent(
            direction="long",
            liquidity_side="sell_side",
            index=20,
            timestamp=ts,
            liquidity_level=100.0,
            extreme=99.0,
        )
        self.cisd = CISDEvent(
            direction="long",
            index=22,
            timestamp=ts + timedelta(minutes=2),
            close=102.0,
            confirmation_level=101.0,
        )
        self.smt = SMTEvent(
            direction="long",
            index=20,
            timestamp=ts,
            leader="NQ",
            laggard="MNQ",
            leader_extreme=99.0,
            laggard_extreme=100.5,
        )
        self.pd = PDArray(
            direction="long",
            kind="bullish_fvg",
            index=19,
            low=99.5,
            high=100.5,
            equilibrium=105.0,
        )

    def test_order_requires_smt_liquidity_cisd_and_pd_array(self):
        signal = assemble_paper_signal(
            symbol="NQ",
            entry_price=102.0,
            sweep=self.sweep,
            smt=self.smt,
            cisd=self.cisd,
            pd_array=self.pd,
            config=self.config,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "PAPER_ENTRY")
        self.assertTrue(signal.requirements["smt"])
        self.assertTrue(signal.requirements["liquidity_sweep"])
        self.assertTrue(signal.requirements["cisd"])
        self.assertTrue(signal.requirements["pd_array"])

    def test_missing_smt_blocks_order(self):
        signal = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=None,
            cisd=self.cisd, pd_array=self.pd, config=self.config,
        )
        self.assertIsNone(signal)

    def test_missing_liquidity_sweep_blocks_order(self):
        signal = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=None, smt=self.smt,
            cisd=self.cisd, pd_array=self.pd, config=self.config,
        )
        self.assertIsNone(signal)

    def test_missing_cisd_blocks_order(self):
        signal = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=self.smt,
            cisd=None, pd_array=self.pd, config=self.config,
        )
        self.assertIsNone(signal)

    def test_missing_pd_array_blocks_order(self):
        signal = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=self.smt,
            cisd=self.cisd, pd_array=None, config=self.config,
        )
        self.assertIsNone(signal)

    def test_bullish_cutloss_is_under_sweep_extreme(self):
        signal = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=self.smt,
            cisd=self.cisd, pd_array=self.pd, config=self.config,
        )
        self.assertEqual(signal.stop_loss_price, 98.75)
        self.assertEqual(signal.target_price, 108.5)
        self.assertGreater(signal.risk_points, 0)

    def test_signal_id_is_stable_and_contains_no_live_order_capability(self):
        signal = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=self.smt,
            cisd=self.cisd, pd_array=self.pd, config=self.config,
        )
        duplicate = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=self.smt,
            cisd=self.cisd, pd_array=self.pd, config=self.config,
        )
        self.assertEqual(signal.signal_id, duplicate.signal_id)
        self.assertNotIn("place_order", signal.action.lower())
        self.assertEqual(signal.order_mode, "paper_only")

    def test_same_sweep_does_not_create_second_paper_order(self):
        later_cisd = CISDEvent(
            direction="long", index=23,
            timestamp=self.cisd.timestamp + timedelta(minutes=1),
            close=102.25, confirmation_level=101.0,
        )
        first = assemble_paper_signal(
            symbol="NQ", entry_price=102.0, sweep=self.sweep, smt=self.smt,
            cisd=self.cisd, pd_array=self.pd, config=self.config,
        )
        second = assemble_paper_signal(
            symbol="NQ", entry_price=102.25, sweep=self.sweep, smt=self.smt,
            cisd=later_cisd, pd_array=self.pd, config=self.config,
        )
        self.assertEqual(first.signal_id, second.signal_id)


if __name__ == "__main__":
    unittest.main()
