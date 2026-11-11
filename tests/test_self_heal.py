"""Self-heal loop tests: a bad first SQL must be fixed, not fatal."""

from __future__ import annotations

from text_to_sql_agent.llm import MockLLM
from text_to_sql_agent.pipeline import run_pipeline


def test_self_heal_fixes_hallucinated_column(db, settings):
    mock = MockLLM(script=[
        ("What is our total revenue?", [
            '{"sql": "SELECT SUM(profit) FROM order_items"}',
            '{"sql": "SELECT SUM(price) FROM order_items"}',
        ]),
    ])
    result = run_pipeline("What is our total revenue?", mock, db, settings)
    assert result.status == "ok", result.error
    assert result.attempts == 2
    assert result.rows, "expected rows after the heal"
    assert any(s["step"] == "self_heal" for s in result.step_log)


def test_self_heal_gives_up_after_max_retries(db, settings):
    mock = MockLLM(script=[
        ("What is our total revenue?", [
            '{"sql": "SELECT SUM(profit) FROM order_items"}',
        ]),
    ])
    settings.max_retries = 2
    result = run_pipeline("What is our total revenue?", mock, db, settings)
    assert result.status == "failed"
    # Initial attempt + 2 retries = 3 attempts.
    assert result.attempts == 3
    assert result.error


def test_ambiguous_profit_clarifies_without_llm_call(db, settings):
    mock = MockLLM(script=[])
    result = run_pipeline("What is our profit?", mock, db, settings)
    assert result.status == "clarification"
    assert result.clarification
    assert result.attempts == 0
    assert mock.calls == [], "no LLM call should be spent on an unanswerable term"


def test_llm_ambiguity_path_clarifies(db, settings, mock_llm):
    result = run_pipeline("Show me the best sellers.", mock_llm, db, settings)
    assert result.status == "clarification"
    assert "revenue" in (result.clarification or "").lower()
