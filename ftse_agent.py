#!/usr/bin/env python3
"""
FTSE 250 Paper Trading Agent
Strategy: News-driven + Fundamental Analysis
Starting budget: £10,000
Runs on GitHub Actions — Mon-Fri after market close
"""

import json
import time
from datetime import datetime, date
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

# ── Config ───────────────────────────────────────────────────────────────────────
# All paths relative to repo root (works in GitHub Actions)
PORTFOLIO_FILE = Path("portfolio.json")
DASHBOARD_FILE = Path("dashboard.html")
LOG_FILE       = Path("trading_log.txt")

STARTING_BUDGET   = 10_000.0
MAX_POSITIONS     = 10
POSITION_SIZE_PCT = 0.09
MAX_POSITION_PCT  = 0.14
BUY_THRESHOLD     = 62
SELL_SCORE_THRESH = 32
STOP_LOSS_PCT     = -12.0
TAKE_PROFIT_PCT   = 28.0
TRAILING_CUT_PCT  = -8.0

# ── FTSE 250 Universe ─────────────────────────────────────────────────────────────

FTSE_UNIVERSE = [
    # Financials & Asset Management
    "ABDN.L", "INVP.L", "LRE.L",  "QLT.L",  "EMG.L",  "ITRK.L", "OSB.L", "TPK.L",
    
    # Consumer & Retail
    "JDW.L",  "HFD.L",  "CARD.L", "SMWH.L", "WOSG.L", "MKS.L",  "TATE.L", "SBRY.L", "BME.L",
    
    # Industrials & Engineering
    "IMI.L",  "MGAM.L", "VSVS.L", "VCT.L",  "MSLH.L", "SHI.L",  "4IM.L",  "CHG.L",
    
    # Technology & Software
    "ALFA.L", "BYIT.L", "CTEC.L", "AUTO.L", "KIE.L",  "SGE.L",
    
    # Energy, Utilities & Mining
    "UKW.L",  "NESF.L", "BOY.L",  "HBR.L",  "ENOG.L",
    
    # Real Estate & REITs
    "SAFE.L", "UTG.L",  "WKP.L",  "PHP.L", "BBOX.L", "DLN.L",
    
    # Healthcare & Life Sciences
    "OXB.L",  "ELM.L",  "GNS.L",
    
    # Travel, Leisure & Media
    "MAB.L",  "RCH.L",  "ITV.L",  "JET2.L", "WTB.L",
    
    # Housebuilders & Construction
    "CRST.L", "BTRW.L", "PSN.L",  "BKG.L",  "BWY.L",
    
    # Diversified & Growth
    "TEP.L",  "INCH.L", "IPO.L",  "RHIM.L", "GAW.L",
    "TBCG.L", "NXT.L",  "DPLM.L", "ESNT.L", "HTWS.L",
]



# ── News Sentiment Words ──────────────────────────────────────────────────────────
POSITIVE_WORDS = [
    "profit", "growth", "beat", "strong", "upgrade", "record", "increased",
    "raised", "positive", "outperform", "buy", "bullish", "exceed", "boost",
    "gain", "rally", "surge", "robust", "confident", "dividend", "acquisition",
    "partnership", "innovation", "expansion", "revenue up", "earnings beat",
    "ahead of", "better than", "breakthrough", "win", "contract", "upgraded",
    "recovery", "rebound", "optimistic", "momentum", "new high", "raises guidance",
]
NEGATIVE_WORDS = [
    "loss", "decline", "miss", "warning", "cut", "reduced", "negative",
    "downgrade", "sell", "bearish", "fall", "drop", "weak", "disappoint",
    "concern", "risk", "debt", "layoff", "restructure", "below expectations",
    "shortfall", "challenging", "headwind", "investigation", "fine", "penalty",
    "profit warning", "disappoints", "lowers guidance", "misses", "defaults",
    "insolvency", "lawsuit", "recall", "scandal", "departure", "struggles",
]


