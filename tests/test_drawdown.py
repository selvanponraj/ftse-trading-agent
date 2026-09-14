import unittest

from ftse_agent import STARTING_BUDGET, bot_snapshot_nav, peak_and_drawdown


class DrawdownTests(unittest.TestCase):
    def test_full_account_snapshot_is_not_the_peak(self):
        snapshots = [
            {"total_value": 50_082.36, "equity_value": 0.0},
            {"total_value": 10_000.0, "equity_value": 0.0},
        ]
        peak, drawdown = peak_and_drawdown(10_000.0, snapshots)
        self.assertEqual(peak, STARTING_BUDGET)
        self.assertEqual(drawdown, 0.0)

    def test_real_bot_peak_above_budget_is_kept(self):
        snapshots = [
            {"total_value": 50_082.36, "equity_value": 0.0},
            {"total_value": 11_200.0, "equity_value": 2_000.0},
        ]
        peak, drawdown = peak_and_drawdown(10_000.0, snapshots)
        self.assertEqual(peak, 11_200.0)
        self.assertAlmostEqual(drawdown, (10_000 - 11_200) / 11_200 * 100)

    def test_idle_budget_is_the_floor_peak(self):
        peak, drawdown = peak_and_drawdown(10_000.0, [])
        self.assertEqual(peak, STARTING_BUDGET)
        self.assertEqual(drawdown, 0.0)

    def test_nav_normalizer_rewrites_uncapped_cash_dump(self):
        self.assertEqual(
            bot_snapshot_nav({"total_value": 50_082.36, "equity_value": 0.0}),
            STARTING_BUDGET,
        )
