import os
import unittest
from unittest.mock import MagicMock, patch

import httpx

from broker import Trading212Broker, broker_from_env, t212_to_yahoo, yahoo_to_t212


class TickerMapTests(unittest.TestCase):
    def test_lse_yahoo_becomes_t212(self):
        self.assertEqual(yahoo_to_t212("BP.L"), "BP_UK_EQ")

    def test_t212_round_trips_to_yahoo(self):
        self.assertEqual(t212_to_yahoo("BP_UK_EQ"), "BP.L")
        self.assertEqual(t212_to_yahoo("BP_GB_EQ"), "BP.L")
        self.assertEqual(t212_to_yahoo("BP_L_EQ"), "BP.L")
        self.assertEqual(t212_to_yahoo("BPl_EQ"), "BP.L")
        self.assertEqual(t212_to_yahoo("JET2l_EQ"), "JET2.L")
        self.assertEqual(t212_to_yahoo("AAPL_US_EQ"), "AAPL")

    def test_broker_to_yahoo_uses_t212_map(self):
        b = Trading212Broker("k", "s")
        self.assertEqual(b.broker_to_yahoo("HSBAl_EQ"), "HSBA.L")


class ResolveTickerTests(unittest.TestCase):
    def broker_with(self, instruments):
        broker = Trading212Broker("k", "s", environment="demo")
        broker.get_instruments = MagicMock(return_value=instruments)
        return broker

    def test_matches_lowercase_l_lse_shape(self):
        broker = self.broker_with([
            {"ticker": "BP_US_EQ", "shortName": "BP", "currencyCode": "USD"},
            {"ticker": "BPl_EQ", "shortName": "BP", "currencyCode": "GBX"},
        ])
        self.assertEqual(broker.resolve_t212_ticker("BP.L"), "BPl_EQ")

    def test_matches_uk_suffix_shape(self):
        broker = self.broker_with([{"ticker": "BP_UK_EQ", "shortName": "BP"}])
        self.assertEqual(broker.resolve_t212_ticker("BP.L"), "BP_UK_EQ")

    def test_prefers_sterling_listing_over_us(self):
        broker = self.broker_with([
            {"ticker": "VODl_EQ", "shortName": "VOD", "currencyCode": "GBX"},
            {"ticker": "VOD_US_EQ", "shortName": "VOD", "currencyCode": "USD"},
        ])
        self.assertEqual(broker.resolve_t212_ticker("VOD.L"), "VODl_EQ")

    def test_unknown_instrument_returns_none(self):
        broker = self.broker_with([{"ticker": "AAPL_US_EQ", "shortName": "AAPL"}])
        self.assertIsNone(broker.resolve_t212_ticker("BP.L"))

    def test_empty_instrument_list_returns_none(self):
        broker = self.broker_with([])
        self.assertIsNone(broker.resolve_t212_ticker("BP.L"))


