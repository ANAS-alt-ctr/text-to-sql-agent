"""Smoke test: the full pipeline end-to-end with a mock LLM.

No API key, no network. Runs the real graph (LangGraph when installed,
otherwise the sequential runner) against the sample database.
"""

from __future__ import annotations

from text_to_sql_agent.agents import ChartAgent
from text_to_sql_agent.pipeline import run_pipeline


def test_full_pipeline_end_to_end(db, settings, mock_llm):
    result = run_pipeline("How many orders are there in total?", mock_llm, db, settings)
    assert result.status == "ok", result.error
    assert result.sql == "SELECT COUNT(*) FROM orders"
    assert result.rows == [(40,)]
    assert result.columns == ["COUNT(*)"]
    assert result.chart is not None
    assert result.chart["chart_type"] in ("bar", "line", "table")
    assert result.insight, "insight should be non-empty"
    steps = [s["step"] for s in result.step_log]
    for expected in ("prepare", "generate_sql", "validate", "execute", "chart", "insight"):
        assert expected in steps, steps
    assert result.tokens_used > 0
    assert result.latency_seconds >= 0


def test_pipeline_returns_revenue(db, settings, mock_llm):
    result = run_pipeline("What is our total revenue?", mock_llm, db, settings)
    assert result.status == "ok"
    assert len(result.rows) == 1
    revenue = result.rows[0][0]
    assert revenue is not None and revenue > 0


def test_result_serialises_to_json(db, settings, mock_llm):
    import json

    result = run_pipeline("How many orders are there in total?", mock_llm, db, settings)
    payload = result.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["status"] == "ok"
    assert payload["rows"] == [[40]]


def test_chart_agent_rules():
    # Date x + numeric y -> line.
    spec = ChartAgent.spec(
        ["month", "revenue"],
        [("2017-01", 100.0), ("2017-02", 150.0)],
        "Revenue per month",
    )
    assert spec.chart_type == "line"
    # Categorical x + numeric y -> bar.
    spec = ChartAgent.spec(
        ["state", "n"],
        [("SP", 10), ("RJ", 8)],
        "Customers by state",
    )
    assert spec.chart_type == "bar"
    # No numeric y -> table.
    spec = ChartAgent.spec(
        ["order_status"],
        [("delivered",), ("canceled",)],
        "Statuses",
    )
    assert spec.chart_type == "table"


def test_token_budget_blocks_further_calls(db, settings, mock_llm):
    settings.llm_session_token_budget = 1  # absurdly small on purpose
    result = run_pipeline("How many orders are there in total?", mock_llm, db, settings)
    # The insight call happens after execution; budget is checked per call.
    # The first (SQL) call fits, so the run completes -- but the budget
    # must be enforced: with budget 1 the second call raises and the
    # insight falls back to the template instead of crashing.
    assert result.status == "ok"
    assert result.insight
