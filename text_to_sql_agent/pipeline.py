"""Pipeline orchestration.

The agent is a small state graph:

    prepare (schema + ambiguity pre-check)
        -> generate SQL
        -> validate (allow-list, schema grounding, EXPLAIN)
        -> execute (timeout + row cap)
        -> chart (deterministic spec)
        -> insight (plain-English findings)

On any validation or execution failure the graph routes to ``self_heal``,
which re-prompts the SQL agent with the error, up to ``max_retries``
retries (initial attempt + 3 retries = 4 attempts max). Every step is
logged with a timestamp (see :mod:`text_to_sql_agent.logging`).

The graph is built with LangGraph when it is installed. If LangGraph is
not available, :func:`run_pipeline` falls back to an equivalent
sequential runner that calls the same node functions in the same order
-- the behaviour and the log are identical either way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .agents import ChartAgent, InsightAgent, SQLAgent, detect_ambiguity
from .config import Settings
from .db import Database
from .llm import LLMClient, LLMResponse
from .logging import StepLog
from .validator import validate_sql

try:  # LangGraph is optional at runtime; the sequential runner covers it.
    from langgraph.graph import END, StateGraph
    from typing_extensions import TypedDict

    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - import guard
    _HAS_LANGGRAPH = False


@dataclass
class PipelineResult:
    """Everything one question produces, for the API / UI / eval."""

    status: str  # "ok" | "clarification" | "failed"
    question: str
    sql: str | None = None
    attempts: int = 0
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    chart: dict | None = None
    insight: str = ""
    clarification: str | None = None
    error: str | None = None
    latency_seconds: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0
    step_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "question": self.question,
            "sql": self.sql,
            "attempts": self.attempts,
            "columns": self.columns,
            "rows": [list(r) for r in self.rows],
            "chart": self.chart,
            "insight": self.insight,
            "clarification": self.clarification,
            "error": self.error,
            "latency_seconds": round(self.latency_seconds, 3),
            "tokens_used": self.tokens_used,
            "cost_usd": round(self.cost_usd, 6),
            "step_log": self.step_log,
        }


class _BudgetExceeded(RuntimeError):
    pass


class _BudgetedClient:
    """Wraps any LLM client with a hard session token budget.

    The wrapped (user-supplied) client is never mutated: this object is
    what the agents call, and it tracks tokens/cost itself.
    """

    def __init__(self, inner: LLMClient, settings: Settings) -> None:
        self._inner = inner
        self._settings = settings
        self.tokens_used = 0
        self.cost_usd = 0.0

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        if self.tokens_used >= self._settings.llm_session_token_budget:
            raise _BudgetExceeded(
                f"Session token budget ({self._settings.llm_session_token_budget}) "
                "reached; refusing further LLM calls."
            )
        response = self._inner.complete(messages)
        self.tokens_used += response.total_tokens
        self.cost_usd += response.cost_usd(
            self._settings.llm_price_input_per_1k,
            self._settings.llm_price_output_per_1k,
        )
        return response


class Pipeline:
    """Holds the collaborators and the node functions for one run."""

    def __init__(
        self,
        llm: LLMClient,
        db: Database,
        settings: Settings,
        log: StepLog | None = None,
    ) -> None:
        self.llm = _BudgetedClient(llm, settings)
        self.db = db
        self.settings = settings
        self.log = log or StepLog(settings.log_file)
        self.sql_agent = SQLAgent(self.llm)
        self.insight_agent = InsightAgent(self.llm)

    @property
    def tokens_used(self) -> int:
        return self.llm.tokens_used

    @property
    def cost_usd(self) -> float:
        return self.llm.cost_usd

    # -- nodes ----------------------------------------------------------
    def node_prepare(self, state: dict) -> dict:
        schema = self.db.get_schema()
        state["schema"] = schema
        self.log.record("prepare", f"Loaded schema: {len(schema.tables)} tables.")
        ambiguous, terms = detect_ambiguity(state["question"], schema)
        if ambiguous:
            state["status"] = "clarification"
            state["clarification"] = (
                "I can't answer that from this database as asked: the term(s) "
                + ", ".join(f"'{t}'" for t in terms)
                + " have no definition here (there is no cost data, so no "
                "profit/margin, and no subscription data, so no churn). "
                "Could you rephrase using revenue, orders, deliveries or "
                "review scores?"
            )
            self.log.record("clarify", state["clarification"])
        return state

    def node_generate(self, state: dict) -> dict:
        state["attempts"] += 1
        answer = self.sql_agent.generate(
            state["question"], state["schema"], retry_context=state.get("retry_context")
        )
        if answer.ambiguous:
            state["status"] = "clarification"
            state["clarification"] = answer.clarification
            self.log.record("clarify", answer.clarification or "")
        else:
            state["sql"] = answer.sql
            self.log.record("generate_sql", f"Attempt {state['attempts']}: {answer.sql}")
        return state

    def node_validate(self, state: dict) -> dict:
        result = validate_sql(state["sql"], state["schema"], db=self.db)
        state["validation"] = result
        if result.ok:
            self.log.record("validate", "SQL passed validation." + (
                f" Warnings: {'; '.join(result.warnings)}" if result.warnings else ""
            ))
            state["sql"] = result.normalized_sql or state["sql"]
        else:
            self.log.record("validate_failed", "; ".join(result.errors))
            state["last_error"] = "; ".join(result.errors)
        return state

    def node_self_heal(self, state: dict) -> dict:
        state["retry_context"] = (
            f"Failed SQL:\n{state.get('sql')}\nError:\n{state.get('last_error')}"
        )
        self.log.record(
            "self_heal",
            f"Retry {state['attempts'] + 1} of {self.settings.max_retries} after failure.",
        )
        return state

    def node_execute(self, state: dict) -> dict:
        try:
            columns, rows = self.db.execute(
                state["sql"],
                timeout_seconds=self.settings.query_timeout_seconds,
                row_cap=self.settings.row_cap,
            )
        except Exception as exc:
            state["last_error"] = f"Execution failed: {exc}"
            self.log.record("execute_failed", str(exc))
            return state
        state["columns"] = columns
        state["rows"] = rows
        state["executed"] = True
        self.log.record("execute", f"Returned {len(rows)} row(s), {len(columns)} column(s).")
        return state

    def node_chart(self, state: dict) -> dict:
        spec = ChartAgent.spec(state["columns"], state["rows"], state["question"])
        state["chart"] = spec.to_dict()
        self.log.record("chart", f"Chart type: {spec.chart_type}.")
        return state

    def node_insight(self, state: dict) -> dict:
        try:
            insight = self.insight_agent.summarize(
                state["question"],
                state["sql"],
                state["columns"],
                state["rows"],
                len(state["rows"]),
            )
        except Exception as exc:  # pragma: no cover - defensive
            insight = InsightAgent._fallback(state["question"], len(state["rows"]))
            self.log.record("insight_fallback", str(exc))
        state["insight"] = insight
        state["status"] = "ok"
        self.log.record("insight", "Insight written.")
        return state

    # -- routing helpers -------------------------------------------------
    def _validation_ok(self, state: dict) -> bool:
        return bool(state.get("validation") and state["validation"].ok)

    def _can_retry(self, state: dict) -> bool:
        # attempts counts generates; retries = attempts - 1 so far.
        return (state["attempts"] - 1) < self.settings.max_retries

    # -- sequential runner (fallback when LangGraph is absent) ----------
    def run_sequential(self, question: str) -> PipelineResult:
        started = time.monotonic()
        state: dict = {
            "question": question,
            "attempts": 0,
            "status": None,
            "sql": None,
            "retry_context": None,
            "last_error": None,
            "columns": [],
            "rows": [],
            "chart": None,
            "insight": "",
            "clarification": None,
        }
        # Patch the SQL/Insight agents to route through the budget wrapper.
        state = self.node_prepare(state)
        if state["status"] != "clarification":
            while True:
                state = self.node_generate(state)
                if state["status"] == "clarification":
                    break
                state = self.node_validate(state)
                if self._validation_ok(state):
                    state = self.node_execute(state)
                    if state.get("executed"):
                        state = self.node_chart(state)
                        state = self.node_insight(state)
                        break
                    # execution failed: retry if allowed
                    if self._can_retry(state):
                        state = self.node_self_heal(state)
                        continue
                    state["status"] = "failed"
                    state["error"] = state.get("last_error")
                    break
                if self._can_retry(state):
                    state = self.node_self_heal(state)
                    continue
                state["status"] = "failed"
                state["error"] = state.get("last_error")
                break
        return self._finish(state, started)

    def _finish(self, state: dict, started: float) -> PipelineResult:
        status = state.get("status") or "failed"
        return PipelineResult(
            status=status,
            question=state["question"],
            sql=state.get("sql"),
            attempts=state["attempts"],
            columns=state.get("columns", []),
            rows=state.get("rows", []),
            chart=state.get("chart"),
            insight=state.get("insight", ""),
            clarification=state.get("clarification"),
            error=state.get("error"),
            latency_seconds=time.monotonic() - started,
            tokens_used=self.tokens_used,
            cost_usd=self.cost_usd,
            step_log=self.log.as_dicts(),
        )

    # -- LangGraph wiring ------------------------------------------------
    def build_graph(self):
        """Build the LangGraph StateGraph over the same node functions."""
        if not _HAS_LANGGRAPH:  # pragma: no cover
            raise RuntimeError("LangGraph is not installed.")

        class AgentState(TypedDict, total=False):
            question: str
            schema: Any
            sql: str | None
            attempts: int
            retry_context: str | None
            last_error: str | None
            validation: Any
            executed: bool
            columns: list
            rows: list
            chart: dict | None
            insight: str
            status: str | None
            clarification: str | None
            error: str | None

        graph = StateGraph(AgentState)
        graph.add_node("prepare", self.node_prepare)
        graph.add_node("generate", self.node_generate)
        graph.add_node("validate", self.node_validate)
        graph.add_node("self_heal", self.node_self_heal)
        graph.add_node("execute", self.node_execute)
        graph.add_node("chart", self.node_chart)
        graph.add_node("insight", self.node_insight)
        graph.set_entry_point("prepare")

        def after_prepare(state):
            return END if state.get("status") == "clarification" else "generate"

        def after_generate(state):
            return END if state.get("status") == "clarification" else "validate"

        def after_validate(state):
            if self._validation_ok(state):
                return "execute"
            return "self_heal" if self._can_retry(state) else END

        def after_execute(state):
            if state.get("executed"):
                return "chart"
            return "self_heal" if self._can_retry(state) else END

        graph.add_conditional_edges("prepare", after_prepare)
        graph.add_conditional_edges("generate", after_generate)
        graph.add_conditional_edges("validate", after_validate)
        graph.add_conditional_edges("execute", after_execute)
        graph.add_edge("self_heal", "generate")
        graph.add_edge("chart", "insight")
        graph.add_edge("insight", END)
        return graph.compile()

    def run_graph(self, question: str) -> PipelineResult:
        started = time.monotonic()
        app = self.build_graph()
        initial: dict = {
            "question": question,
            "attempts": 0,
            "retry_context": None,
            "last_error": None,
            "validation": None,
            "executed": False,
            "columns": [],
            "rows": [],
            "chart": None,
            "insight": "",
            "status": None,
            "clarification": None,
            "error": None,
        }
        final = app.invoke(initial)
        if final.get("status") is None:
            # Validation/execution exhausted retries without setting status.
            final["status"] = "failed"
            final["error"] = final.get("last_error") or "Pipeline ended without a result."
        return self._finish(final, started)


def run_pipeline(
    question: str,
    llm: LLMClient,
    db: Database,
    settings: Settings,
    log: StepLog | None = None,
) -> PipelineResult:
    """Run one question through the agent.

    Uses the LangGraph state graph when LangGraph is installed, otherwise
    the equivalent sequential runner.
    """
    pipeline = Pipeline(llm, db, settings, log=log)
    if _HAS_LANGGRAPH:
        try:
            return pipeline.run_graph(question)
        except Exception as exc:  # pragma: no cover - defensive fallback
            pipeline.log.record("graph_fallback", f"LangGraph run failed ({exc}); using sequential runner.")
            return Pipeline(llm, db, settings, log=pipeline.log).run_sequential(question)
    return pipeline.run_sequential(question)
