import base64
import logging
import os
import time

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


def yahoo_to_t212(ticker: str) -> str:
    """Yahoo `BP.L` → Trading 212 `BP_UK_EQ`."""
    if ticker.endswith(".L"):
        return f"{ticker[:-2]}_UK_EQ"
    return ticker.replace(".", "_")


_LSE_SUFFIXES = ("_UK_EQ", "_GB_EQ", "_LN_EQ", "_L_EQ", "l_EQ")


def t212_to_yahoo(ticker: str) -> str:
    """Trading 212 `BPl_EQ` / `BP_UK_EQ` → Yahoo `BP.L`."""
    for suffix in _LSE_SUFFIXES:
        if ticker.endswith(suffix):
            return f"{ticker[:-len(suffix)]}.L"
    if ticker.endswith("_EQ"):
        return ticker.removesuffix("_EQ").split("_")[0]
    return ticker.replace("_", ".")


def _is_placeholder(value: str) -> bool:
    return not value or value.startswith("<") or value.startswith("your ")


def broker_from_env() -> "Trading212Broker | None":
    api_key = (os.getenv("TRADING212_API_KEY") or "").strip()
    api_secret = (os.getenv("TRADING212_API_SECRET") or "").strip()
    environment = (os.getenv("TRADING212_ENVIRONMENT") or "").strip().lower()
    if _is_placeholder(api_key) or _is_placeholder(api_secret):
        return None
    if environment not in ("demo", "live"):
        return None
    return Trading212Broker(api_key, api_secret, environment=environment)


class Trading212Broker:
    _FILLED = frozenset({"FILLED"})
    _DEAD = frozenset({"CANCELLED", "CANCELED", "REJECTED"})

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        environment: str = "demo",
        fill_timeout: float = 15,
    ):
        self.environment = environment
        self.fill_timeout = float(fill_timeout)
        self.base_url = f"https://{environment}.trading212.com/api/v0"
        credentials = base64.b64encode(
            f"{api_key}:{api_secret}".encode("utf-8")
        ).decode("utf-8")
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }
        self._instruments: list[dict] | None = None

    def _request(self, method: str, path: str, **kwargs) -> dict | list | None:
        network_attempts = 0
        retried_429 = False
        while True:
            try:
                response = httpx.request(
                    method, f"{self.base_url}{path}", headers=self.headers, **kwargs
                )
                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and not retried_429:
                    logger.warning("BROKER_429 %s %s", method, path)
                    retried_429 = True
                    time.sleep(5)
                    continue
                logger.error(
                    f"HTTP {e.response.status_code} on {method} {path}: {e.response.text}"
                )
                return None
            except httpx.RequestError as e:
                network_attempts += 1
                logger.error(
                    f"Network error on {method} {path} (attempt {network_attempts}/3): {e}"
                )
                if network_attempts >= 3:
                    return None
                time.sleep(1)
            except Exception as e:
                logger.error(f"Request failed for {method} {path}: {e}")
                return None

    def get_open_positions(self) -> list[dict] | None:
        result = self._request("GET", "/equity/portfolio")
        if result is None:
            return None
        return result if isinstance(result, list) else []

    def get_account_cash(self) -> dict | None:
        return self._request("GET", "/equity/account/cash")

    def get_instruments(self) -> list[dict]:
        if self._instruments is None:
            result = self._request("GET", "/equity/metadata/instruments")
            self._instruments = result if isinstance(result, list) else []
        return self._instruments

    def resolve_t212_ticker(self, yahoo: str) -> str | None:
        """Map a Yahoo symbol onto a ticker that actually exists on this T212 account."""
        instruments = [i for i in self.get_instruments() if isinstance(i, dict) and i.get("ticker")]
        if not instruments:
            logger.error("No Trading 212 instruments returned; cannot resolve %s", yahoo)
            return None

        by_ticker = {str(i["ticker"]): i for i in instruments}
        base = yahoo[:-2] if yahoo.endswith(".L") else yahoo.split(".")[0]

        # Explicit LSE shapes, most specific first.
        for candidate in (f"{base}l_EQ", f"{base}_UK_EQ", f"{base}_GB_EQ", f"{base}_LN_EQ", f"{base}_L_EQ"):
            if candidate in by_ticker:
                return candidate

        def is_sterling(item: dict) -> bool:
            return str(item.get("currencyCode") or "").upper() in ("GBX", "GBP")

        same_symbol = [
            (t, i) for t, i in by_ticker.items()
            if str(i.get("shortName") or "").upper() == base.upper()
        ]
        sterling = [t for t, i in same_symbol if is_sterling(i)]
        if len(sterling) == 1:
            return sterling[0]
        if len(same_symbol) == 1 and yahoo.endswith(".L") is False:
            return same_symbol[0][0]
        logger.error("No Trading 212 ticker matched %s (base %s)", yahoo, base)
        return None

    def broker_to_yahoo(self, ticker: str) -> str:
        return t212_to_yahoo(ticker)

    @staticmethod
    def _order_id(order: dict | list | None) -> str | None:
        if not isinstance(order, dict):
            return None
        oid = order.get("id")
        if oid is None:
            return None
        return str(oid)

    def _status(self, order: dict | list | None) -> str:
        if not isinstance(order, dict):
            return ""
        return str(order.get("status") or "").upper()

    def _wait_fill(self, order_id: str, seed: dict | None = None) -> dict | None:
        if seed is not None and self._status(seed) in self._FILLED:
            return seed
        if seed is not None and self._status(seed) in self._DEAD:
            return None
        deadline = time.monotonic() + self.fill_timeout
        last = seed
        while time.monotonic() < deadline:
            last = self._request("GET", f"/equity/orders/{order_id}")
            status = self._status(last)
            if status in self._FILLED:
                return last if isinstance(last, dict) else None
            if status in self._DEAD:
                return None
            time.sleep(0.5)
        last = self._request("GET", f"/equity/orders/{order_id}")
        if self._status(last) in self._FILLED and isinstance(last, dict):
            return last
        return None

    def _cancel(self, order_id: str) -> None:
        logger.warning("T212 cancelled unfilled order id=%s", order_id)
        self._request("DELETE", f"/equity/orders/{order_id}")

    def _market(self, ticker: str, quantity: int) -> dict | None:
        posted = self._request(
            "POST",
            "/equity/orders/market",
            json={"ticker": ticker, "quantity": quantity},
        )
        order_id = self._order_id(posted)
        if not order_id:
            return None
        if self._status(posted) in self._DEAD:
            return None
        filled = self._wait_fill(order_id, seed=posted if isinstance(posted, dict) else None)
        if filled is not None:
            return filled
        self._cancel(order_id)
        return None

    def place_market_order(self, ticker: str, quantity: int) -> dict | None:
        return self._market(ticker, quantity)

    def close_position(self, ticker: str, quantity: int) -> dict | None:
        return self._market(ticker, -quantity)

    def place_limit_order(
        self,
        ticker: str,
        quantity: int,
        limit_price: float,
        stop_price: float | None = None,
        take_profit: float | None = None,
    ) -> dict | None:
        payload = {
            "ticker": ticker,
            "quantity": quantity,
            "limitPrice": limit_price,
        }
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if take_profit is not None:
            payload["takeProfitPrice"] = take_profit
        return self._request("POST", "/equity/orders/limit", json=payload)

    def place_stop_order(
        self,
        ticker: str,
        quantity: int,
        stop_price: float,
    ) -> dict | None:
        return self._request(
            "POST",
            "/equity/orders/stop",
            json={"ticker": ticker, "quantity": quantity, "stopPrice": stop_price},
        )

