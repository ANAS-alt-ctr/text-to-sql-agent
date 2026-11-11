"""Configuration -- everything comes from environment variables.

No API key, token or secret is ever stored in this file or anywhere in the
codebase. Copy ``.env.example`` to ``.env`` and fill in your own values,
or export the variables in your shell.

The LLM is OpenAI-compatible: point LLM_BASE_URL at OpenAI
(https://api.openai.com/v1) or at any compatible gateway (e.g. a local
server, Azure OpenAI, or another provider that speaks the same API).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # loads .env from the project root if present; harmless if absent


def _getenv(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _getfloat(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _getint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """All runtime settings. Read once at startup; tests can override."""

    # --- LLM (OpenAI-compatible chat completions) ---
    llm_base_url: str = field(
        default_factory=lambda: _getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    llm_api_key: str = field(default_factory=lambda: _getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _getenv("LLM_MODEL", "gpt-4o-mini"))
    llm_timeout_seconds: float = field(
        default_factory=lambda: _getfloat("LLM_TIMEOUT_SECONDS", 60.0)
    )
    llm_max_tokens: int = field(default_factory=lambda: _getint("LLM_MAX_TOKENS", 1024))
    # Hard ceiling on tokens spent in one user session (input + output).
    # The pipeline refuses new LLM calls once this is reached.
    llm_session_token_budget: int = field(
        default_factory=lambda: _getint("LLM_SESSION_TOKEN_BUDGET", 100_000)
    )
    # Prices in USD per 1,000 tokens, used only for cost reporting.
    llm_price_input_per_1k: float = field(
        default_factory=lambda: _getfloat("LLM_PRICE_INPUT_PER_1K", 0.00015)
    )
    llm_price_output_per_1k: float = field(
        default_factory=lambda: _getfloat("LLM_PRICE_OUTPUT_PER_1K", 0.0006)
    )

    # --- Database ---
    # SQLite by default (zero setup). For Postgres use e.g.
    # postgresql+psycopg2://user:password@localhost:5432/olist
    db_url: str = field(
        default_factory=lambda: _getenv("DB_URL", "sqlite:///./data/sample.db")
    )

    # --- Safety ---
    query_timeout_seconds: float = field(
        default_factory=lambda: _getfloat("QUERY_TIMEOUT_SECONDS", 30.0)
    )
    row_cap: int = field(default_factory=lambda: _getint("ROW_CAP", 100))
    max_retries: int = field(default_factory=lambda: _getint("MAX_RETRIES", 3))

    # --- Logging ---
    log_file: str = field(default_factory=lambda: _getenv("LOG_FILE", "logs/steps.jsonl"))

    def require_llm_key(self) -> None:
        """Raise a clear error if no key is configured (only when a real
        LLM call is about to be made -- importing/using the mock never
        needs a key)."""
        if not self.llm_api_key:
            raise RuntimeError(
                "No LLM API key configured. Set LLM_API_KEY in your environment "
                "or in a .env file (see .env.example). The mock LLM used in "
                "tests needs no key."
            )
