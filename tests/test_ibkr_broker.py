import unittest
from unittest.mock import MagicMock, patch

from ibkr_broker import IbkrBroker, ibkr_from_env, ibkr_to_yahoo, yahoo_to_ibkr


class TickerTests(unittest.TestCase):
    def test_yahoo_strips_lse_suffix(self):
        self.assertEqual(yahoo_to_ibkr("HSBA.L"), "HSBA")

    def test_barc_stays_barc(self):
        self.assertEqual(yahoo_to_ibkr("BARC.L"), "BARC")

    def test_bp_uses_trailing_dot(self):
        self.assertEqual(yahoo_to_ibkr("BP.L"), "BP.")
        self.assertEqual(yahoo_to_ibkr("BP"), "BP.")
        self.assertEqual(yahoo_to_ibkr("BP."), "BP.")

    def test_ibkr_symbol_becomes_yahoo(self):
        self.assertEqual(ibkr_to_yahoo("HSBA"), "HSBA.L")

    def test_bp_dot_round_trips_to_yahoo(self):
        self.assertEqual(ibkr_to_yahoo("BP."), "BP.L")


def ib_position(symbol, qty, avg, exchange="LSE", currency="GBP", sec_type="STK"):
    pos = MagicMock()
    pos.contract.symbol = symbol
    pos.contract.exchange = exchange
    pos.contract.primaryExchange = exchange
    pos.contract.currency = currency
    pos.contract.secType = sec_type
    pos.position = qty
    pos.avgCost = avg
    return pos


class PositionFilterTests(unittest.TestCase):
    def test_keeps_universe_lse_drops_others(self):
        broker = IbkrBroker(ib=MagicMock(), universe={"HSBA.L": "Financials"})
        broker.ib.positions.return_value = [
            ib_position("HSBA", 10, 15.5),
            ib_position("AAPL", 5, 100, exchange="NASDAQ", currency="USD"),
        ]
        pos = broker.get_open_positions()
        self.assertEqual(
            pos,
            [{"ticker": "HSBA", "quantity": 10, "averagePrice": 15.5}],
        )

    def test_drops_us_listing_that_collides_with_lse_symbol(self):
        broker = IbkrBroker(ib=MagicMock(), universe={"NG.L": "Utilities"})
        broker.ib.positions.return_value = [
            ib_position("NG", 100, 5.98, exchange="AMEX", currency="USD"),
        ]
        self.assertEqual(broker.get_open_positions(), [])

    def test_drops_cfd_and_lse_etf(self):
        broker = IbkrBroker(
            ib=MagicMock(), universe={"IBUS30.L": "Index", "EQSG.L": "ETF"}
        )
        broker.ib.positions.return_value = [
            ib_position("IBUS30", 2, 52665.1, exchange="", currency="USD", sec_type="CFD"),
            ib_position("EQSG", 100, 70.4, exchange="LSEETF"),
        ]
        self.assertEqual(broker.get_open_positions(), [])

    def test_maps_bp_dot_position_to_universe(self):
        broker = IbkrBroker(ib=MagicMock(), universe={"BP.L": "Energy"})
        broker.ib.positions.return_value = [ib_position("BP.", 20, 4.5)]
        pos = broker.get_open_positions()
        self.assertEqual(
            pos,
            [{"ticker": "BP.", "quantity": 20, "averagePrice": 4.5}],
        )
        self.assertEqual(broker.broker_to_yahoo("BP."), "BP.L")

        broker = IbkrBroker(ib=MagicMock(), universe={"HSBA.L": "Financials"})
        broker.ib.positions.side_effect = RuntimeError("disconnected")
        self.assertIsNone(broker.get_open_positions())


class CashTests(unittest.TestCase):
    def test_prefers_gbp_available_funds(self):
        row = MagicMock()
        row.tag, row.currency, row.value = "AvailableFunds", "GBP", "1234.5"
        other = MagicMock()
        other.tag, other.currency, other.value = "TotalCashValue", "USD", "9"
        broker = IbkrBroker(ib=MagicMock(), universe={})
        broker.ib.accountSummary.return_value = [other, row]
        self.assertEqual(broker.get_account_cash(), {"free": 1234.5})

    def test_cash_error_returns_none(self):
        broker = IbkrBroker(ib=MagicMock(), universe={})
        broker.ib.accountSummary.side_effect = RuntimeError("no account")
        self.assertIsNone(broker.get_account_cash())


