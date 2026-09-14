import unittest
from unittest.mock import patch

import ftse_agent


def fresh_portfolio(cash=10_000.0, holdings=None):
    return {
        "cash": cash,
        "holdings": holdings or {},
        "trades": [],
        "daily_snapshots": [],
        "created": "2026-09-01",
        "starting_value": 10_000.0,
        "version": "1.0",
    }


def stock(ticker="BP.L", news=64.0, price=5.0):
    return {
        "ticker": ticker,
        "name": "BP p.l.c.",
        "sector": "Energy",
        "current_price": price,
        "pe_ratio": 11.0,
        "forward_pe": 10.0,
        "peg_ratio": 0.7,
        "revenue_growth": 0.3,
        "earnings_growth": 0.4,
        "profit_margin": 0.3,
        "return_on_equity": 0.3,
        "debt_to_equity": 0.2,
        "mom_1mo": 1.0,
        "mom_3mo": 2.0,
        "rsi": 30.0,
        "news_sentiment": news,
        "news_headlines": [{"title": "BP raises guidance", "score": 80}],
    }


class FakeBroker:
    def __init__(self, fail_buy=False, fail_sell=False):
        self.fail_buy = fail_buy
        self.fail_sell = fail_sell
        self.buys = []
        self.sells = []
        self.cash_calls = 0

    def get_account_cash(self):
        self.cash_calls += 1
        return {"free": 50_000.0}

    def resolve_t212_ticker(self, yahoo):
        from broker import yahoo_to_t212
        return yahoo_to_t212(yahoo)

    def place_market_order(self, ticker, quantity):
        if self.fail_buy:
            return None
        self.buys.append((ticker, quantity))
        return {"id": "ok", "ticker": ticker, "quantity": quantity}

    def close_position(self, ticker, quantity):
        if self.fail_sell:
            return None
        self.sells.append((ticker, quantity))
        return {"id": "ok", "ticker": ticker, "quantity": -quantity}


class BuyPassTests(unittest.TestCase):
    def setUp(self):
        self.universe = patch.object(ftse_agent, "FTSE_UNIVERSE", {"BP.L": "Energy"})
        self.universe.start()
        self.addCleanup(self.universe.stop)
        self.logs = []
        logger = patch.object(
            ftse_agent, "log", lambda m, level="INFO": self.logs.append(m)
        )
        logger.start()
        self.addCleanup(logger.stop)
        prices = patch.object(ftse_agent, "get_live_prices", lambda tickers: {})
        prices.start()
        self.addCleanup(prices.stop)
        self.sleeps = []
        sleep = patch.object(ftse_agent.time, "sleep", lambda s=0, *_a, **_k: self.sleeps.append(s))
        sleep.start()
        self.addCleanup(sleep.stop)
        self.broker = FakeBroker()

    def test_unscored_news_produces_no_buy(self):
        with patch.object(ftse_agent, "get_stock_data", lambda t: stock(news=None)):
            _, trades, _ = ftse_agent.run_session(fresh_portfolio(), self.broker)

        self.assertEqual(trades, [])
        self.assertEqual(self.broker.buys, [])
        self.assertTrue(any("SKIP no news score" in m for m in self.logs))

    def test_scored_news_produces_a_buy(self):
        with patch.object(ftse_agent, "get_stock_data", lambda t: stock(news=64.0)):
            portfolio, trades, _ = ftse_agent.run_session(fresh_portfolio(), self.broker)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["action"], "BUY")
        self.assertEqual(self.broker.buys[0][0], "BP_UK_EQ")
        self.assertIn("News 64/100", trades[0]["reason"])
        self.assertIn("BP.L", portfolio["holdings"])
        self.assertEqual(self.broker.cash_calls, 0)
        self.assertIn(2, self.sleeps)

    def test_failed_broker_order_is_not_booked(self):
        self.broker.fail_buy = True
        with patch.object(ftse_agent, "get_stock_data", lambda t: stock(news=64.0)):
            portfolio, trades, _ = ftse_agent.run_session(fresh_portfolio(), self.broker)

        self.assertEqual(trades, [])
        self.assertEqual(portfolio["holdings"], {})
        self.assertTrue(any("BROKER_FAIL BUY" in m for m in self.logs))


class SellPassTests(unittest.TestCase):
    def setUp(self):
        self.universe = patch.object(ftse_agent, "FTSE_UNIVERSE", {})
        self.universe.start()
        self.addCleanup(self.universe.stop)
        self.logs = []
        logger = patch.object(
            ftse_agent, "log", lambda m, level="INFO": self.logs.append(m)
        )
        logger.start()
        self.addCleanup(logger.stop)
        self.sleeps = []
        sleep = patch.object(ftse_agent.time, "sleep", lambda s=0, *_a, **_k: self.sleeps.append(s))
        sleep.start()
        self.addCleanup(sleep.stop)
        self.broker = FakeBroker()
        self.holding = {
            "BP.L": {
                "shares": 100,
                "avg_cost": 5.0,
                "first_bought": "2026-08-01",
                "score_at_buy": 64.0,
                "name": "BP p.l.c.",
                "sector": "Energy",
            }
        }

    def test_unscored_news_holds_instead_of_score_selling(self):
        with patch.object(ftse_agent, "get_live_prices", lambda t: {"BP.L": 4.9}):
            with patch.object(ftse_agent, "get_stock_data", lambda t: stock(news=None)):
                portfolio, trades, _ = ftse_agent.run_session(
                    fresh_portfolio(cash=100.0, holdings=self.holding),
                    self.broker,
                )

        self.assertEqual(trades, [])
        self.assertIn("BP.L", portfolio["holdings"])
        self.assertTrue(any("no news score" in m for m in self.logs))

    def test_stop_loss_still_sells_without_news(self):
        with patch.object(ftse_agent, "get_live_prices", lambda t: {"BP.L": 4.0}):
            with patch.object(ftse_agent, "get_stock_data", lambda t: stock(news=None)):
                portfolio, trades, _ = ftse_agent.run_session(
                    fresh_portfolio(cash=100.0, holdings=self.holding),
                    self.broker,
                )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["action"], "SELL")
        self.assertEqual(self.broker.sells, [("BP_UK_EQ", 100)])
        self.assertIn("Stop-loss", trades[0]["reason"])
        self.assertNotIn("BP.L", portfolio["holdings"])
        self.assertEqual(self.broker.cash_calls, 0)
        self.assertIn(2, self.sleeps)

    def test_failed_broker_sell_keeps_the_holding(self):
        self.broker.fail_sell = True
        with patch.object(ftse_agent, "get_live_prices", lambda t: {"BP.L": 4.0}):
            with patch.object(ftse_agent, "get_stock_data", lambda t: stock(news=None)):
                portfolio, trades, _ = ftse_agent.run_session(
                    fresh_portfolio(cash=100.0, holdings=self.holding),
                    self.broker,
                )

        self.assertEqual(trades, [])
        self.assertIn("BP.L", portfolio["holdings"])
        self.assertTrue(any("BROKER_FAIL SELL" in m for m in self.logs))


if __name__ == "__main__":
    unittest.main()
