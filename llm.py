"""Thin helpers around the Groq SDK: client, model discovery, retries, JSON parsing."""
from __future__ import annotations

import json
import os
import re
import time

# Groq rotates models often, so we DISCOVER what your key can use instead of hard-coding one ID.
# The first ID in each list that exists on your account wins. Override manually if you like.
TEXT_MODEL_PREFS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
    "llama-3.1-8b-instant",
]
VISION_MODEL_PREFS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3.6-27b",
    "qwen/qwen3.8-27b",
]


def get_client(api_key: str | None = None):
    from groq import Groq

    key = api_key or os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set (Colab: add it under Secrets, or use getpass).")
    return Groq(api_key=key)


def list_models(client) -> list[str]:
    return sorted(m.id for m in client.models.list().data)


def pick_model(available: list[str], prefs: list[str]) -> str | None:
    for p in prefs:
        if p in available:
            return p
    return None


def chat(client, model, messages, json_mode: bool = False, retries: int = 5, **kwargs):
    """chat.completions.create with retry/backoff on rate limits and a JSON-mode fallback."""
    import groq

    retryable = (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError)
    params = dict(model=model, messages=messages, **kwargs)
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    last = None
    for attempt in range(retries):
        try:
            return client.chat.completions.create(**params)
        except groq.BadRequestError:
            if "response_format" in params:  # model may not support JSON mode -> retry without it
                params.pop("response_format")
                continue
            raise
        except retryable as e:
            last = e
            time.sleep(min(3 * 2**attempt, 30))
    raise RuntimeError(f"Groq call failed after {retries} attempts: {last}")


def strip_think(text: str) -> str:
    """Reasoning models (e.g. Qwen) may emit <think>...</think>; remove it."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def text_of(resp) -> str:
    return strip_think(resp.choices[0].message.content or "")


def extract_json(text: str):
    """Parse JSON from an LLM reply, tolerating code fences and surrounding prose."""
    text = strip_think(text)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model output")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])
