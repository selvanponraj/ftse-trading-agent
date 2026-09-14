import unittest
from unittest.mock import MagicMock

from ftse_agent import STARTING_BUDGET, apply_broker_snapshot, cap_bot_cash


class CapBotCashTests(unittest.TestCase):
    def test_caps_idle_account_at_budget(self):
        portfolio = {"cash": 0.0, "holdings": {}}
        cap_bot_cash(portfolio, broker_free=50_082.36)
        self.assertEqual(portfolio["cash"], STARTING_BUDGET)

    def test_does_not_invent_cash_the_broker_does_not_have(self):
        portfolio = {"cash": 0.0, "holdings": {}}
        cap_bot_cash(portfolio, broker_free=2_000.0)
        self.assertEqual(portfolio["cash"], 2_000.0)

    def test_subtracts_open_bot_positions_from_budget(self):
        portfolio = {
            "cash": 0.0,
            "holdings": {"BP.L": {"shares": 100, "avg_cost": 9.0}},
        }
        cap_bot_cash(portfolio, broker_free=50_000.0)
        self.assertEqual(portfolio["cash"], 9_100.0)

    def test_zero_cash_when_holdings_already_fill_the_budget(self):
        portfolio = {
            "cash": 0.0,
            "holdings": {"BP.L": {"shares": 200, "avg_cost": 60.0}},
        }
        cap_bot_cash(portfolio, broker_free=50_000.0)
        self.assertEqual(portfolio["cash"], 0.0)


class SnapshotBudgetTests(unittest.TestCase):
    def test_snapshot_caps_cash_at_starting_budget(self):
        broker = MagicMock()
        broker.get_account_cash.return_value = {"free": 50_082.36}
        broker.get_open_positions.return_value = []
        portfolio = {
            "cash": 0.0,
            "holdings": {},
            "trades": [],
            "starting_value": STARTING_BUDGET,
        }
        self.assertTrue(apply_broker_snapshot(portfolio, broker))
        self.assertEqual(portfolio["cash"], STARTING_BUDGET)
        self.assertEqual(portfolio["holdings"], {})

    def test_snapshot_fails_when_positions_are_none(self):
        broker = MagicMock()
        broker.get_account_cash.return_value = {"free": 50_000.0}
        broker.get_open_positions.return_value = None
        portfolio = {
            "cash": 9_000.0,
            "holdings": {"BP.L": {"shares": 10, "avg_cost": 5.0}},
        }
        self.assertFalse(apply_broker_snapshot(portfolio, broker))
        self.assertIn("BP.L", portfolio["holdings"])


class PersistAfterSessionTests(unittest.TestCase):
    def test_failed_end_snapshot_still_saves(self):
        from unittest.mock import patch

        from ftse_agent import persist_after_session

        logs = []
        portfolio = {"cash": 100.0, "holdings": {"BP.L": {"shares": 1}}}
        broker = MagicMock()
        with patch("ftse_agent.apply_broker_snapshot", return_value=False):
            with patch("ftse_agent.save_portfolio") as save:
                with patch("ftse_agent.generate_dashboard") as dash:
                    with patch("ftse_agent.log", lambda m, level="INFO": logs.append(m)):
                        persist_after_session(portfolio, broker, {})
        save.assert_called_once_with(portfolio)
        dash.assert_called_once()
        self.assertTrue(any("BROKER_429 snapshot skipped" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