# ── Logging ───────────────────────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Portfolio I/O ─────────────────────────────────────────────────────────────────
def load_portfolio() -> dict:
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    p = {
        "cash":             STARTING_BUDGET,
        "holdings":         {},
        "trades":           [],
        "daily_snapshots":  [],
        "created":          str(date.today()),
        "starting_value":   STARTING_BUDGET,
        "version":          "1.0",
    }
    save_portfolio(p)
    log(f"Initialised new portfolio with £{STARTING_BUDGET:,.2f}")
    return p

def save_portfolio(p: dict):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)


# ── Price Normalisation ───────────────────────────────────────────────────────────
def gbx_to_gbp(price: float, ticker: str) -> float:
    """yfinance returns LSE (.L) stocks in GBX (pence). Convert to GBP (pounds)."""
    return round(price / 100, 4) if ticker.endswith(".L") else round(price, 4)


# ── Market Data ───────────────────────────────────────────────────────────────────
def analyse_news(items: list) -> tuple[float, list]:
    headlines, scores = [], []
    for item in items:
        title = (item.get("title") or "").strip()
        text  = title.lower() + " " + (item.get("summary") or "").lower()
        pos   = sum(1 for w in POSITIVE_WORDS if w in text)
        neg   = sum(1 for w in NEGATIVE_WORDS if w in text)
        score = max(0, min(100, 50 + pos * 9 - neg * 11))
        scores.append(score)
        if title:
            headlines.append({"title": title, "score": score})
    avg = float(np.mean(scores)) if scores else 50.0
    return round(avg, 1), headlines[:6]

def get_stock_data(ticker: str) -> dict | None:
    try:
        stock = yf.Ticker(ticker)
        info  = stock.info or {}
        hist  = stock.history(period="3mo")
        if hist.empty or len(hist) < 5:
            return None
        price = gbx_to_gbp(float(hist["Close"].iloc[-1]), ticker)
        if price <= 0:
            return None
        p1m = gbx_to_gbp(float(hist["Close"].iloc[-22]) if len(hist) >= 22 else float(hist["Close"].iloc[0]), ticker)
        p3m = gbx_to_gbp(float(hist["Close"].iloc[0]), ticker)

        delta = hist["Close"].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = float((100 - (100 / (1 + rs))).iloc[-1])
        if np.isnan(rsi):
            rsi = 50.0

        try:
            news_items = stock.news or []
        except Exception:
            news_items = []
        ns, headlines = analyse_news(news_items[:12])

        return {
            "ticker":          ticker,
            "name":            info.get("longName") or info.get("shortName") or ticker,
            "sector":          info.get("sector", "Unknown"),
            "current_price":   round(price, 4),
            "pe_ratio":        info.get("trailingPE"),
            "forward_pe":      info.get("forwardPE"),
            "peg_ratio":       info.get("pegRatio"),
            "revenue_growth":  info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "profit_margin":   info.get("profitMargins"),
            "return_on_equity":info.get("returnOnEquity"),
            "debt_to_equity":  info.get("debtToEquity"),
            "mom_1mo":         round((price - p1m) / p1m * 100, 2),
            "mom_3mo":         round((price - p3m) / p3m * 100, 2),
            "rsi":             round(rsi, 1),
            "news_sentiment":  ns,
            "news_headlines":  headlines,
        }
    except Exception as e:
        log(f"  Data error {ticker}: {e}", "WARN")
        return None


