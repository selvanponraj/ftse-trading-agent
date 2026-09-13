import unittest
from unittest.mock import MagicMock, patch

import ftse_agent
from tests.test_session_gate import FakeBroker, fresh_portfolio, stock


class ParseBrokerTests(unittest.TestCase):
    def test_all_is_t212_then_ibkr(self):
        self.assertEqual(ftse_agent.parse_broker_choice("ALL"), ["T212", "IBKR"])

    def test_default_t212(self):
        self.assertEqual(ftse_agent.parse_broker_choice(""), ["T212"])

    def test_invalid_is_none(self):
        self.assertIsNone(ftse_agent.parse_broker_choice("FOO"))


class LegPathTests(unittest.TestCase):
    def tearDown(self):
        ftse_agent.set_leg_paths("T212")

    def test_ibkr_paths(self):
        ftse_agent.set_leg_paths("IBKR")
        self.assertEqual(ftse_agent.PORTFOLIO_FILE.name, "ibkr_portfolio.json")
        self.assertEqual(ftse_agent.DASHBOARD_FILE.name, "ibkr_dashboard.html")
        self.assertEqual(ftse_agent.LOG_FILE.name, "ibkr_trading_log.txt")


class DualBookTests(unittest.TestCase):
    def setUp(self):
        self.universe = patch.object(ftse_agent, "FTSE_UNIVERSE", {"BP.L": "Energy"})
        self.universe.start()
        self.addCleanup(self.universe.stop)
        sleep = patch.object(ftse_agent.time, "sleep", lambda *_a, **_k: None)
        sleep.start()
        self.addCleanup(sleep.stop)
        prices = patch.object(ftse_agent, "get_live_prices", lambda tickers: {})
        prices.start()
        self.addCleanup(prices.stop)
        logger = patch.object(ftse_agent, "log", lambda *a, **k: None)
        logger.start()
        self.addCleanup(logger.stop)

    def test_apply_does_not_share_holdings_across_books(self):
        ranked = [(64.0, stock(news=64.0))]
        a = fresh_portfolio()
        b = fresh_portfolio()
        t212 = FakeBroker()
        ibkr = FakeBroker()
        ftse_agent.apply_session(a, t212, ranked)
        ftse_agent.apply_session(b, ibkr, ranked)
        self.assertIn("BP.L", a["holdings"])
        self.assertIn("BP.L", b["holdings"])
        self.assertIsNot(a["holdings"], b["holdings"])
        a["holdings"]["BP.L"]["shares"] = 999
        self.assertNotEqual(b["holdings"]["BP.L"]["shares"], 999)
        self.assertEqual(len(t212.buys), 1)
        self.assertEqual(len(ibkr.buys), 1)


class SnapshotYahooTests(unittest.TestCase):
    def test_snapshot_uses_broker_to_yahoo(self):
        broker = MagicMock()
        broker.get_account_cash.return_value = {"free": 10_000.0}
        broker.get_open_positions.return_value = [
            {"ticker": "HSBA", "quantity": 3, "averagePrice": 15.0}
        ]
        broker.broker_to_yahoo.return_value = "HSBA.L"
        portfolio = {"cash": 0.0, "holdings": {}}
        self.assertTrue(ftse_agent.apply_broker_snapshot(portfolio, broker))
        self.assertIn("HSBA.L", portfolio["holdings"])
        broker.broker_to_yahoo.assert_called_with("HSBA")


class IbkrSnapshotIsolationTests(unittest.TestCase):
    def _broker(self, positions):
        from ibkr_broker import IbkrBroker

        broker = IbkrBroker(ib=MagicMock(), universe={"BARC.L": "Financials", "ITV.L": "Media"})
        broker.get_account_cash = MagicMock(return_value={"free": 50_000.0})
        broker.get_open_positions = MagicMock(return_value=positions)
        return broker

    def test_does_not_import_foreign_ibkr_positions(self):
        broker = self._broker([
            {"ticker": "ITV", "quantity": 100, "averagePrice": 0.74},
            {"ticker": "BARC", "quantity": 50, "averagePrice": 4.9},
        ])
        portfolio = {"cash": 0.0, "holdings": {}}
        self.assertTrue(ftse_agent.apply_broker_snapshot(portfolio, broker))
        self.assertEqual(portfolio["holdings"], {})

    def test_refreshes_only_names_this_bot_already_tracks(self):
        broker = self._broker([
            {"ticker": "ITV", "quantity": 100, "averagePrice": 0.74},
            {"ticker": "BARC", "quantity": 182, "averagePrice": 4.94},
        ])
        portfolio = {
            "cash": 0.0,
            "holdings": {
                "BARC.L": {
                    "shares": 182,
                    "avg_cost": 4.94,
                    "first_bought": "2026-09-13",
                    "score_at_buy": 76.7,
                    "name": "Barclays PLC",
                    "sector": "Financials",
                    "broker_ticker": "BARC",
                }
            },
        }
        self.assertTrue(ftse_agent.apply_broker_snapshot(portfolio, broker))
        self.assertIn("BARC.L", portfolio["holdings"])
        self.assertNotIn("ITV.L", portfolio["holdings"])
        self.assertEqual(portfolio["holdings"]["BARC.L"]["shares"], 182)

    def test_keeps_local_bot_holding_if_ibkr_has_not_filled_yet(self):
        broker = self._broker([])
        portfolio = {
            "cash": 9_000.0,
            "holdings": {"BARC.L": {"shares": 182, "avg_cost": 4.94, "name": "Barclays"}},
        }
        self.assertTrue(ftse_agent.apply_broker_snapshot(portfolio, broker))
        self.assertIn("BARC.L", portfolio["holdings"])
        self.assertEqual(portfolio["holdings"]["BARC.L"]["shares"], 182)


if __name__ == "__main__":
    unittest.main()
