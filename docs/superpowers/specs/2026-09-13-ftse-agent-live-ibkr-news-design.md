# FTSE Agent Live — IBKR News + LiteLLM Sentiment

Date: 2026-09-13  
Status: approved design (implementation not started)

## Problem

`ftse_agent.py` weights news at 2.8, the largest scoring input. `analyse_news` treats missing or unparseable yfinance items as sentiment **50**. Recent sessions log `news=50.0` for every ticker, so news is not informing trades.

The live agent must use real headlines from Interactive Brokers and score those headlines with LiteLLM. Paper GitHub Actions must stay unchanged.

## Goal

Run a local live-session copy of the agent that:

1. Connects to IB Gateway on `127.0.0.1:7497`.
2. Fetches contract-specific historical headlines from IBKR.
3. Scores those headlines with LiteLLM (`gpt-5.6-luna` via local proxy).
4. Keeps the existing buy/sell thresholds, position sizing, and dashboard behaviour.
5. Writes separate live artefacts so paper `portfolio.json` is never mixed in.

This phase does **not** place IBKR orders. Fills stay internal (same as paper: last yfinance close as price). Order routing is out of scope.

## Schedule (live run)

One session per London weekday, **same-day near close**: **16:05–16:15 London**.

That window is still inside continuous trading, close enough to the daily bar used for RSI/momentum, and avoids next-open gap on a news-weighted book.

GitHub Actions `cron: '30 16 * * 1-5'` (UTC) stays as-is for `ftse_agent.py` only. `ftse_agent_live.py` is run locally (manual or a later local scheduler). Changing GHA for live IBKR is out of scope because Gateway is not reachable from GitHub-hosted runners.

## Files and isolation

| Path | Role |
|---|---|
| `ftse_agent.py` | Unchanged paper agent |
| `ftse_agent_live.py` | Live copy: IBKR news + LiteLLM score |
| `.env` | Secrets and endpoints; already gitignored |
| `.env.example` | Same keys with placeholder values |
| `portfolio_live.json` | Live book |
| `dashboard_live.html` | Live dashboard |
| `trading_log_live.txt` | Live log |

Do not read or write `portfolio.json`, `dashboard.html`, or `trading_log.txt` from the live agent.

## Environment

Load with `python-dotenv` at process start. Required keys:

```
LITE_LLM_BASE_URL=http://localhost:4000
LITE_LLM_MODEL=gpt-5.6-luna
LITE_LLM_API_KEY=<your LiteLLM proxy key>
LITE_LLM_TIMEOUT=30
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=7
IBKR_NEWS_PROVIDERS=BRFG+BRFUPDN+DJNL
```

`IBKR_CLIENT_ID` must be an integer not already used by another API client on the same Gateway. Default `7`.

Missing required keys: log error and exit before connecting.

## Components

### 1. IBKR client

- Library: `ib_insync`.
- Connect: `IB().connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)`.
- On connect failure: log and **exit** (no silent yfinance-news fallback).
- After connect: `reqNewsProviders()`; log provider codes. If the list is empty, log a warning that Gateway **Configuration → API → News** is probably off; continue, scores will be 50.

Ticker mapping: strip `.L` from Yahoo symbols. Qualify `Stock(symbol, 'LSE', 'GBP')`. Cache `conId` in memory for the session.

Headlines: `reqHistoricalNews(conId, IBKR_NEWS_PROVIDERS, '', '', 12)` (up to 12 headlines). Sleep **0.3s** between news requests to reduce pacing violations.

Do not call `reqNewsArticle` in this phase (headline text is enough).

If qualify or historical news fails for one ticker: empty headline list for that ticker, log `NEWS_UNAVAILABLE {ticker} {reason}`, continue the scan.

### 2. LiteLLM scorer

OpenAI-compatible chat:

- URL: `{LITE_LLM_BASE_URL}/v1/chat/completions`
- Header: `Authorization: Bearer {LITE_LLM_API_KEY}`
- Timeout: `LITE_LLM_TIMEOUT` seconds
- Model: `LITE_LLM_MODEL`

The model **must not** invent news. User content is only ticker, name, and IBKR headlines. System prompt: score 0–100 from the given headlines; if headlines are empty, return 50 and `unavailable: true`.

Batch size: **8 tickers per request**. Response JSON:

```json
{
  "scores": [
    {
      "ticker": "VOD.L",
      "score": 64,
      "unavailable": false,
      "headlines": [{"title": "...", "score": 70}]
    }
  ]
}
```

Clamp each score to `[0, 100]`. On HTTP/parse/timeout: those tickers get 50 and `NEWS_UNAVAILABLE`. Do not retry more than once per batch.

Tickers with **zero headlines** skip LiteLLM and get 50 immediately.

### 3. Scoring integration

Replace only the news input inside `get_stock_data` / the live scan:

- Keep yfinance for price, history, RSI, momentum, fundamentals.
- Set `news_sentiment` from LiteLLM (or 50 if unavailable).
- Set `news_headlines` from IBKR titles (plus per-headline scores when present).

Keyword lists `POSITIVE_WORDS` / `NEGATIVE_WORDS` are unused in the live file’s news path. Do not use them as a second sentiment layer.

Buy/sell constants stay: `BUY_THRESHOLD=62`, `SELL_SCORE_THRESH=32`, stops/targets unchanged.

## Data flow

1. Load `.env`.
2. Connect IB Gateway; exit if down.
3. Log news providers.
4. Load `portfolio_live.json` (create like paper if missing, £10,000 start).
5. **Sell news:** for each holding, yfinance price + IBKR headlines; LiteLLM in batches of 8; then sell pass (stops still apply before score).
6. **Buy news:** scan `FTSE_UNIVERSE` with yfinance + IBKR headlines (skip names already held); LiteLLM in batches of 8; then buy pass by score rank.
7. Snapshot, save `portfolio_live.json`, write `dashboard_live.html`, append `trading_log_live.txt`.
8. Disconnect IB.

Do not interleave LiteLLM with the yfinance loop. Collect `(ticker, headlines)` first, then score in batches of 8.

## Error handling

| Failure | Behaviour |
|---|---|
| No `.env` / missing key | Exit |
| Gateway not on 7497 | Exit |
| Empty news subscriptions | Warn; all news 50 |
| Qualify / historical news error | That ticker: 50, continue |
| IB pacing error | Sleep 2s, retry once, then 50 |
| LiteLLM down / timeout | Headlines still logged; scores 50 |
| yfinance gap for a ticker | Skip ticker as today |

Never crash the whole session for a single ticker except connect/config failures.

## Testing

1. Gateway up, news enabled: one LSE name returns at least one headline and `news_sentiment != 50` when headlines exist.
2. Gateway down: process exits with a connect error; paper files untouched.
3. LiteLLM down, Gateway up: log shows headlines and `NEWS_UNAVAILABLE` for scores; session completes.
4. Empty headlines: score 50, no LLM call for that ticker.
5. Regression: `python ftse_agent.py` still writes only paper artefacts.

## Non-goals

- IBKR order placement, bracket stops, or live fills
- Changing GitHub Actions schedule
- Fixing yfinance news in `ftse_agent.py`
- Full article body fetch
- Paid news (Benzinga, Fly) unless already in `reqNewsProviders()`

## Dependencies

Add: `ib_insync`, `python-dotenv`, `openai` (or `httpx`/`requests` already present). Prefer `requests` already in `requirements.txt` for LiteLLM HTTP to avoid a new client unless `openai` is clearly simpler. Decision: use **`requests`** for LiteLLM and **`python-dotenv`** + **`ib_insync`**.