# ── Composite Scoring ─────────────────────────────────────────────────────────────
def score_stock(d: dict) -> float:
    s, w = [], []

    def number(v):
        """Convert yfinance values (including numeric strings) to floats."""
        if v is None:
            return None
        try:
            value = float(str(v).replace(",", "").strip())
            return None if np.isnan(value) else value
        except (TypeError, ValueError):
            return None

    def add(v, wt):
        value = number(v)
        if value is not None:
            s.append(value); w.append(float(wt))

    pe = number(d.get("pe_ratio"))
    if pe and pe > 0:
        add(90 if pe < 10 else 75 if pe < 15 else 60 if pe < 20 else
            45 if pe < 25 else 28 if pe < 35 else 12, 2.0)

    fpe = number(d.get("forward_pe"))
    if fpe and 0 < fpe < 100:
        add(90 if fpe < 12 else 70 if fpe < 18 else 50 if fpe < 25 else 22, 1.8)

    peg = number(d.get("peg_ratio"))
    if peg and peg > 0:
        add(92 if peg < 0.8 else 75 if peg < 1.2 else 55 if peg < 2 else 25, 1.5)

    rg = number(d.get("revenue_growth"))
    if rg is not None:
        add(92 if rg > 0.25 else 78 if rg > 0.15 else 63 if rg > 0.08 else
            52 if rg > 0.02 else 38 if rg > -0.05 else 15, 2.2)

    eg = number(d.get("earnings_growth"))
    if eg is not None:
        add(90 if eg > 0.30 else 74 if eg > 0.15 else 58 if eg > 0.05 else
            45 if eg > 0 else 18, 1.8)

    mg = number(d.get("profit_margin"))
    if mg is not None:
        add(90 if mg > 0.25 else 75 if mg > 0.15 else 60 if mg > 0.08 else
            42 if mg > 0.03 else 28 if mg > 0 else 8, 1.5)

    roe = number(d.get("return_on_equity"))
    if roe is not None:
        add(88 if roe > 0.25 else 73 if roe > 0.15 else 58 if roe > 0.08 else
            38 if roe > 0 else 10, 1.2)

    dte = number(d.get("debt_to_equity"))
    if dte is not None:
        add(85 if dte < 0.3 else 70 if dte < 0.7 else 55 if dte < 1.2 else
            38 if dte < 2 else 15, 1.0)

    rsi = number(d.get("rsi", 50)) or 50
    if rsi:
        add(82 if rsi < 25 else 72 if rsi < 40 else 60 if rsi < 55 else
            45 if rsi < 65 else 30 if rsi < 75 else 12, 1.0)

    mom = number(d.get("mom_1mo", 0)) or 0
    add(68 if -3 < mom < 8 else 62 if -12 < mom <= -3 else
        42 if mom >= 8 else 22, 0.8)

    add(d.get("news_sentiment", 50), 2.8)

    return round(sum(a * b for a, b in zip(s, w)) / sum(w), 1) if s else 0.0


# ── Portfolio Valuation ───────────────────────────────────────────────────────────
def get_live_prices(tickers: list) -> dict:
    prices = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="2d")
            if not h.empty:
                prices[t] = gbx_to_gbp(float(h["Close"].iloc[-1]), t)
        except Exception:
            pass
        time.sleep(0.1)
    return prices

def portfolio_total(p: dict, prices: dict | None = None) -> float:
    total = p["cash"]
    for t, h in p["holdings"].items():
        total += (prices or {}).get(t, h["avg_cost"]) * h["shares"]
    return total


