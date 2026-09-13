import json
import os
import unittest
from unittest.mock import MagicMock, patch

from llm_news import parse_llm_sentiment, score_headlines

LLM_ENV = {
    "LITE_LLM_BASE_URL": "http://localhost:4000",
    "LITE_LLM_MODEL": "gpt-5.6-luna",
    "LITE_LLM_API_KEY": "test-key",
    "LITE_LLM_TIMEOUT": "30",
}


def env(**extra):
    return {**os.environ, **LLM_ENV, **extra}


def ok_response(score=81, title="BP raises guidance", item_score=85):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "score": score,
                    "headlines": [{"title": title, "score": item_score}],
                })
            }
        }]
    }
    return resp


class ParseTests(unittest.TestCase):
    def test_reads_json_object(self):
        raw = json.dumps({
            "score": 72,
            "headlines": [{"title": "BP beats estimates", "score": 80}],
        })
        score, items = parse_llm_sentiment(raw)
        self.assertEqual(score, 72.0)
        self.assertEqual(items[0]["title"], "BP beats estimates")
        self.assertEqual(items[0]["score"], 80)

    def test_extracts_json_wrapped_in_prose(self):
        raw = (
            "Here is my assessment of the headlines.\n"
            '{"score": 58, "headlines": [{"title": "Mixed outlook", "score": 55}]}\n'
            "Let me know if you want more detail."
        )
        score, items = parse_llm_sentiment(raw)
        self.assertEqual(score, 58.0)
        self.assertEqual(items[0]["score"], 55)

    def test_strips_markdown_fence(self):
        raw = """```json
        {"score": 41, "headlines": [{"title": "Warning", "score": 20}]}
        ```"""
        score, items = parse_llm_sentiment(raw)
        self.assertEqual(score, 41.0)
        self.assertEqual(items[0]["score"], 20)


class ScoreHeadlinesTests(unittest.TestCase):
    def test_no_headlines_returns_none_score(self):
        score, items = score_headlines("BP.L", [])
        self.assertIsNone(score)
        self.assertEqual(items, [])

    def test_missing_env_returns_none_score(self):
        bare = {k: v for k, v in os.environ.items() if not k.startswith("LITE_LLM_")}
        with patch.dict("os.environ", bare, clear=True):
            score, items = score_headlines("BP.L", [{"title": "BP raises guidance"}])
        self.assertIsNone(score)
        self.assertEqual(items, [{"title": "BP raises guidance"}])

    def test_llm_failure_returns_none_score(self):
        logs = []
        with patch("llm_news.requests.post", side_effect=OSError("down")):
            with patch.dict("os.environ", env(), clear=True):
                score, items = score_headlines(
                    "BP.L",
                    [{"title": "BP profit warning"}],
                    log=lambda m, level="INFO": logs.append((level, m)),
                )

        self.assertIsNone(score)
        self.assertEqual(items, [{"title": "BP profit warning"}])
        self.assertTrue(any("LLM_CALL" in m for _, m in logs))
        self.assertTrue(any("LLM_FAIL" in m for _, m in logs))

    def test_successful_call_returns_scores(self):
        with patch("llm_news.requests.post", return_value=ok_response()) as post:
            with patch.dict("os.environ", env(), clear=True):
                score, items = score_headlines(
                    "BP.L", [{"title": "BP raises guidance"}], name="BP p.l.c.",
                )

        self.assertEqual(score, 81.0)
        self.assertEqual(items[0]["score"], 85)
        self.assertTrue(post.called)

    def test_temperature_is_omitted_by_default(self):
        with patch("llm_news.requests.post", return_value=ok_response()) as post:
            with patch.dict("os.environ", env(), clear=True):
                score_headlines("BP.L", [{"title": "BP raises guidance"}])

        self.assertNotIn("temperature", post.call_args.kwargs["json"])

    def test_temperature_is_sent_when_configured(self):
        with patch("llm_news.requests.post", return_value=ok_response()) as post:
            with patch.dict("os.environ", env(LITE_LLM_TEMPERATURE="0"), clear=True):
                score_headlines("BP.L", [{"title": "BP raises guidance"}])

        self.assertEqual(post.call_args.kwargs["json"]["temperature"], 0.0)

    def test_logs_call_and_scores(self):
        logs = []
        with patch("llm_news.requests.post", return_value=ok_response()):
            with patch.dict("os.environ", env(), clear=True):
                score_headlines(
                    "BP.L", [{"title": "BP raises guidance"}], name="BP p.l.c.",
                    log=lambda m, level="INFO": logs.append(m),
                )
        joined = "\n".join(logs)
        self.assertIn("LLM_CALL BP.L", joined)
        self.assertIn("LLM_SCORE BP.L overall=81.0", joined)
        self.assertIn("LLM_HEADLINE BP.L 85 BP raises guidance", joined)


if __name__ == "__main__":
    unittest.main()
