"""Live check that every FTSE_UNIVERSE Yahoo symbol still has price history."""

import unittest

import yfinance as yf

from ftse_agent import FTSE_UNIVERSE


def missing_yahoo_symbols(tickers: list[str]) -> list[str]:
    missing = []
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
        except Exception:
            missing.append(ticker)
            continue
        if hist is None or hist.empty:
            missing.append(ticker)
    return missing


class UniverseYahooTests(unittest.TestCase):
    def test_universe_is_a_ticker_to_sector_map(self):
        self.assertIsInstance(FTSE_UNIVERSE, dict)
        self.assertTrue(FTSE_UNIVERSE)
        for ticker, sector in FTSE_UNIVERSE.items():
            self.assertTrue(ticker.endswith(".L"), msg=ticker)
            self.assertTrue(sector, msg=ticker)

    def test_all_universe_symbols_have_yahoo_history(self):
        missing = missing_yahoo_symbols(list(FTSE_UNIVERSE))
        self.assertEqual(
            missing,
            [],
            msg="Yahoo has no 5-day history for: " + ", ".join(missing),
        )


if __name__ == "__main__":
    missing = missing_yahoo_symbols(list(FTSE_UNIVERSE))
    print(f"checked={len(FTSE_UNIVERSE)}")
    print("missing=" + (", ".join(missing) if missing else "(none)"))
    unittest.main()