# ── Trading Session ───────────────────────────────────────────────────────────────
def run_session(portfolio: dict) -> tuple[dict, list, dict]:
    today, now_ts = str(date.today()), datetime.now().strftime("%Y-%m-%d %H:%M")
    trades = []

    log(f"{'='*60}")
    log(f"FTSE Trading Agent — {now_ts}")
    log(f"Cash: £{portfolio['cash']:,.2f} | Positions: {len(portfolio['holdings'])}")

    live_prices = get_live_prices(list(portfolio["holdings"].keys()))
    total_value = portfolio_total(portfolio, live_prices)
    log(f"Portfolio total: £{total_value:,.2f}")

    # ── SELL PASS ─────────────────────────────────────────────────────────────────
    log("--- Sell evaluation ---")
    for ticker in list(portfolio["holdings"].keys()):
        h        = portfolio["holdings"][ticker]
        price    = live_prices.get(ticker, h["avg_cost"])
        pnl_pct  = (price - h["avg_cost"]) / h["avg_cost"] * 100
        bought   = date.fromisoformat(h.get("first_bought", today))
        days     = (date.today() - bought).days
        sell, reason = False, ""

        if pnl_pct <= STOP_LOSS_PCT:
            sell, reason = True, f"Stop-loss {pnl_pct:+.1f}%"
        elif pnl_pct >= TAKE_PROFIT_PCT:
            sell, reason = True, f"Take-profit {pnl_pct:+.1f}%"
        elif days >= 14 and pnl_pct <= TRAILING_CUT_PCT:
            sell, reason = True, f"Trailing cut {pnl_pct:+.1f}% after {days}d"
        else:
            data = get_stock_data(ticker)
            if data:
                sc = score_stock(data)
                if sc < SELL_SCORE_THRESH and days >= 7:
                    sell, reason = True, f"Score {sc} after {days}d"
                else:
                    log(f"  HOLD {ticker} pnl={pnl_pct:+.1f}% score={sc} {days}d")

        if sell:
            proceeds = price * h["shares"]
            portfolio["cash"] += proceeds
            trade = {
                "date": today, "ticker": ticker, "name": h.get("name", ticker),
                "action": "SELL", "shares": h["shares"], "price": round(price, 4),
                "total": round(proceeds, 2), "pnl_pct": round(pnl_pct, 2), "reason": reason,
            }
            portfolio["trades"].append(trade)
            trades.append(trade)
            del portfolio["holdings"][ticker]
            log(f"  SELL {h['shares']}x {ticker} @ £{price:.2f} | {reason}")

    # ── BUY PASS ──────────────────────────────────────────────────────────────────
    slots = MAX_POSITIONS - len(portfolio["holdings"])
    if slots > 0 and portfolio["cash"] > 500:
        total_value = portfolio_total(portfolio, live_prices)
        log(f"--- Buy scan ({slots} slots, £{portfolio['cash']:,.2f} cash) ---")
        scored = []
        for ticker in FTSE_UNIVERSE:
            if ticker in portfolio["holdings"]:
                continue
            data = get_stock_data(ticker)
            if data:
                sc = score_stock(data)
                scored.append((sc, data))
                pe = data.get("pe_ratio")
                try:
                    pe_text = f"{float(str(pe).replace(',', '').strip()):.1f}" if pe else "N/A"
                except (TypeError, ValueError):
                    pe_text = "N/A"
                log(f"  {ticker:12s} score={sc:5.1f} RSI={data['rsi']:4.1f} "
                    f"P/E={pe_text:6s} "
                    f"news={data['news_sentiment']:4.1f} {data['name'][:28]}")
            time.sleep(0.25)

        scored.sort(key=lambda x: x[0], reverse=True)
        buys = 0
        for sc, data in scored:
            if buys >= slots or sc < BUY_THRESHOLD:
                break
            ticker = data["ticker"]
            price  = data["current_price"]  # Already in GBP (converted by gbx_to_gbp)

            pos_v  = min(total_value * POSITION_SIZE_PCT,
                         total_value * MAX_POSITION_PCT,
                         portfolio["cash"] * 0.85)
            if pos_v < 100:
                continue
            shares = max(1, int(pos_v / price))
            cost   = shares * price
            if cost > portfolio["cash"]:
                shares = max(1, int(portfolio["cash"] * 0.85 / price))
                cost   = shares * price
            if cost < 50 or portfolio["cash"] < cost:
                continue

            portfolio["cash"] -= cost
            portfolio["holdings"][ticker] = {
                "shares":       shares,
                "avg_cost":     round(price, 4),
                "first_bought": today,
                "score_at_buy": sc,
                "name":         data["name"],
            }

            parts = [f"Score {sc}"]
            if data.get("pe_ratio") and 0 < data["pe_ratio"] < 20:
                parts.append(f"P/E {data['pe_ratio']:.1f}")
            if data.get("revenue_growth") and data["revenue_growth"] > 0.05:
                parts.append(f"RevGrow +{data['revenue_growth']*100:.0f}%")
            if data.get("news_sentiment", 50) > 62:
                parts.append(f"News {data['news_sentiment']:.0f}/100")
            if data.get("rsi", 50) < 42:
                parts.append(f"RSI {data['rsi']:.0f} oversold")

            trade = {
                "date": today, "ticker": ticker, "name": data["name"],
                "action": "BUY", "shares": shares, "price": round(price, 4),
                "total": round(cost, 2), "score": sc, "reason": " | ".join(parts),
                "news_headlines": data.get("news_headlines", []),
            }
            portfolio["trades"].append(trade)
            trades.append(trade)
            buys += 1
            log(f"  BUY  {shares}x {ticker} ({data['name'][:28]}) @ £{price:.2f} "
                f"= £{cost:,.2f} | {' | '.join(parts)}")
    else:
        log("No buy slots or insufficient cash.")

    # ── Daily Snapshot ────────────────────────────────────────────────────────────
    fresh      = get_live_prices(list(portfolio["holdings"].keys()))
    final_v    = portfolio_total(portfolio, fresh)
    equity_v   = final_v - portfolio["cash"]
    ret_pct    = (final_v - STARTING_BUDGET) / STARTING_BUDGET * 100

    portfolio["daily_snapshots"].append({
        "date":          today,
        "timestamp":     now_ts,
        "cash":          round(portfolio["cash"], 2),
        "equity_value":  round(equity_v, 2),
        "total_value":   round(final_v, 2),
        "return_pct":    round(ret_pct, 2),
        "positions":     len(portfolio["holdings"]),
        "trades_today":  len(trades),
    })
    log(f"{'='*60}")
    log(f"Done | Total: £{final_v:,.2f} | Return: {ret_pct:+.2f}% | Trades: {len(trades)}")
    return portfolio, trades, fresh


