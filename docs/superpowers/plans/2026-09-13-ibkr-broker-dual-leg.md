# Dual Broker T212 + IBKR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank the FTSE universe once, then place orders on T212, IBKR Gateway (port 4002), or both, each with its own £10,000 book.

**Architecture:** `BROKER=T212|IBKR|ALL` builds sequential legs. `IbkrBroker` duck-types T212 methods via `ib_insync`. `rank_universe()` has no broker I/O. `apply_session()` trades one book. Artefact paths and logs switch per leg.

**Tech Stack:** Python 3.12, unittest, ib_insync, httpx, yfinance.

## Global Constraints

- Each broker cap is £10,000 (`STARTING_BUDGET` per portfolio file).
- Yahoo + LiteLLM news only; no IBKR news.
- ALL is sequential: T212 then IBKR. No threads.
- IBKR default `127.0.0.1:4002`, `IBKR_CLIENT_ID=7`.
- IBKR snapshot keeps only LSE names in `FTSE_UNIVERSE`.
- Failed order → do not book locally. Failed start snapshot → skip that leg.
- T212 429 retry, 2s inter-order sleep, local cash on fills unchanged.
- Unit tests must not require a live Gateway (`ib_insync` mocked).
- Project tests use `unittest` (`PYTHONPATH=. python -m unittest …`), not pytest.

---

## File map

- Create: `ibkr_broker.py` — Gateway adapter
- Create: `tests/test_ibkr_broker.py`
- Create: `tests/test_broker_legs.py`
- Modify: `broker.py` — `broker_to_yahoo` on `Trading212Broker`
- Modify: `ftse_agent.py` — legs, rank/apply, per-leg paths
- Modify: `.env.example`, `requirements.txt`

### Task 1: T212 `broker_to_yahoo`

**Files:** `broker.py`, `tests/test_broker.py`

**Produces:** `Trading212Broker.broker_to_yahoo(self, ticker: str) -> str`

- [ ] **Step 1:** Add to `TickerMapTests`:

```python
def test_broker_to_yahoo_uses_t212_map(self):
    b = Trading212Broker("k", "s")
    self.assertEqual(b.broker_to_yahoo("HSBAl_EQ"), "HSBA.L")
```

- [ ] **Step 2:** Run `PYTHONPATH=. python -m unittest tests.test_broker.TickerMapTests.test_broker_to_yahoo_uses_t212_map -q` — expect FAIL (`AttributeError`).

- [ ] **Step 3:** On `Trading212Broker`:

```python
def broker_to_yahoo(self, ticker: str) -> str:
    return t212_to_yahoo(ticker)
```

- [ ] **Step 4:** Re-run the test — PASS.

### Task 2: IBKR ticker + position filter (no Gateway)

**Files:** Create `ibkr_broker.py`, `tests/test_ibkr_broker.py`

**Produces:**
- `yahoo_to_ibkr(yahoo: str) -> str`
- `ibkr_to_yahoo(symbol: str) -> str`
- `IbkrBroker.broker_to_yahoo`
- `IbkrBroker.resolve_t212_ticker`
- `IbkrBroker.get_open_positions` (uses injected `ib` + `universe`)

- [ ] **Step 1:** Write `tests/test_ibkr_broker.py`:

```python
import unittest
from unittest.mock import MagicMock

from ibkr_broker import IbkrBroker, ibkr_to_yahoo, yahoo_to_ibkr


class TickerTests(unittest.TestCase):
    def test_yahoo_strips_lse_suffix(self):
        self.assertEqual(yahoo_to_ibkr("HSBA.L"), "HSBA")

    def test_ibkr_symbol_becomes_yahoo(self):
        self.assertEqual(ibkr_to_yahoo("HSBA"), "HSBA.L")


class PositionFilterTests(unittest.TestCase):
    def test_keeps_universe_lse_drops_others(self):
        broker = IbkrBroker(ib=MagicMock(), universe={"HSBA.L": "Financials"})
        hsba = MagicMock()
        hsba.contract.symbol = "HSBA"
        hsba.contract.exchange = "LSE"
        hsba.position = 10
        hsba.avgCost = 15.5
        aapl = MagicMock()
        aapl.contract.symbol = "AAPL"
        aapl.contract.exchange = "NASDAQ"
        aapl.position = 5
        aapl.avgCost = 100
        broker.ib.positions.return_value = [hsba, aapl]
        pos = broker.get_open_positions()
        self.assertEqual(pos, [{"ticker": "HSBA", "quantity": 10, "averagePrice": 15.5}])
```

- [ ] **Step 2:** Run `PYTHONPATH=. python -m unittest tests.test_ibkr_broker -q` — FAIL (import).

- [ ] **Step 3:** Implement mapping helpers and `IbkrBroker` with `get_open_positions` filtering: exchange in `("LSE", "SMART")` is not required if Yahoo form is in `universe`; drop if `{symbol}.L` not in universe. `positions()` exception → return `None`.

- [ ] **Step 4:** Re-run `tests.test_ibkr_broker` — PASS.

