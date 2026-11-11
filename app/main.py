"""FastAPI backend for the Text-to-SQL agent.

Run:
    uvicorn app.main:app --reload --port 8000

Endpoints:
    GET  /health   -- liveness + which DB/model are configured
    GET  /schema   -- tables and columns the agent can query
    POST /ask      -- {"question": "..."} -> full PipelineResult JSON
    GET  /metrics  -- session totals (questions, tokens, cost, failures)

The LLM is configured ONLY via environment variables (see .env.example).
No key is ever read from the request body.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from text_to_sql_agent.config import Settings
from text_to_sql_agent.db import Database
from text_to_sql_agent.llm import MockLLM, OpenAICompatibleClient
from text_to_sql_agent.pipeline import run_pipeline

settings = Settings()
_started_at = time.time()
_stats = {"questions": 0, "ok": 0, "clarification": 0, "failed": 0,
          "tokens": 0, "cost_usd": 0.0}


def _build_llm():
    """Real client when a key is configured, mock otherwise (clearly labelled)."""
    if settings.llm_api_key:
        return OpenAICompatibleClient(settings)
    return MockLLM(script=[
        ("How many orders", ['{"sql": "SELECT COUNT(*) FROM orders"}']),
    ])


def _get_db() -> Database:
    return Database(settings.db_url)


app = FastAPI(
    title="Text-to-SQL AI Data Analyst Agent",
    description="Plain-English questions -> validated SQL -> charts + insights.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _started_at, 1),
        "db_url": settings.db_url.split("@")[-1],  # never leak credentials
        "llm_model": settings.llm_model,
        "llm_configured": bool(settings.llm_api_key),
        "mock_llm": not bool(settings.llm_api_key),
    }


@app.get("/schema")
def schema():
    db = _get_db()
    schema = db.get_schema()
    return {
        "tables": {
            name: [c.name for c in table.columns]
            for name, table in schema.tables.items()
        }
    }


@app.post("/ask")
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty.")
    try:
        result = run_pipeline(req.question.strip(), _build_llm(), _get_db(), settings)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    _stats["questions"] += 1
    _stats[result.status] = _stats.get(result.status, 0) + 1
    _stats["tokens"] += result.tokens_used
    _stats["cost_usd"] += result.cost_usd
    return result.to_dict()


@app.get("/metrics")
def metrics():
    return dict(_stats, llm_model=settings.llm_model)
