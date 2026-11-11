"""Shared fixtures: a fresh sample DB and a mock LLM per test."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from text_to_sql_agent.config import Settings
from text_to_sql_agent.db import Database
from text_to_sql_agent.llm import MockLLM


@pytest.fixture()
def sample_db_path(tmp_path):
    """Build the deterministic sample DB in a temp dir."""
    import make_sample_db

    path = tmp_path / "sample.db"
    make_sample_db.build_sample_db(str(path))
    return path


@pytest.fixture()
def db(sample_db_path):
    return Database(f"sqlite:///{sample_db_path}")


@pytest.fixture()
def settings(sample_db_path):
    s = Settings()
    s.db_url = f"sqlite:///{sample_db_path}"
    s.log_file = ""  # no disk logging in tests
    s.max_retries = 3
    return s


@pytest.fixture()
def mock_llm():
    """Mock that answers the common smoke-test questions with gold SQL."""
    return MockLLM(script=[
        ("How many orders are there in total?",
         ['{"sql": "SELECT COUNT(*) FROM orders"}']),
        ("What is our total revenue?",
         ['{"sql": "SELECT SUM(price) FROM order_items"}']),
        ("Show me the best sellers.",
         ['{"ambiguous": true, "clarification": "Best by revenue or by number of orders?"}']),
    ])