### Task 3: IBKR cash, qualify, market orders (mocked IB)

**Files:** `ibkr_broker.py`, `tests/test_ibkr_broker.py`

**Produces:** `get_account_cash() -> dict | None`, `place_market_order`, `close_position`, `ibkr_from_env`

- [ ] **Step 1:** Tests:

```python
class CashTests(unittest.TestCase):
    def test_prefers_gbp_available_funds(self):
        row = MagicMock()
        row.tag, row.currency, row.value = "AvailableFunds", "GBP", "1234.5"
        other = MagicMock()
        other.tag, other.currency, other.value = "TotalCashValue", "USD", "9"
        broker = IbkrBroker(ib=MagicMock(), universe={})
        broker.ib.accountSummary.return_value = [other, row]
        self.assertEqual(broker.get_account_cash(), {"free": 1234.5})


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
```

- [ ] **Step 2:** Run those tests — FAIL.

- [ ] **Step 3:** Implement cash from `accountSummary()` GBP `AvailableFunds` else GBP `TotalCashValue`. Market BUY/SELL via `MarketOrder`; poll `ib.sleep(0.5)` until status in `Filled`/`Cancelled`/`Inactive` or timeout 15s. Filled → `{id, ticker, quantity}`; else `None`. `resolve_t212_ticker` qualifies `Stock(symbol, 'LSE', 'GBP')`. `environment="ibkr"`, `base_url=f"{host}:{port}"`. `ibkr_from_env(universe)` reads `IBKR_HOST/PORT/CLIENT_ID`, imports `ib_insync`, connects; on failure returns `None`.

- [ ] **Step 4:** Re-run `tests.test_ibkr_broker` — PASS.

### Task 4: BROKER legs + artefact paths

**Files:** `ftse_agent.py`, `tests/test_broker_legs.py`, `.env.example`, `requirements.txt`

**Produces:** `parse_broker_choice(raw: str) -> list[str] | None`, `set_leg_paths(name: str)`, `load_portfolio(path=None)`

- [ ] **Step 1:**

```python
class ParseBrokerTests(unittest.TestCase):
    def test_all_is_t212_then_ibkr(self):
        self.assertEqual(ftse_agent.parse_broker_choice("ALL"), ["T212", "IBKR"])
    def test_default_t212(self):
        self.assertEqual(ftse_agent.parse_broker_choice(""), ["T212"])
    def test_invalid_is_none(self):
        self.assertIsNone(ftse_agent.parse_broker_choice("FOO"))
```

- [ ] **Step 2:** Run — FAIL.

- [ ] **Step 3:** Implement parse (`""`/`None` → `["T212"]`). `set_leg_paths("IBKR")` sets module `PORTFOLIO_FILE`/`DASHBOARD_FILE`/`LOG_FILE` to `ibkr_*` names. `load_portfolio`/`save_portfolio` use current `PORTFOLIO_FILE`. Add `ib_insync` to `requirements.txt`. Extend `.env.example` with `BROKER=T212`, `IBKR_HOST`, `IBKR_PORT=4002`, `IBKR_CLIENT_ID=7`.

- [ ] **Step 4:** Tests PASS.

### Task 5: Rank once, apply per book, snapshot uses `broker_to_yahoo`

**Files:** `ftse_agent.py`, `tests/test_session_gate.py`, `tests/test_broker_legs.py`, `tests/test_budget.py`

**Produces:** `rank_universe() -> list[tuple[float, dict]]`, `apply_session(portfolio, broker, ranked) -> tuple`, `run_session` = rank + apply (existing tests keep working), `apply_broker_snapshot` uses `broker.broker_to_yahoo` if present else `t212_to_yahoo`.

- [ ] **Step 1:** Dual-book test: two FakeBrokers, `ranked = [(64.0, stock())]`, apply on portfolio A then B; A holdings do not appear in B. Snapshot test: mock `broker_to_yahoo` returning `HSBA.L` for `HSBA`.

- [ ] **Step 2:** FAIL until split exists.

- [ ] **Step 3:** Extract current buy-scan loop into `rank_universe()` (do **not** skip holdings). `apply_session` sells using `ranked` lookup for score-sell (fallback `get_stock_data` if missing), buys from ranked skipping names already held. `run_session` calls both. `__main__`: parse BROKER; build legs (`broker_from_env` / `ibkr_from_env`); exit 1 if none; rank once (log to each selected log file); for each leg: `set_leg_paths`, load, snapshot (skip leg on fail), apply, persist (`BROKER_FAIL snapshot skipped` for IBKR end fail; T212 may keep `BROKER_429 snapshot skipped`), disconnect IBKR. T212-only with snapshot fail still skips that only remaining leg → exit 1.

- [ ] **Step 4:** `PYTHONPATH=. python -m unittest tests.test_broker tests.test_session_gate tests.test_budget tests.test_broker_legs tests.test_ibkr_broker tests.test_news_gate tests.test_drawdown tests.test_llm_news -q` — all PASS.
