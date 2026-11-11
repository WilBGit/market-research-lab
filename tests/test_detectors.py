import unittest
from datetime import datetime, timedelta, timezone

from nq_monitor.models import Candle
from nq_monitor.strategy import (
    StrategyConfig,
    detect_cisd,
    detect_liquidity_sweep,
    detect_smt,
    find_pd_array,
    evaluate_setup,
)


def bar(i, open_, high, low, close, symbol="NQ"):
    return Candle(
        timestamp=datetime(2026, 11, 6, 17, i, tzinfo=timezone.utc),
        open=open_, high=high, low=low, close=close, volume=100, symbol=symbol
    )


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.config = StrategyConfig(
            tick_size=0.25,
            liquidity_lookback=5,
            cisd_window=3,
            atr_period=3,
            min_body_atr=0.25,
            pd_lookback=8,
            htf_lookback=10,
        )

    def test_detects_bullish_sell_side_liquidity_sweep(self):
        candles = [bar(i, 105, 110, 100, 105) for i in range(5)]
        candles.append(bar(5, 100, 103, 99, 101))
        sweep = detect_liquidity_sweep(candles, 5, self.config)
        self.assertIsNotNone(sweep)
        self.assertEqual(sweep.direction, "long")
        self.assertEqual(sweep.liquidity_side, "sell_side")
        self.assertEqual(sweep.liquidity_level, 100)
        self.assertEqual(sweep.extreme, 99)

    def test_detects_bearish_buy_side_liquidity_sweep(self):
        candles = [bar(i, 105, 110, 100, 105) for i in range(5)]
        candles.append(bar(5, 110, 111, 107, 109))
        sweep = detect_liquidity_sweep(candles, 5, self.config)
        self.assertIsNotNone(sweep)
        self.assertEqual(sweep.direction, "short")
        self.assertEqual(sweep.liquidity_side, "buy_side")
        self.assertEqual(sweep.liquidity_level, 110)
        self.assertEqual(sweep.extreme, 111)

    def test_cisd_requires_displacement_after_sweep(self):
        candles = [bar(i, 100, 101, 99, 100) for i in range(6)]
        candles[5] = bar(5, 100, 103, 98.5, 101)
        sweep = detect_liquidity_sweep(candles, 5, self.config)
        self.assertIsNotNone(sweep)
        candles.extend([
            bar(6, 101, 101.5, 100.5, 101.25),
            bar(7, 101.25, 104, 101, 103.75),
        ])
        cisd = detect_cisd(candles, sweep, 7, self.config)
        self.assertIsNotNone(cisd)
        self.assertEqual(cisd.direction, "long")
        self.assertGreater(cisd.close, sweep.extreme)

    def test_smt_requires_one_market_to_break_and_the_other_not(self):
        primary = [bar(i, 105, 110, 100, 105, "NQ") for i in range(5)]
        secondary = [bar(i, 105, 110, 100, 105, "MNQ") for i in range(5)]
        primary.append(bar(5, 100, 103, 99, 101, "NQ"))
        secondary.append(bar(5, 100, 103, 100.25, 101, "MNQ"))
        smt = detect_smt(primary, secondary, 5, "long", self.config)
        self.assertIsNotNone(smt)
        self.assertEqual(smt.direction, "long")
        self.assertEqual(smt.leader, "NQ")
        self.assertEqual(smt.laggard, "MNQ")

    def test_pd_array_must_be_in_discount_and_touched(self):
        candles = [bar(i, 104, 105, 103, 104) for i in range(10)]
        candles[4] = bar(4, 99, 100, 98.5, 99.5)
        candles[5] = bar(5, 99.5, 101, 99.25, 100.75)
        candles[6] = bar(6, 100.75, 102, 100.5, 101.5)
        candles[9] = bar(9, 101, 102, 99.75, 101)
        pd = find_pd_array(candles, 9, "long", self.config)
        self.assertIsNotNone(pd)
        self.assertEqual(pd.direction, "long")
        self.assertLess(pd.low + (pd.high - pd.low) / 2, pd.equilibrium)
        self.assertTrue(pd.low <= candles[9].high and pd.high >= candles[9].low)

    def test_evaluate_setup_returns_none_without_all_gates(self):
        primary = [bar(i, 105, 110, 100, 105, "NQ") for i in range(5)]
        secondary = [bar(i, 105, 110, 100, 105, "MNQ") for i in range(5)]
        primary.append(bar(5, 100, 103, 99, 101, "NQ"))
        secondary.append(bar(5, 100, 103, 100.25, 101, "MNQ"))
        self.assertIsNone(evaluate_setup(primary, secondary, "NQ", 5, self.config))


if __name__ == "__main__":
    unittest.main()
