"""The agents.

Four small agents, each with one job:

* :class:`SchemaAgent` -- describes the database for the prompt.
* :class:`SQLAgent` -- turns a question into SQL (or a clarification
  request when a term cannot be grounded). A deterministic pre-check
  catches business terms the dataset cannot answer at all (profit,
  margin, churn, ...) before any LLM call is spent.
* :class:`ChartAgent` -- deterministic chart spec from result columns
  and rows (bar / line / table). No LLM needed: rules beat guessing.
* :class:`InsightAgent` -- plain-English findings + caveats from the
  results, via the LLM, with a template fallback if the LLM fails.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .db import Schema
from .glossary import GLOSSARY, SYNONYMS, format_glossary_for_prompt
from .llm import LLMClient

# Business terms this dataset cannot answer: there is no cost data
# (so no profit/margin), no subscription data (so no churn/retention).
# If a question uses one, the honest answer is a clarification, never
# an invented definition. Anything subtler ("best sellers", "how are we
# doing") is left to the LLM, which the prompt instructs to clarify.
UNDEFINED_BUSINESS_TERMS = {
    "profit",
    "margin",
    "margins",
    "churn",
    "retention",
    "roi",
    "cac",
    "ltv",
    "lifetime value",
    "runway",
    "burn rate",
}


def detect_ambiguity(question: str, schema: Schema) -> tuple[bool, list[str]]:
    """Deterministic ambiguity pre-check.

    Returns (is_ambiguous, ungrounded_terms). Only fires for business
    terms the schema/glossary provably cannot define; everything else
    goes to the LLM, whose prompt also tells it to clarify rather than
    guess. Values (city names, categories, state codes) are never
    flagged -- only concepts.
    """
    lowered = question.lower()
    found = [t for t in UNDEFINED_BUSINESS_TERMS if t in lowered]
    if found:
        return True, sorted(set(found))
    return False, []


@dataclass
class SQLAnswer:
    sql: str | None = None
    ambiguous: bool = False
    clarification: str | None = None


SQL_SYSTEM_PROMPT = """You are a careful data analyst that writes SQL for a \
relational database. You NEVER guess. You only use tables and columns \
listed in the schema below, and only the business definitions in the \
glossary. Return ONLY a JSON object, no other text:

- If you can write the query: {"sql": "<single SELECT statement>"}
- If a term in the question cannot be grounded in the schema or glossary, \
or the question is too vague to answer with one query: \
{"ambiguous": true, "clarification": "<one short question asking what they mean>"}

Rules:
- SELECT only. Never DELETE, UPDATE, INSERT, DROP or any write.
- Use only tables/columns from the schema. Never invent a column.
- Prefer explicit columns over SELECT *.
- If the question asks for "top N" without a number, use 10.
- Dates are stored as ISO text (YYYY-MM-DD HH:MM:SS). Use strftime / \
date functions as appropriate for SQLite.
"""


class SchemaAgent:
    """Describes the database for the SQL agent's prompt."""

    @staticmethod
    def describe(schema: Schema) -> str:
        return schema.format_for_prompt()


class SQLAgent:
    """Generates SQL (or a clarification) from a plain-English question."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def generate(
        self,
        question: str,
        schema: Schema,
        retry_context: str | None = None,
    ) -> SQLAnswer:
        user_prompt = (
            f"{format_glossary_for_prompt()}\n\n"
            f"DATABASE SCHEMA:\n{schema.format_for_prompt()}\n\n"
            f"QUESTION: {question}\n"
        )
        if retry_context:
            user_prompt += (
                "\nYour previous SQL failed. Here is what went wrong -- "
                "fix it, do not repeat the same mistake:\n" + retry_context
            )
        response = self._llm.complete(
            [
                {"role": "system", "content": SQL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        return parse_sql_answer(response.text)

    @staticmethod
    def build_messages(question: str, schema: Schema, retry_context: str | None = None):
        """Exposed for tests: the exact prompt the agent sends."""
        user_prompt = (
            f"{format_glossary_for_prompt()}\n\n"
            f"DATABASE SCHEMA:\n{schema.format_for_prompt()}\n\n"
            f"QUESTION: {question}\n"
        )
        if retry_context:
            user_prompt += "\nPrevious attempt failed:\n" + retry_context
        return [
            {"role": "system", "content": SQL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]


def parse_sql_answer(text: str) -> SQLAnswer:
    """Parse the LLM's JSON answer, tolerating markdown fences."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: the model returned bare SQL instead of JSON.
        if cleaned.upper().lstrip().startswith(("SELECT", "WITH")):
            return SQLAnswer(sql=cleaned)
        return SQLAnswer(
            ambiguous=True,
            clarification="I could not parse the generated SQL. Please rephrase the question.",
        )
    if isinstance(data, dict) and data.get("ambiguous"):
        return SQLAnswer(
            ambiguous=True,
            clarification=str(data.get("clarification", "Could you clarify what you mean?")),
        )
    sql = data.get("sql") if isinstance(data, dict) else None
    if not sql:
        return SQLAnswer(
            ambiguous=True,
            clarification="I could not turn that question into a query. Could you rephrase it?",
        )
    return SQLAnswer(sql=str(sql).strip())


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _looks_like_date(value) -> bool:
    if not isinstance(value, str):
        return False
    # YYYY-MM-DD or YYYY-MM (e.g. strftime('%Y-%m') month buckets).
    return bool(re.match(r"^\d{4}-\d{2}(-\d{2})?", value))


