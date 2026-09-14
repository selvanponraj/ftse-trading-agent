"""Score Yahoo headlines with LiteLLM. Headlines come from sentiment.fetch_headlines."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LogFn = Callable[..., None]


def parse_llm_sentiment(raw: str) -> tuple[float, list[dict]]:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Reasoning models often wrap the object in commentary.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    score = max(0.0, min(100.0, float(data["score"])))
    items = []
    for row in data.get("headlines") or []:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        try:
            item_score = int(round(float(row.get("score", score))))
        except (TypeError, ValueError):
            item_score = int(round(score))
        items.append({"title": title, "score": max(0, min(100, item_score))})
    return score, items


def _fallback_titles(headlines: list) -> list[dict]:
    return [{"title": h["title"]} for h in headlines if isinstance(h, dict) and h.get("title")]


def _emit(log: LogFn | None, msg: str, level: str = "INFO") -> None:
    if log is None:
        return
    try:
        log(msg, level)
    except TypeError:
        log(msg)


def _extract_content(payload: dict) -> str:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(part))
        content = "".join(parts)
    if not content:
        content = choice.get("text") or message.get("reasoning_content") or ""
    return str(content or "")


def score_headlines(
    ticker: str,
    headlines: list,
    name: str = "",
    log: LogFn | None = None,
) -> tuple[float | None, list]:
    """Return (score, headlines). A score of None means unscored — callers must not trade."""
    titles = _fallback_titles(headlines)
    if not titles:
        _emit(log, f"LLM_SKIP {ticker} no headlines", "WARN")
        return None, []

    base = os.getenv("LITE_LLM_BASE_URL", "").rstrip("/")
    model = os.getenv("LITE_LLM_MODEL", "")
    api_key = os.getenv("LITE_LLM_API_KEY", "")
    timeout = float(os.getenv("LITE_LLM_TIMEOUT", "30"))
    url = f"{base}/v1/chat/completions"
    if not base or not model or not api_key:
        _emit(log, f"LLM_SKIP {ticker} missing LITE_LLM_* env", "WARN")
        return None, titles

    payload_headlines = [h["title"] for h in titles]
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You score UK equity news sentiment. Use only the headlines given. "
                    "Do not invent stories. Reply with JSON only: "
                    '{"score": 0-100, "headlines": [{"title": str, "score": 0-100}]}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "ticker": ticker,
                    "name": name,
                    "headlines": payload_headlines,
                }),
            },
        ],
    }
    # Reasoning models (gpt-5.x) reject temperature, so only send it when asked for.
    temperature = os.getenv("LITE_LLM_TEMPERATURE", "").strip()
    if temperature:
        try:
            body["temperature"] = float(temperature)
        except ValueError:
            _emit(log, f"LLM_CONFIG bad LITE_LLM_TEMPERATURE={temperature!r}, ignored", "WARN")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    _emit(log, f"LLM_CALL {ticker} model={model} headlines={len(payload_headlines)} POST {url}")
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        content = _extract_content(resp.json())
        score, items = parse_llm_sentiment(content)
        if not items:
            items = [{**h, "score": int(round(score))} for h in titles]
        items = items[:12]
        _emit(log, f"LLM_SCORE {ticker} overall={score:.1f} headlines={len(items)}")
        for row in items:
            _emit(log, f"LLM_HEADLINE {ticker} {row['score']} {row['title'][:80]}")
        return score, items
    except Exception as exc:
        detail = str(exc)
        err_body = ""
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            err_body = (exc.response.text or "")[:300]
            detail = f"HTTP {exc.response.status_code} {err_body}"
        _emit(log, f"LLM_FAIL {ticker} {detail}", "WARN")
        return None, titles
