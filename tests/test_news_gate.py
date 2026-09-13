import unittest

from ftse_agent import has_tradeable_news, score_stock


class NewsGateTests(unittest.TestCase):
    def test_missing_news_blocks_trade(self):
        self.assertFalse(has_tradeable_news({"news_sentiment": None}))

    def test_absent_key_blocks_trade(self):
        self.assertFalse(has_tradeable_news({}))

    def test_scored_news_allows_trade(self):
        self.assertTrue(has_tradeable_news({"news_sentiment": 64.0}))

    def test_zero_score_is_still_a_real_reading(self):
        self.assertTrue(has_tradeable_news({"news_sentiment": 0.0}))


class ScoreStockNewsTests(unittest.TestCase):
    def test_unscored_news_is_not_treated_as_neutral(self):
        base = {"pe_ratio": 12.0, "rsi": 40.0, "mom_1mo": 2.0}
        without_news = score_stock({**base, "news_sentiment": None})
        with_neutral = score_stock({**base, "news_sentiment": 50})
        self.assertNotEqual(without_news, with_neutral)


if __name__ == "__main__":
    unittest.main()