@dataclass
class ChartSpec:
    chart_type: str  # "bar" | "line" | "table"
    x: str | None = None
    y: str | None = None
    title: str = ""
    data: list[dict] = None

    def to_dict(self) -> dict:
        return {
            "chart_type": self.chart_type,
            "x": self.x,
            "y": self.y,
            "title": self.title,
            "data": self.data or [],
        }


class ChartAgent:
    """Deterministic chart spec from result columns and rows.

    Rules: date-like x + numeric y -> line; categorical x + numeric y
    (<= 20 categories) -> bar; anything else -> table. No LLM involved.
    """

    @staticmethod
    def spec(columns: list[str], rows: list[tuple], question: str = "") -> ChartSpec:
        title = (question or "Query result").strip()
        if len(columns) < 2 or not rows:
            return ChartSpec(chart_type="table", title=title, data=[])
        x_col, y_col = columns[0], columns[1]
        x_values = [r[0] for r in rows]
        y_values = [r[1] for r in rows]
        if not all(_is_number(v) for v in y_values):
            return ChartSpec(chart_type="table", title=title, data=[])
        data = [
            {"x": x, "y": y}
            for x, y in list(zip(x_values, y_values))[:50]
        ]
        if all(_looks_like_date(v) for v in x_values if v is not None):
            return ChartSpec(chart_type="line", x=x_col, y=y_col, title=title, data=data)
        if all(isinstance(v, str) for v in x_values if v is not None) and len(rows) <= 20:
            return ChartSpec(chart_type="bar", x=x_col, y=y_col, title=title, data=data)
        if len(rows) == 1:
            return ChartSpec(chart_type="table", title=title, data=[])
        return ChartSpec(chart_type="bar", x=x_col, y=y_col, title=title, data=data)


class InsightAgent:
    """Plain-English findings + caveats, via the LLM.

    Falls back to a template summary if the LLM call fails, so the
    pipeline still returns something useful.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def summarize(
        self,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[tuple],
        row_count: int,
    ) -> str:
        preview = "\n".join(
            str(dict(zip(columns, r))) for r in rows[:10]
        )
        prompt = (
            "You are a data analyst. Summarise the result of this query in "
            "plain English for a non-technical reader: state the headline "
            "finding with the actual numbers, then one or two caveats "
            "(what this does NOT prove, any limitation of the data). "
            "Keep it under 120 words. No SQL, no jargon.\n\n"
            f"Question: {question}\nSQL: {sql}\n"
            f"Columns: {', '.join(columns)}\n"
            f"Rows returned (capped at 100): {row_count}\n"
            f"First rows:\n{preview}"
        )
        try:
            response = self._llm.complete(
                [{"role": "user", "content": prompt}]
            )
            text = response.text.strip()
            return text if text else self._fallback(question, row_count)
        except Exception:
            return self._fallback(question, row_count)

    @staticmethod
    def _fallback(question: str, row_count: int) -> str:
        return (
            f"The query returned {row_count} row(s). "
            "The numbers are in the table and chart above; interpret them "
            "with care -- this is a descriptive result, not a causal claim."
        )
