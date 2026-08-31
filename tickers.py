from concurrent.futures import ThreadPoolExecutor
import yfinance as yf

EXCLUDED_YF_INDUSTRIES = {
    "Asset Management",
    "Financial Data & Stock Exchanges",
    "Closed-End Fund - Equity",
    "Closed-End Fund - Debt",
    "Closed-End Fund - Foreign",
}


def is_operating_company(ticker: str) -> tuple[str, bool, str]:
    """Inspects yfinance metadata to check if ticker is a standard operating company."""
    try:
        t = yf.Ticker(ticker)
        info = t.info

        quote_type = info.get("quoteType", "")
        sector = info.get("sector", "")
        industry = info.get("industry", "")
        long_name = info.get("longName", "") or ""

        # Filter out ETFs, Mutual Funds, and non-equity quote types
        if quote_type not in ["EQUITY"]:
            return ticker, False, f"Non-equity quoteType: {quote_type}"

        # Filter out Real Estate & REIT sectors
        if "Real Estate" in sector or "REIT" in industry:
            return ticker, False, f"Real Estate/REIT: {industry}"

        # Filter out Asset Managers & Closed-end trusts
        if industry in EXCLUDED_YF_INDUSTRIES:
            return ticker, False, f"Investment vehicle: {industry}"

        # Filter out Trust / Fund keywords in entity name
        if any(
            w in long_name.lower()
            for w in [
                "trust",
                "fund",
                "income fund",
                "infrastructure fund",
                "capital partners",
            ]
        ):
            return ticker, False, f"Fund naming: {long_name}"

        return ticker, True, "Operating Company"
    except Exception as e:
        return ticker, False, f"Lookup error: {e}"


def filter_universe(
    tickers: list[str], max_workers: int = 12
) -> dict[str, list[str]]:
    """Runs concurrent checks across the ticker list."""
    operating_companies = []
    excluded_entities = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(is_operating_company, tickers)

    for ticker, is_valid, reason in results:
        if is_valid:
            operating_companies.append(ticker)
        else:
            excluded_entities.append((ticker, reason))

    return {
        "operating": operating_companies,
        "excluded": excluded_entities,
    }