# T212 Rate Limit — Scan All, Order Few

Date: 2026-09-13  
Status: approved design (implementation not started)

## Problem

A 16:10 session already **scans the full Yahoo universe and ranks it**, then places at most 10 buys. Trading 212 still returned HTTP 429 on `GET /equity/account/cash` because the agent called that endpoint after **every** market order (plus start-of-session cash, positions, and instruments). The order POSTs succeeded; cash reads did not.

Scanning more names does not cause this. Extra T212 **account** GETs do.

Official T212 public API is sensitive to burst account/portfolio reads. Ten `POST /equity/orders/market` plus ten immediate cash GETs in ~15 seconds trips `TooManyRequests`.

## Goal

Keep: score all current `FTSE_UNIVERSE` symbols (Yahoo + LiteLLM), rank, buy/sell at most 10 slots.

Change: T212 traffic during a session is **snapshot → orders only → optional end snapshot**. Local cash tracks fills under the £10,000 cap. 429s are retried on order POST only; a 429 on cash never un-books a successful fill.

## Non-goals

- Splitting rank and order into two scripts
- Raising `MAX_POSITIONS` above 10
- Changing buy/sell thresholds or the news gate
- Hitting T212 during the Yahoo/LLM scan

## Session T212 budget

| When | Calls |
|---|---|
| Start | `GET /equity/account/cash`, `GET /equity/portfolio`, `GET /equity/metadata/instruments` (once, cached on the broker instance) |
| Each sell/buy | `POST /equity/orders/market` only. No cash GET. |
| Between orders | Sleep **2.0** seconds |
| End | `GET /equity/account/cash` then `GET /equity/portfolio`. If either 429s after one retry, keep the local book and log `BROKER_429 snapshot skipped`. |

Resolve Yahoo→T212 using the in-memory instruments cache. Do not re-fetch instruments per ticker.

## Local cash

`_refresh_cash` during fills uses **only** `fallback_delta` (± cost/proceeds) and `cap_bot_cash`. It must not call `get_account_cash`.

End-of-session reconcile may call the same snapshot helper. **Start** failure still **exits** the process (no dummy book). **End** failure logs `BROKER_429 snapshot skipped` and still writes `portfolio.json` / dashboard from the local book.

## 429 handling (`broker._request`)

- On HTTP 429: log `BROKER_429 {method} {path}`, sleep **5** seconds, retry **once**.
- If still 429 or other HTTP error: return `None` (current behaviour).
- Network errors keep the existing up-to-3 retries with 1s sleep.

Order POST returns `None` after retries → do **not** add/remove the holding (same as today). Cash GET `None` at end → do not abort; log and finish.

## Ranking (unchanged)

1. Sell pass (price stops first; score-sell still needs LLM news).
2. Buy scan: every universe ticker not already held; skip unscored news; sort by composite descending; buy while `buys < slots` and `score >= 62` and cash rules hold.

## Tests

- After a successful fake buy, `get_account_cash` call count is **0**.
- `place_market_order` is called once per booked buy.
- Broker `_request`: first 429 then 200 → returns the JSON body (one retry).
- Broker `_request`: two 429s → returns `None`.
- Existing news-gate and £10k cap tests still pass.

## Files

- `broker.py` — 429 retry in `_request`
- `ftse_agent.py` — local cash on fills; 2s inter-order sleep; snapshot at end not per fill
- `tests/test_session_gate.py`, `tests/test_broker.py` — coverage above
