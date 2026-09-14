# Dual Broker — T212 and IBKR (scan once)

Date: 2026-09-13  
Status: approved design (implementation not started)

## Problem

The agent can place orders only through Trading 212. The operator also has IB Gateway on port **4002** and wants the same ranked FTSE session to trade there, with a **separate £10,000 book**, and optionally both brokers in one run.

IBKR is an **order router**, not a news source. Yahoo + LiteLLM stay as they are.

## Goal

- `BROKER=T212|IBKR|ALL` selects which order legs run.
- Rank the universe **once** (Yahoo + LiteLLM).
- Apply sells/buys independently per selected broker.
- Each broker has its own **£10,000** cap and artefact files. The two books never share cash.
- T212 HTTP behaviour (429 retry, no cash GET on fills, 2s between orders) is unchanged.

## Non-goals

- IBKR historical news or replacing LiteLLM/Yahoo headlines
- IBKR live port 4001 / TWS 7496 unless env overrides `IBKR_PORT`
- Concurrent threads (ALL is sequential: T212 then IBKR)
- GitHub Actions connecting to Gateway
- Bracket/stop/limit orders on IBKR
- Raising `MAX_POSITIONS` or changing score thresholds / news gate

This spec **does not** implement the older “IBKR news only” design. Headlines remain Yahoo + LiteLLM.

## Environment

```
BROKER=T212
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=7
```

T212 keys remain `TRADING212_API_KEY`, `TRADING212_API_SECRET`, `TRADING212_ENVIRONMENT=demo|live`.

| Value | Legs |
|---|---|
| `T212` | Trading 212 only |
| `IBKR` | IB Gateway only |
| `ALL` | T212 then IBKR |
| missing / other | log and exit 1 |

Defaults: `BROKER=T212`, `IBKR_HOST=127.0.0.1`, `IBKR_PORT=4002`, `IBKR_CLIENT_ID=7`.

A selected T212 leg with placeholder keys is **dropped**. A selected IBKR leg that cannot import `ib_insync` or cannot connect is **dropped**. If **no** legs remain, exit 1. On `ALL`, one failed leg does not abort the other.

## Artefacts (per leg)

| Leg | Book | Dashboard | Log |
|---|---|---|---|
| T212 | `portfolio.json` | `dashboard.html` | `trading_log.txt` |
| IBKR | `ibkr_portfolio.json` | `ibkr_dashboard.html` | `ibkr_trading_log.txt` |

Missing book JSON: initialise that file with **£10,000** cash (same shape as today’s `load_portfolio`). `STARTING_BUDGET` is 10,000 **per file**, not 10,000 split across brokers.

Rank-once log lines are written to **every selected leg’s log**. Apply/snapshot/order lines go only to that leg’s log.

## Architecture

```
.env BROKER
    → build legs
    → rank_universe() once   # Yahoo + LLM, no broker
    → for each leg:
          load that portfolio
          snapshot broker (start)
          apply_trades(portfolio, broker, scored)
          persist_after_session for that leg’s paths
```

- `broker.py` — `Trading212Broker` only. Factory returns T212 when that leg is selected and configured.
- `ibkr_broker.py` — `IbkrBroker` (`ib_insync`), connect `IBKR_HOST:IBKR_PORT` with `IBKR_CLIENT_ID`.
- `ftse_agent.py` — split today’s `run_session` into **rank** vs **apply**. Scoring, gates, sizing, local `_refresh_cash` unchanged. Snapshot ticker→Yahoo goes through the broker (`broker_to_yahoo`) so T212 suffixes and IBKR LSE symbols both work.
- `requirements.txt` — add `ib_insync`.
- `.env.example` — `BROKER` and `IBKR_*` placeholders.

`IbkrBroker` duck-types the methods the session already calls. Keep `resolve_t212_ticker(yahoo)` as the existing getattr hook (IBKR returns the LSE symbol). Also expose `environment` and `base_url` for the existing broker log line (`environment="ibkr"`, `base_url` = `host:port`).

## IBKR adapter

**Connect:** `IB().connect(host, port, clientId=...)`. Failure: log `BROKER_FAIL IBKR connect`, skip leg. Disconnect when the IBKR leg finishes.

**`get_account_cash`:** `{ "free": <float> }` from GBP `AvailableFunds`, else GBP `TotalCashValue`. `None` on failure.

**`get_open_positions`:** `{ticker, quantity, averagePrice}` for **LSE** names whose Yahoo form (`{symbol}.L`) is in `FTSE_UNIVERSE`. All other Gateway positions are ignored. `None` on transport/API failure (never pretend empty vs failed).

**`broker_to_yahoo`:** LSE `HSBA` → `HSBA.L`. T212 keeps `t212_to_yahoo`.

**`resolve_t212_ticker("HSBA.L")`:** strip `.L` → `HSBA`, qualify `Stock('HSBA', 'LSE', 'GBP')`. `None` if qualify fails.

**`place_market_order(ticker, qty)`:** market BUY, integer shares, wait up to **15s** for fill. Success: `{id, ticker, quantity}`. Reject/timeout: `None` (do not book locally).

**`close_position(ticker, qty)`:** market SELL same size; same success/`None` rules.

Market orders only. No IBKR news requests.

Start snapshot uses the same `apply_broker_snapshot` helper and £10k `cap_bot_cash`. Failure **skips that leg** (T212 on `ALL` still runs). End snapshot failure: log `BROKER_FAIL snapshot skipped` (T212 may still log `BROKER_429 snapshot skipped` for HTTP 429) and still write that leg’s JSON + dashboard.

## Session apply (per leg)

Unchanged rules on **that** book: max 10 positions, buy threshold 62, news must be scored, stop-loss without news, 2s sleep between **that** broker’s orders, local cash deltas, failed order not booked.

The shared ranked list is reused. A name already held on T212 can still be bought on IBKR if the IBKR book has a slot, and vice versa.

## Tests (no live Gateway)

- `BROKER` parsing and leg list (T212 / IBKR / ALL); placeholder T212 dropped; failed IBKR connect dropped.
- `HSBA.L` → `HSBA`; positions filter keeps universe LSE, drops others.
- Shared scores: two fake brokers, two portfolios; a buy on one does not mutate the other JSON.
- Existing T212, news-gate, and budget tests still pass.

Mock `ib_insync` in unit tests. Do not require Gateway for CI.

## Files to change

- `ibkr_broker.py` (new)
- `ftse_agent.py` (rank/apply split, per-leg paths, `broker_to_yahoo` in snapshot)
- `broker.py` (T212 factory only; add `broker_to_yahoo` wrapping `t212_to_yahoo`)
- `.env.example`, `requirements.txt`
- `tests/test_ibkr_broker.py`, `tests/test_broker_legs.py` (new)
- Existing `tests/test_session_gate.py` / `tests/test_broker.py` stay valid for T212
