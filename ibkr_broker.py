import logging
import os
import time

logger = logging.getLogger(__name__)

# IBKR execDetails send POSIX abbreviations (MET) that zoneinfo does not have.
_IBKR_TZ_ALIASES = {
    "MET": "Europe/London",
    "CET": "Europe/London",
    "CEST": "Europe/London",
    "WET": "Europe/London",
    "WEST": "Europe/London",
    "BST": "Europe/London",
    "GMT": "Europe/London",
}


def londonize_ib_datetime(stamp: str) -> str:
    """Rewrite IBKR fill stamps like `20260914 10:05:33 MET` to Europe/London."""
    if not isinstance(stamp, str) or stamp.count(" ") < 2 or "  " in stamp:
        return stamp
    date_part, time_part, zone = stamp.split(" ", 2)
    mapped = _IBKR_TZ_ALIASES.get(zone.strip(), zone.strip())
    return f"{date_part} {time_part} {mapped}"


def patch_ib_timezones() -> None:
    try:
        from ib_insync import util as ib_util
    except ImportError:
        return
    current = ib_util.parseIBDatetime
    if getattr(current, "_ftse_london", False):
        return
    original = current

    def parseIBDatetime(stamp):
        if isinstance(stamp, str):
            stamp = londonize_ib_datetime(stamp)
        return original(stamp)

    parseIBDatetime._ftse_london = True  # type: ignore[attr-defined]
    ib_util.parseIBDatetime = parseIBDatetime


patch_ib_timezones()

_DEAD_STATUSES = {"Cancelled", "Inactive", "ApiCancelled"}

try:
    from ib_insync import MarketOrder, Stock
except ImportError:
    MarketOrder = None
    Stock = None


def yahoo_to_ibkr(yahoo: str) -> str:
    """Yahoo `BARC.L` → `BARC`. IBKR lists BP as `BP.`."""
    clean = str(yahoo).removesuffix(".L")
    if clean == "BP":
        return "BP."
    return clean


def ibkr_to_yahoo(symbol: str) -> str:
    """IBKR `BARC` / `BP.` → Yahoo `BARC.L` / `BP.L`."""
    s = str(symbol)
    if s.endswith(".L"):
        return s
    if s.endswith("."):
        s = s[:-1]
    return f"{s}.L"


def _is_lse_equity(contract) -> bool:
    """A US listing can share an LSE symbol (AMEX `NG` vs National Grid `NG.L`)."""
    if str(getattr(contract, "secType", "") or "") != "STK":
        return False
    if str(getattr(contract, "currency", "") or "").upper() != "GBP":
        return False
    venues = {
        str(getattr(contract, "exchange", "") or "").upper(),
        str(getattr(contract, "primaryExchange", "") or "").upper(),
    }
    return "LSE" in venues


def to_ib_stock(symbol: str):
    if Stock is None:
        logger.error("BROKER_FAIL IBKR ib_insync not installed")
        return None
    return Stock(
        symbol=yahoo_to_ibkr(symbol),
        exchange="SMART",
        primaryExchange="LSE",
        currency="GBP",
    )