class OrderTests(unittest.TestCase):
    def test_buy_returns_none_on_timeout(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "Submitted"
        trade.order.orderId = 1
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BP.L": "Energy"}, fill_timeout=0)
        broker._contract = MagicMock(return_value=MagicMock())
        self.assertIsNone(broker.place_market_order("BP", 10))

    def test_unfilled_order_is_cancelled(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "PreSubmitted"
        trade.order.orderId = 12
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BARC.L": "Financials"}, fill_timeout=0)
        broker._contract = MagicMock(return_value=MagicMock())
        self.assertIsNone(broker.place_market_order("BARC", 182))
        ib.cancelOrder.assert_called_once_with(trade.order)

    def test_filled_order_is_not_cancelled(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.order.orderId = 13
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BARC.L": "Financials"}, fill_timeout=1)
        broker._contract = MagicMock(return_value=MagicMock())
        self.assertIsNotNone(broker.place_market_order("BARC", 10))
        ib.cancelOrder.assert_not_called()

    def test_broker_cancelled_order_is_not_cancelled_again(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "Cancelled"
        trade.order.orderId = 14
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BARC.L": "Financials"}, fill_timeout=1)
        broker._contract = MagicMock(return_value=MagicMock())
        self.assertIsNone(broker.place_market_order("BARC", 10))
        ib.cancelOrder.assert_not_called()

    def test_order_is_transmitted(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.order.orderId = 7
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BP.L": "Energy"}, fill_timeout=1)
        broker._contract = MagicMock(return_value=MagicMock())
        with patch("ibkr_broker.MarketOrder") as market_order:
            broker.place_market_order("BP", 10)
        market_order.assert_called_once_with("BUY", 10, transmit=True)

    def test_sell_is_transmitted(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.order.orderId = 8
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BP.L": "Energy"}, fill_timeout=1)
        broker._contract = MagicMock(return_value=MagicMock())
        with patch("ibkr_broker.MarketOrder") as market_order:
            broker.close_position("BP", 4)
        market_order.assert_called_once_with("SELL", 4, transmit=True)

    def test_filled_buy_returns_dict(self):
        ib = MagicMock()
        trade = MagicMock()
        trade.orderStatus.status = "Filled"
        trade.order.orderId = 42
        ib.placeOrder.return_value = trade
        broker = IbkrBroker(ib=ib, universe={"BP.L": "Energy"}, fill_timeout=1)
        broker._contract = MagicMock(return_value=MagicMock())
        self.assertEqual(
            broker.place_market_order("BP", 10),
            {"id": 42, "ticker": "BP", "quantity": 10},
        )


class FromEnvTests(unittest.TestCase):
    def test_missing_library_reports_reason(self):
        msgs = []
        with patch("ibkr_broker._import_ib", return_value=None):
            broker = ibkr_from_env({}, log=lambda m, level="INFO": msgs.append(m))
        self.assertIsNone(broker)
        self.assertTrue(any("ib_insync" in m for m in msgs), msgs)

    def test_connect_error_reports_host_port_and_reason(self):
        msgs = []
        ib = MagicMock()
        ib.connect.side_effect = ConnectionRefusedError("refused")
        with patch("ibkr_broker._import_ib", return_value=lambda: ib):
            with patch.dict("os.environ", {
                "IBKR_HOST": "127.0.0.1",
                "IBKR_PORT": "4002",
            }, clear=False):
                broker = ibkr_from_env({}, log=lambda m, level="INFO": msgs.append(m))
        self.assertIsNone(broker)
        joined = " ".join(msgs)
        self.assertIn("127.0.0.1:4002", joined)
        self.assertIn("refused", joined)

    def test_client_id_comes_from_config_not_env(self):
        ib = MagicMock()
        with patch("ibkr_broker._import_ib", return_value=lambda: ib):
            with patch.dict("os.environ", {"IBKR_CLIENT_ID": "99"}, clear=False):
                broker = ibkr_from_env({})
        self.assertIsNotNone(broker)
        self.assertEqual(ib.connect.call_args.kwargs["clientId"], 7)


class ResolveTests(unittest.TestCase):
    def test_qualify_failure_returns_none(self):
        ib = MagicMock()
        ib.qualifyContracts.side_effect = RuntimeError("no contract")
        broker = IbkrBroker(ib=ib, universe={"HSBA.L": "Financials"})
        self.assertIsNone(broker.resolve_t212_ticker("HSBA.L"))

    def test_qualify_returns_lse_symbol(self):
        ib = MagicMock()
        qualified = MagicMock()
        qualified.symbol = "HSBA"
        ib.qualifyContracts.return_value = [qualified]
        broker = IbkrBroker(ib=ib, universe={"HSBA.L": "Financials"})
        with patch("ibkr_broker.Stock", return_value=MagicMock()):
            self.assertEqual(broker.resolve_t212_ticker("HSBA.L"), "HSBA")

    def test_contract_uses_smart_routing_not_direct_lse(self):
        ib = MagicMock()
        qualified = MagicMock()
        qualified.symbol = "BARC"
        ib.qualifyContracts.return_value = [qualified]
        broker = IbkrBroker(ib=ib, universe={"BARC.L": "Financials"})
        with patch("ibkr_broker.Stock", return_value=MagicMock()) as stock:
            broker._contract("BARC.L")
        stock.assert_called_once_with(
            symbol="BARC",
            exchange="SMART",
            primaryExchange="LSE",
            currency="GBP",
        )

    def test_bp_contract_uses_trailing_dot(self):
        ib = MagicMock()
        qualified = MagicMock()
        qualified.symbol = "BP."
        ib.qualifyContracts.return_value = [qualified]
        broker = IbkrBroker(ib=ib, universe={"BP.L": "Energy"})
        with patch("ibkr_broker.Stock", return_value=MagicMock()) as stock:
            broker._contract("BP.L")
        stock.assert_called_once_with(
            symbol="BP.",
            exchange="SMART",
            primaryExchange="LSE",
            currency="GBP",
        )


class TimezoneTests(unittest.TestCase):
    def test_met_stamp_rewrites_to_london(self):
        from ibkr_broker import londonize_ib_datetime

        self.assertEqual(
            londonize_ib_datetime("20260914 10:05:33 MET"),
            "20260914 10:05:33 Europe/London",
        )

    def test_parse_met_fill_time_does_not_raise(self):
        from zoneinfo import ZoneInfo

        from ibkr_broker import patch_ib_timezones

        patch_ib_timezones()
        from ib_insync.util import parseIBDatetime

        parsed = parseIBDatetime("20260914 10:05:33 MET")
        self.assertEqual(str(parsed.tzinfo), str(ZoneInfo("Europe/London")))


if __name__ == "__main__":
    unittest.main()