class BrokerFromEnvTests(unittest.TestCase):
    def test_missing_key_returns_none(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TRADING212_")}
        with patch.dict("os.environ", env, clear=True):
            self.assertIsNone(broker_from_env())

    def test_placeholder_secret_returns_none(self):
        with patch.dict("os.environ", {
            "TRADING212_API_KEY": "k",
            "TRADING212_API_SECRET": "<your API secret>",
            "TRADING212_ENVIRONMENT": "demo",
        }, clear=False):
            self.assertIsNone(broker_from_env())

    def test_demo_environment_hits_demo_host(self):
        with patch.dict("os.environ", {
            "TRADING212_API_KEY": "k",
            "TRADING212_API_SECRET": "s",
            "TRADING212_ENVIRONMENT": "demo",
        }, clear=False):
            b = broker_from_env()
        self.assertIsInstance(b, Trading212Broker)
        self.assertEqual(b.base_url, "https://demo.trading212.com/api/v0")
        self.assertEqual(b.environment, "demo")

    def test_live_environment_hits_live_host(self):
        with patch.dict("os.environ", {
            "TRADING212_API_KEY": "k",
            "TRADING212_API_SECRET": "s",
            "TRADING212_ENVIRONMENT": "live",
        }, clear=False):
            b = broker_from_env()
        self.assertIsInstance(b, Trading212Broker)
        self.assertEqual(b.base_url, "https://live.trading212.com/api/v0")
        self.assertEqual(b.environment, "live")


class OrderPayloadTests(unittest.TestCase):
    def test_market_buy_posts_positive_quantity(self):
        broker = Trading212Broker("k", "s", environment="live")
        broker._request = MagicMock(return_value={"id": 1, "status": "FILLED"})
        broker.place_market_order("BP_UK_EQ", 10)
        broker._request.assert_called_with(
            "POST",
            "/equity/orders/market",
            json={"ticker": "BP_UK_EQ", "quantity": 10},
        )

    def test_close_posts_negative_quantity(self):
        broker = Trading212Broker("k", "s", environment="live")
        broker._request = MagicMock(return_value={"id": 1, "status": "FILLED"})
        broker.close_position("BP_UK_EQ", 10)
        broker._request.assert_called_with(
            "POST",
            "/equity/orders/market",
            json={"ticker": "BP_UK_EQ", "quantity": -10},
        )


class FillWaitTests(unittest.TestCase):
    def test_filled_order_is_returned_without_cancel(self):
        broker = Trading212Broker("k", "s", fill_timeout=15)
        broker._request = MagicMock(side_effect=[
            {"id": 9, "status": "NEW"},
            {"id": 9, "status": "FILLED"},
        ])
        with patch("broker.time.sleep"):
            result = broker.place_market_order("BP_UK_EQ", 10)
        self.assertEqual(result["id"], 9)
        methods = [c[0][0] for c in broker._request.call_args_list]
        self.assertNotIn("DELETE", methods)

    def test_unfilled_order_is_cancelled_and_not_returned(self):
        broker = Trading212Broker("k", "s", fill_timeout=0)
        broker._request = MagicMock(side_effect=[
            {"id": 9, "status": "NEW"},
            {"id": 9, "status": "NEW"},
            {},
        ])
        with patch("broker.time.sleep"):
            self.assertIsNone(broker.place_market_order("BP_UK_EQ", 10))
        broker._request.assert_any_call("DELETE", "/equity/orders/9")

    def test_unfilled_close_is_cancelled_and_not_returned(self):
        broker = Trading212Broker("k", "s", fill_timeout=0)
        broker._request = MagicMock(side_effect=[
            {"id": 11, "status": "NEW"},
            {"id": 11, "status": "NEW"},
            {},
        ])
        with patch("broker.time.sleep"):
            self.assertIsNone(broker.close_position("BP_UK_EQ", 10))
        broker._request.assert_any_call("DELETE", "/equity/orders/11")

    def test_rejected_order_is_not_booked(self):
        broker = Trading212Broker("k", "s", fill_timeout=15)
        broker._request = MagicMock(return_value={"id": 3, "status": "REJECTED"})
        with patch("broker.time.sleep"):
            self.assertIsNone(broker.place_market_order("BP_UK_EQ", 10))
        methods = [c[0][0] for c in broker._request.call_args_list]
        self.assertNotIn("DELETE", methods)

    def test_order_404_after_submit_is_treated_as_fill(self):
        broker = Trading212Broker("k", "s", fill_timeout=15)
        posted = {"id": 54900230968, "status": "NEW", "ticker": "BARCl_EQ", "quantity": 184}
        broker._request = MagicMock(side_effect=[posted, Trading212Broker.NOT_FOUND])
        with patch("broker.time.sleep"):
            result = broker.place_market_order("BARCl_EQ", 184)
        self.assertIsNotNone(result)
        self.assertEqual(str(result["id"]), "54900230968")
        methods = [c[0][0] for c in broker._request.call_args_list]
        self.assertNotIn("DELETE", methods)

    def test_order_404_does_not_retry_as_429(self):
        broker = Trading212Broker("k", "s")
        with patch("broker.time.sleep") as sleeper:
            with patch("broker.httpx.request") as mock_request:
                mock_request.return_value = _httpx_response(
                    404, text='{"detail":"Order not found"}'
                )
                result = broker._request(
                    "GET", "/equity/orders/1", missing_ok=True
                )
        self.assertIs(result, Trading212Broker.NOT_FOUND)
        mock_request.assert_called_once()
        sleeper.assert_not_called()

    def test_order_429_after_submit_is_treated_as_fill(self):
        broker = Trading212Broker("k", "s", fill_timeout=15)
        posted = {"id": 54900231213, "status": "NEW", "ticker": "BARCl_EQ", "quantity": 183}
        broker._request = MagicMock(side_effect=[posted, Trading212Broker.RATE_LIMITED])
        with patch("broker.time.sleep"):
            result = broker.place_market_order("BARCl_EQ", 183)
        self.assertIsNotNone(result)
        self.assertEqual(str(result["id"]), "54900231213")
        methods = [c[0][0] for c in broker._request.call_args_list]
        self.assertNotIn("DELETE", methods)

    def test_order_status_429_does_not_sleep_or_retry(self):
        broker = Trading212Broker("k", "s")
        with patch("broker.time.sleep") as sleeper:
            with patch("broker.httpx.request") as mock_request:
                mock_request.return_value = _httpx_response(429, text="too many requests")
                result = broker._request(
                    "GET", "/equity/orders/54900231213", missing_ok=True
                )
        self.assertIs(result, Trading212Broker.RATE_LIMITED)
        mock_request.assert_called_once()
        sleeper.assert_not_called()


def _httpx_response(status: int, json_data=None, text="too many requests"):
    request = httpx.Request("GET", "https://demo.trading212.com/api/v0/equity/account/cash")
    if json_data is not None:
        return httpx.Response(status, json=json_data, request=request)
    return httpx.Response(status, text=text, request=request)


class RateLimitRetryTests(unittest.TestCase):
    @patch("broker.time.sleep")
    @patch("broker.httpx.request")
    def test_retries_once_after_429(self, mock_request, mock_sleep):
        ok = {"free": 1000.0}
        mock_request.side_effect = [_httpx_response(429), _httpx_response(200, ok)]
        broker = Trading212Broker("k", "s")
        self.assertEqual(broker.get_account_cash(), ok)
        mock_sleep.assert_called_once_with(5)
        self.assertEqual(mock_request.call_count, 2)

    @patch("broker.time.sleep")
    @patch("broker.httpx.request")
    def test_two_429s_return_none(self, mock_request, mock_sleep):
        mock_request.side_effect = [_httpx_response(429), _httpx_response(429)]
        broker = Trading212Broker("k", "s")
        self.assertIsNone(broker.get_account_cash())
        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called_once_with(5)

    @patch("broker.httpx.request")
    def test_failed_portfolio_request_returns_none(self, mock_request):
        mock_request.return_value = _httpx_response(500, text="error")
        broker = Trading212Broker("k", "s")
        self.assertIsNone(broker.get_open_positions())