class IbkrBroker:
    # Never pull in holdings another IBKR client opened, even if they are in FTSE_UNIVERSE.
    import_untracked_positions = False
    def __init__(
        self,
        ib,
        universe: dict | None = None,
        host: str = "127.0.0.1",
        port: int = 4002,
        fill_timeout: float = 15,
    ):
        self.ib = ib
        self.universe = universe or {}
        self.host = host
        self.port = int(port)
        self.fill_timeout = float(fill_timeout)
        self.environment = "ibkr"
        self.base_url = f"{self.host}:{self.port}"

    def broker_to_yahoo(self, ticker: str) -> str:
        return ibkr_to_yahoo(ticker)

    def _contract(self, ticker: str):
        contract = to_ib_stock(ticker)
        if contract is None:
            return None
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            return None
        return qualified[0]

    def resolve_t212_ticker(self, yahoo: str) -> str | None:
        symbol = yahoo_to_ibkr(yahoo)
        try:
            contract = self._contract(symbol)
        except Exception as e:
            logger.error("IBKR qualify failed %s: %s", yahoo, e)
            return None
        if contract is None:
            return None
        return getattr(contract, "symbol", None) or symbol

    def get_open_positions(self) -> list[dict] | None:
        try:
            raw = self.ib.positions()
        except Exception as e:
            logger.error("IBKR positions failed: %s", e)
            return None
        if raw is None:
            return None
        out = []
        for pos in raw:
            contract = pos.contract
            if not _is_lse_equity(contract):
                continue
            symbol = str(contract.symbol)
            yahoo = ibkr_to_yahoo(symbol)
            if yahoo not in self.universe:
                continue
            qty = int(float(pos.position or 0))
            if qty < 1:
                continue
            out.append({
                "ticker": symbol,
                "quantity": qty,
                "averagePrice": float(pos.avgCost or 0),
            })
        return out

    def get_account_cash(self) -> dict | None:
        try:
            rows = self.ib.accountSummary()
        except Exception as e:
            logger.error("IBKR cash failed: %s", e)
            return None
        gbp_avail = None
        gbp_cash = None
        for row in rows or []:
            if getattr(row, "currency", "") != "GBP":
                continue
            try:
                value = float(row.value)
            except (TypeError, ValueError):
                continue
            tag = getattr(row, "tag", "")
            if tag == "AvailableFunds":
                gbp_avail = value
            elif tag == "TotalCashValue":
                gbp_cash = value
        if gbp_avail is not None:
            return {"free": gbp_avail}
        if gbp_cash is not None:
            return {"free": gbp_cash}
        return None

    @staticmethod
    def _status(trade) -> str:
        return str(getattr(getattr(trade, "orderStatus", None), "status", "") or "")

    def _wait_fill(self, trade) -> bool:
        deadline = time.monotonic() + self.fill_timeout
        while time.monotonic() < deadline:
            status = self._status(trade)
            if status == "Filled":
                return True
            if status in _DEAD_STATUSES:
                return False
            sleeper = getattr(self.ib, "sleep", None)
            if callable(sleeper):
                sleeper(0.5)
            else:
                time.sleep(0.5)
        return self._status(trade) == "Filled"

    def _cancel(self, trade) -> None:
        """An unfilled order stays live at IBKR (queued to the next open) unless pulled."""
        if self._status(trade) in _DEAD_STATUSES:
            return
        order = getattr(trade, "order", None)
        if order is None:
            return
        try:
            self.ib.cancelOrder(order)
            logger.warning(
                "IBKR cancelled unfilled order id=%s",
                getattr(order, "orderId", "?"),
            )
        except Exception as e:
            logger.error("IBKR cancel failed: %s", e)

    def _market(self, ticker: str, quantity: int, action: str) -> dict | None:
        try:
            contract = self._contract(ticker)
            if contract is None:
                return None
            if MarketOrder is not None:
                order = MarketOrder(action, int(quantity), transmit=True)
            else:
                order = (action, int(quantity))
            trade = self.ib.placeOrder(contract, order)
            if not self._wait_fill(trade):
                self._cancel(trade)
                return None
            order_id = getattr(getattr(trade, "order", None), "orderId", None)
            return {"id": order_id, "ticker": ticker, "quantity": int(quantity)}
        except Exception as e:
            logger.error("IBKR order failed %s %s: %s", action, ticker, e)
            return None

    def place_market_order(self, ticker: str, quantity: int) -> dict | None:
        return self._market(ticker, quantity, "BUY")

    def close_position(self, ticker: str, quantity: int) -> dict | None:
        return self._market(ticker, quantity, "SELL")

    def disconnect(self) -> None:
        try:
            self.ib.disconnect()
        except Exception:
            pass


def _import_ib():
    try:
        from ib_insync import IB
    except ImportError:
        return None
    return IB


IBKR_CLIENT_ID = 7


def ibkr_from_env(universe: dict, log=None, client_id: int | None = None) -> IbkrBroker | None:
    def report(msg: str) -> None:
        logger.error(msg)
        if callable(log):
            log(msg, "WARN")

    host = (os.getenv("IBKR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int((os.getenv("IBKR_PORT") or "4002").strip() or "4002")
        cid = IBKR_CLIENT_ID if client_id is None else int(client_id)
    except ValueError:
        report("BROKER_FAIL IBKR invalid IBKR_PORT or IBKR_CLIENT_ID")
        return None
    ib_class = _import_ib()
    if ib_class is None:
        report("BROKER_FAIL IBKR ib_insync not installed — pip install ib_insync")
        return None
    patch_ib_timezones()
    ib = ib_class()
    try:
        ib.connect(host, port, clientId=cid)
    except Exception as e:
        report(
            f"BROKER_FAIL IBKR connect {host}:{port} clientId={cid} "
            f"— {type(e).__name__}: {e}"
        )
        return None
    return IbkrBroker(ib=ib, universe=universe, host=host, port=port)