# ── HTML Dashboard ────────────────────────────────────────────────────────────────
def generate_dashboard(portfolio: dict, live_prices: dict):
    today     = str(date.today())
    snapshots = portfolio.get("daily_snapshots", [])
    holdings  = portfolio["holdings"]
    trades    = portfolio["trades"]
    total_val = portfolio_total(portfolio, live_prices)
    cash      = portfolio["cash"]
    equity    = total_val - cash
    ret_pct   = (total_val - STARTING_BUDGET) / STARTING_BUDGET * 100
    ret_gbp   = total_val - STARTING_BUDGET
    started   = portfolio.get("created", today)
    all_sells = [t for t in trades if t["action"] == "SELL"]
    wins      = [t for t in all_sells if t.get("pnl_pct", 0) > 0]
    losses    = [t for t in all_sells if t.get("pnl_pct", 0) <= 0]
    win_rate  = len(wins) / len(all_sells) * 100 if all_sells else 0
    avg_win   = float(np.mean([t["pnl_pct"] for t in wins]))   if wins   else 0
    avg_loss  = float(np.mean([t["pnl_pct"] for t in losses])) if losses else 0
    peak_snap = max(snapshots, key=lambda s: s["total_value"]) if snapshots else None
    peak_val  = peak_snap["total_value"] if peak_snap else STARTING_BUDGET
    drawdown  = (total_val - peak_val) / peak_val * 100 if peak_val > 0 else 0

    rows_h = ""
    for tk, h in holdings.items():
        pr    = live_prices.get(tk, h["avg_cost"])
        val   = pr * h["shares"]
        pnl_g = (pr - h["avg_cost"]) * h["shares"]
        pnl_p = (pr - h["avg_cost"]) / h["avg_cost"] * 100
        col   = "#16a34a" if pnl_p >= 0 else "#dc2626"
        wt    = val / total_val * 100
        rows_h += (f'<tr><td><strong>{tk}</strong><br>'
                   f'<small style="color:#6b7280">{h.get("name","")[:28]}</small></td>'
                   f'<td class="num">{h["shares"]}</td><td class="num">£{h["avg_cost"]:.2f}</td>'
                   f'<td class="num">£{pr:.2f}</td><td class="num">£{val:,.2f}</td>'
                   f'<td class="num" style="color:{col}">{"▲" if pnl_p>=0 else "▼"} '
                   f'£{pnl_g:,.2f}<br><small>{pnl_p:+.1f}%</small></td>'
                   f'<td class="num">{wt:.1f}%</td>'
                   f'<td class="num">{h.get("score_at_buy","—")}</td>'
                   f'<td>{h.get("first_bought","")}</td></tr>')

    rows_t = ""
    for t in reversed(trades[-40:]):
        is_b  = t["action"] == "BUY"
        col   = "#16a34a" if is_b else "#9333ea"
        badge = (f'<span style="background:{col};color:#fff;padding:2px 8px;'
                 f'border-radius:9999px;font-size:.75rem">{t["action"]}</span>')
        pnl   = (f'<span style="color:{"#16a34a" if t.get("pnl_pct",0)>=0 else "#dc2626"}">'
                 f'{t.get("pnl_pct",""):+.1f}%</span>') if not is_b else "—"
        rows_t += (f'<tr><td>{t["date"]}</td><td>{badge}</td>'
                   f'<td><strong>{t["ticker"]}</strong></td>'
                   f'<td class="num">{t["shares"]}</td>'
                   f'<td class="num">£{t["price"]:.2f}</td>'
                   f'<td class="num">£{t["total"]:,.2f}</td>'
                   f'<td class="num">{pnl}</td>'
                   f'<td style="color:#6b7280;font-size:.8rem">{t.get("reason","")[:60]}</td></tr>')

    chart_labels  = json.dumps([s["date"] for s in snapshots])
    chart_values  = json.dumps([s["total_value"] for s in snapshots])
    chart_returns = json.dumps([s["return_pct"] for s in snapshots])

    s_ret   = "+" if ret_pct >= 0 else ""
    ret_cls = "pos" if ret_pct >= 0 else "neg"
    arr     = "▲" if ret_gbp >= 0 else "▼"
    dd_cls  = "neg" if drawdown < 0 else ""

    hl_section = (
        f'<table><thead><tr><th>Stock</th><th class="num">Shares</th>'
        f'<th class="num">Avg Cost</th><th class="num">Current</th>'
        f'<th class="num">Value</th><th class="num">P&amp;L</th>'
        f'<th class="num">Weight</th><th class="num">Score</th><th>Bought</th></tr></thead>'
        f'<tbody>{rows_h}</tbody></table>'
        if holdings else '<div class="empty">No open positions yet</div>'
    )
    tr_section = (
        f'<table><thead><tr><th>Date</th><th>Action</th><th>Ticker</th>'
        f'<th class="num">Shares</th><th class="num">Price</th>'
        f'<th class="num">Total</th><th class="num">P&amp;L</th><th>Reason</th></tr></thead>'
        f'<tbody>{rows_t}</tbody></table>'
        if trades else '<div class="empty">No trades yet</div>'
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FTSE 250 Trading Agent</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.hdr{{background:linear-gradient(135deg,#1e3a5f,#1e293b);padding:24px 32px;border-bottom:1px solid #334155}}
.hdr h1{{font-size:1.6rem;font-weight:700;color:#f1f5f9}}.hdr p{{color:#94a3b8;font-size:.9rem;margin-top:4px}}
.main{{padding:24px 32px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:16px;margin-bottom:28px}}
.kpi{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;top:0;left:0;width:4px;height:100%;background:var(--a,#3b82f6)}}
.kpi label{{font-size:.78rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:8px}}
.kpi .v{{font-size:1.6rem;font-weight:700;color:#f1f5f9}}.kpi .s{{font-size:.82rem;color:#64748b;margin-top:4px}}
.pos{{color:#4ade80!important}}.neg{{color:#f87171!important}}
.sec{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:24px}}
.sec h2{{font-size:1rem;font-weight:600;color:#cbd5e1;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid #334155}}
.cr{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse;font-size:.86rem}}
th{{background:#0f172a;color:#94a3b8;font-weight:500;padding:10px 12px;text-align:left;border-bottom:1px solid #334155}}
td{{padding:10px 12px;border-bottom:1px solid #1e293b;vertical-align:middle}}
tr:hover td{{background:#263348}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.empty{{text-align:center;color:#475569;padding:40px}}
@media(max-width:768px){{.cr{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="hdr">
  <h1>🏦 FTSE 250 Trading Agent</h1>
  <p>Paper trading · Started {started} · Updated {today} · Strategy: News + Fundamentals · Runs on GitHub Actions</p>
</div>
<div class="main">
<div class="grid">
  <div class="kpi" style="--a:#3b82f6"><label>Total Value</label><div class="v">£{total_val:,.2f}</div><div class="s">Started £{STARTING_BUDGET:,.2f}</div></div>
  <div class="kpi" style="--a:{'#4ade80' if ret_pct>=0 else '#f87171'}"><label>Total Return</label><div class="v {ret_cls}">{s_ret}{ret_pct:.2f}%</div><div class="s">{arr} £{abs(ret_gbp):,.2f}</div></div>
  <div class="kpi" style="--a:#a78bfa"><label>Cash</label><div class="v">£{cash:,.2f}</div><div class="s">{cash/total_val*100:.1f}% of portfolio</div></div>
  <div class="kpi" style="--a:#f59e0b"><label>Invested</label><div class="v">£{equity:,.2f}</div><div class="s">{len(holdings)} positions</div></div>
  <div class="kpi" style="--a:#22d3ee"><label>Win Rate</label><div class="v">{win_rate:.0f}%</div><div class="s">{len(wins)}W / {len(losses)}L closed</div></div>
  <div class="kpi" style="--a:#fb923c"><label>Avg Win</label><div class="v">{avg_win:+.1f}%</div><div class="s">Avg loss: {avg_loss:+.1f}%</div></div>
  <div class="kpi" style="--a:#e879f9"><label>Total Trades</label><div class="v">{len(trades)}</div><div class="s">{len([t for t in trades if t['action']=='BUY'])} buys / {len(all_sells)} sells</div></div>
  <div class="kpi" style="--a:#f43f5e"><label>Drawdown</label><div class="v {dd_cls}">{drawdown:.1f}%</div><div class="s">Peak £{peak_val:,.2f}</div></div>
</div>
<div class="cr">
  <div class="sec"><h2>📈 Portfolio Value</h2><canvas id="vc" height="220"></canvas></div>
  <div class="sec"><h2>📊 Daily Return %</h2><canvas id="rc" height="220"></canvas></div>
</div>
<div class="sec"><h2>💼 Current Holdings ({len(holdings)} positions)</h2>{hl_section}</div>
<div class="sec"><h2>🔄 Recent Trades</h2>{tr_section}</div>
</div>
<script>
const L={chart_labels},V={chart_values},R={chart_returns};
const so={{responsive:true,plugins:{{legend:{{display:false}},tooltip:{{mode:'index'}}}},
  scales:{{x:{{grid:{{color:'#1e293b'}},ticks:{{color:'#64748b',maxTicksLimit:8}}}},
           y:{{grid:{{color:'#1e293b'}},ticks:{{color:'#64748b'}}}}}}}};
const vC=document.getElementById('vc').getContext('2d');
const g=vC.createLinearGradient(0,0,0,300);
g.addColorStop(0,'rgba(59,130,246,.3)');g.addColorStop(1,'rgba(59,130,246,0)');
new Chart(vC,{{type:'line',data:{{labels:L,datasets:[{{data:V,borderColor:'#3b82f6',
  backgroundColor:g,fill:true,tension:.4,pointRadius:V.length<20?4:1,
  pointBackgroundColor:'#3b82f6'}}]}},options:{{...so,plugins:{{...so.plugins,
  tooltip:{{callbacks:{{label:c=>' £'+c.raw.toFixed(2)}}}}}}}}}});
const rC=document.getElementById('rc').getContext('2d');
new Chart(rC,{{type:'bar',data:{{labels:L,datasets:[{{data:R,
  backgroundColor:R.map(v=>v>=0?'rgba(74,222,128,.7)':'rgba(248,113,113,.7)'),
  borderColor:R.map(v=>v>=0?'#4ade80':'#f87171'),borderWidth:1}}]}},
  options:{{...so,plugins:{{...so.plugins,tooltip:{{callbacks:{{label:c=>' '+c.raw.toFixed(2)+'%'}}}}}},
  scales:{{...so.scales,y:{{...so.scales.y,ticks:{{callback:v=>v+'%',color:'#64748b'}}}}}}}}}});
</script>
</body>
</html>"""

    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"Dashboard saved → {DASHBOARD_FILE}")


# ── Entry Point ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log("Starting FTSE 250 Paper Trading Agent (Cloud)")
    portfolio = load_portfolio()
    portfolio, trades, fresh_prices = run_session(portfolio)
    save_portfolio(portfolio)
    generate_dashboard(portfolio, fresh_prices)
    log("Complete.")
