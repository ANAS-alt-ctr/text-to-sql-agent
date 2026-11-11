"""LLM clients.

Two implementations share one small interface:

* :class:`OpenAICompatibleClient` -- talks to any OpenAI-compatible
  ``/chat/completions`` endpoint (OpenAI itself, Azure OpenAI, a local
  server, or another gateway). Configured ONLY via environment variables
  (see :mod:`text_to_sql_agent.config`).
* :class:`MockLLM` -- deterministic, scripted responses for tests and
  the smoke test. Needs no key and makes no network calls.

Token usage is taken from the API response when present, otherwise
estimated as ``len(text) // 4`` characters per token. Cost is computed
from the configured per-1k-token prices; a mock run reports $0.00.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .config import Settings


@dataclass
class LLMResponse:
    """One chat completion."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost_usd(self, price_input_per_1k: float, price_output_per_1k: float) -> float:
        return (
            self.input_tokens / 1000.0 * price_input_per_1k
            + self.output_tokens / 1000.0 * price_output_per_1k
        )


class LLMClient(Protocol):
    """Minimal interface every LLM client in this project implements."""

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        ...


def _estimate_tokens(text: str) -> int:
    """Rough fallback when the API returns no usage block."""
    return max(1, len(text) // 4)


class OpenAICompatibleClient:
    """OpenAI-compatible chat completions client (httpx, sync)."""

    def __init__(self, settings: Settings) -> None:
        settings.require_llm_key()
        self._base_url = settings.llm_base_url.rstrip("/")
        self._api_key = settings.llm_api_key
        self._model = settings.llm_model
        self._timeout = settings.llm_timeout_seconds
        self._max_tokens = settings.llm_max_tokens
        self._client = httpx.Client(timeout=self._timeout)

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": 0,
        }
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
        usage = data.get("usage") or {}
        input_tokens = usage.get("prompt_tokens") or _estimate_tokens(
            json.dumps(messages)
        )
        output_tokens = usage.get("completion_tokens") or _estimate_tokens(text)
        return LLMResponse(
            text=text,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            model=data.get("model", self._model),
        )


@dataclass
class MockLLM:
    """Scripted LLM for tests / smoke test. No network, no key.

    Behaviour is driven by ``script``: a list of ``(keyword, responses)``
    pairs. When the last user message contains ``keyword`` (case
    insensitive), the next response from that keyword's list is returned.
    ``responses`` may be plain strings or dicts (serialised to JSON).
    Anything unmatched gets ``default_response``.

    This lets tests script realistic failure modes -- e.g. a first SQL
    answer with a hallucinated column, then a corrected one -- to
    exercise the self-heal loop deterministically.
    """

    script: list[tuple[str, list[Any]]] = field(default_factory=list)
    default_response: str = (
        '{"sql": "SELECT COUNT(*) AS n FROM orders"}'
    )
    calls: list[list[dict[str, str]]] = field(default_factory=list)
    _counters: dict[str, int] = field(default_factory=dict, repr=False)

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        self.calls.append(messages)
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                break
        # Insight requests (from InsightAgent) get a template insight,
        # not a SQL answer -- the script keywords below are for SQL.
        if "Summarise the result" in user_text:
            match = re.search(r"Rows returned \(capped at 100\): (\d+)", user_text)
            n = match.group(1) if match else "?"
            text = (
                f"The query returned {n} row(s). "
                "This is a mock insight for testing -- with a real LLM this "
                "would be a plain-English summary with numbers and caveats."
            )
            return LLMResponse(
                text=text,
                input_tokens=_estimate_tokens(json.dumps(messages)),
                output_tokens=_estimate_tokens(text),
                model="mock",
            )
        text = self.default_response
        for keyword, responses in self.script:
            if keyword.lower() in user_text.lower():
                idx = self._counters.get(keyword, 0)
                chosen = responses[min(idx, len(responses) - 1)]
                self._counters[keyword] = idx + 1
                text = chosen if isinstance(chosen, str) else json.dumps(chosen)
                break
        return LLMResponse(
            text=text,
            input_tokens=_estimate_tokens(json.dumps(messages)),
            output_tokens=_estimate_tokens(text),
            model="mock",
        )
