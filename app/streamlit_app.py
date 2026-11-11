"""Streamlit UI for the Text-to-SQL agent.

Run (from the project root):
    streamlit run app/streamlit_app.py

The UI calls the same pipeline the API uses, in-process. Set your LLM
variables in .env (or the environment) first -- see .env.example.
Without LLM_API_KEY it runs against the built-in mock LLM and says so.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from text_to_sql_agent.config import Settings
from text_to_sql_agent.db import Database
from text_to_sql_agent.llm import MockLLM, OpenAICompatibleClient
from text_to_sql_agent.pipeline import run_pipeline

SAMPLE_QUESTIONS = [
    "How many orders are there in total?",
    "What is our total revenue?",
    "What is the total revenue by product category?",
    "How many late deliveries were there?",
    "List the top 5 states by number of customers.",
    "What is our profit?",
]


def build_llm(settings: Settings):
    if settings.llm_api_key:
        return OpenAICompatibleClient(settings)
    return MockLLM(script=[
        ("How many orders are there in total?",
         ['{"sql": "SELECT COUNT(*) FROM orders"}']),
        ("What is our total revenue?",
         ['{"sql": "SELECT SUM(price) FROM order_items"}']),
        ("What is the total revenue by product category?",
         ['{"sql": "SELECT p.product_category_name, SUM(oi.price) AS revenue '
          'FROM order_items oi JOIN products p ON oi.product_id = p.product_id '
          'GROUP BY p.product_category_name ORDER BY revenue DESC"}']),
        ("How many late deliveries were there?",
         ['{"sql": "SELECT COUNT(*) FROM orders WHERE '
          'order_delivered_customer_date > order_estimated_delivery_date"}']),
        ("List the top 5 states by number of customers.",
         ['{"sql": "SELECT customer_state, COUNT(*) AS n FROM customers '
          'GROUP BY customer_state ORDER BY n DESC, customer_state ASC LIMIT 5"}']),
        ("What is our profit?",
         ['{"ambiguous": true, "clarification": "Profit is not defined in this '
          'database -- there is no cost data. Did you mean revenue?"}']),
    ])


def main() -> None:
    st.set_page_config(page_title="Text-to-SQL Analyst Agent", layout="wide")
    st.title("Text-to-SQL AI Data Analyst Agent")
    st.caption("Ask a plain-English question about the Olist e-commerce data.")

    settings = Settings()
    db = Database(settings.db_url)

    if not settings.llm_api_key:
        st.warning(
            "No LLM_API_KEY configured -- running with the built-in mock LLM. "
            "Set LLM_API_KEY in .env for real answers (see .env.example)."
        )

    with st.sidebar:
        st.header("Database")
        schema = db.get_schema()
        for name in schema.table_names():
            with st.expander(name):
                st.write(", ".join(schema.tables[name].column_names()))
        st.header("Try one")
        for q in SAMPLE_QUESTIONS:
            if st.button(q, key=f"sample-{q[:20]}"):
                st.session_state["question"] = q

    question = st.text_input(
        "Your question",
        value=st.session_state.get("question", ""),
        placeholder="e.g. What is the total revenue by product category?",
    )
    if st.button("Ask", type="primary") and question.strip():
        with st.spinner("Thinking (schema -> SQL -> validate -> execute)..."):
            result = run_pipeline(question.strip(), build_llm(settings), db, settings)
        st.session_state["last_result"] = result.to_dict()

    result = st.session_state.get("last_result")
    if not result:
        st.stop()

    if result["status"] == "clarification":
        st.info(f"**I need a clarification:** {result['clarification']}")
    elif result["status"] == "failed":
        st.error(f"**I could not answer that.** {result['error']}")
    else:
        st.subheader("Answer")
        st.write(result["insight"])
        if result["columns"] and result["rows"]:
            df = pd.DataFrame(result["rows"], columns=result["columns"])
            st.dataframe(df, use_container_width=True)
            chart = result.get("chart") or {}
            if chart.get("chart_type") == "bar" and chart.get("data"):
                st.bar_chart(pd.DataFrame(chart["data"]).set_index("x"))
            elif chart.get("chart_type") == "line" and chart.get("data"):
                st.line_chart(pd.DataFrame(chart["data"]).set_index("x"))

    with st.expander("SQL used"):
        st.code(result.get("sql") or "(none)", language="sql")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Attempts", result["attempts"])
    col2.metric("Latency", f"{result['latency_seconds']:.2f}s")
    col3.metric("Tokens", result["tokens_used"])
    col4.metric("Cost", f"${result['cost_usd']:.6f}")
    with st.expander("Step log (every decision the agent made)"):
        for step in result["step_log"]:
            st.write(f"`{step['ts']}` **{step['step']}** -- {step['detail']}")


if __name__ == "__main__":
    main()
