"""Run the golden-set eval and print per-category metrics.

Usage:
    # Mock LLM (no API key) on the sample DB -- what CI runs:
    python scripts/run_eval.py --llm mock --db data/sample.db

    # Real model (needs LLM_API_KEY) on the full Olist DB:
    python scripts/run_eval.py --llm openai --db data/olist.db --out eval/results.json

    # Refresh expected_rows in the golden set from the gold SQL
    # (run after rebuilding the sample DB):
    python scripts/run_eval.py --refresh-expected --db data/sample.db

Metrics are reported PER CATEGORY (single-table / join / aggregation /
ambiguous) -- never as one blended number.

Mock behaviour (documented, deliberate): the mock returns the gold SQL
for every question, EXCEPT question a01 ("What is our total revenue?"),
where it first returns SQL with a hallucinated column (``profit``) and
only then the gold SQL. That exercises the self-heal loop so the eval
reports a real self-heal rate even with the mock. With a real LLM the
self-heal rate is whatever the model actually does.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_to_sql_agent.config import Settings
from text_to_sql_agent.db import Database
from text_to_sql_agent.eval_utils import (
    compute_metrics,
    format_metrics_table,
    load_golden_set,
    rows_match,
)
from text_to_sql_agent.llm import MockLLM, OpenAICompatibleClient
from text_to_sql_agent.pipeline import run_pipeline


def build_mock(golden_items) -> MockLLM:
    script = []
    for item in golden_items:
        if item.expects == "clarification":
            script.append((
                item.question,
                [{"ambiguous": True, "clarification": "Could you clarify what you mean?"}],
            ))
        elif item.id == "a01":
            # Scripted self-heal check: hallucinated column first, then gold.
            script.append((
                item.question,
                [
                    {"sql": "SELECT SUM(profit) FROM order_items"},
                    {"sql": item.gold_sql},
                ],
            ))
        else:
            script.append((item.question, [{"sql": item.gold_sql}]))
    return MockLLM(script=script)


def refresh_expected(golden_path: str, db_path: str) -> None:
    """Recompute expected_rows by running each gold SQL on the DB."""
    with open(golden_path, encoding="utf-8") as fh:
        data = json.load(fh)
    conn = sqlite3.connect(db_path)
    try:
        for entry in data["questions"]:
            if entry.get("gold_sql"):
                rows = conn.execute(entry["gold_sql"]).fetchall()
                entry["expected_rows"] = [list(r) for r in rows]
    finally:
        conn.close()
    with open(golden_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"Refreshed expected_rows in {golden_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Golden-set eval runner.")
    parser.add_argument("--db", default="data/sample.db",
                        help="SQLite file (or SQLAlchemy URL with --db-url).")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--llm", choices=["mock", "openai"], default="mock")
    parser.add_argument("--golden", default="eval/golden_set.json")
    parser.add_argument("--out", default="eval/results.json")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--refresh-expected", action="store_true")
    args = parser.parse_args()

    if args.refresh_expected:
        refresh_expected(args.golden, args.db)
        return

    settings = Settings()
    settings.log_file = ""  # no disk logging during eval
    db_url = args.db_url or f"sqlite:///{args.db}"
    db = Database(db_url)
    items = load_golden_set(args.golden)
    if args.max_questions:
        items = items[: args.max_questions]

    llm = build_mock(items) if args.llm == "mock" else OpenAICompatibleClient(settings)
    print(f"Eval: {len(items)} questions | llm={args.llm} | db={db_url}\n")

    records: list[dict] = []
    per_item: list[dict] = []
    for item in items:
        result = run_pipeline(item.question, llm, db, settings)
        if item.expects == "clarification":
            correct = result.status == "clarification"
            valid_sql = None  # no SQL verdict applies to clarification items
        else:
            correct = (
                result.status == "ok"
                and rows_match(result.rows, item.expected_rows)
            )
            # Final SQL passed validation iff a successful "validate" step ran.
            valid_sql = any(s["step"] == "validate" for s in result.step_log)
        hallucinated = sum(
            1
            for step in result.step_log
            if step["step"] == "validate_failed" and "Unknown column" in step["detail"]
        )
        records.append({
            "difficulty": item.difficulty,
            "correct": correct,
            "valid_sql": valid_sql,
            "self_healed": result.attempts > 1 and result.status == "ok",
            "first_attempt_failure": result.attempts > 1,
            "hallucinated_columns": hallucinated,
            "latency": result.latency_seconds,
            "cost": result.cost_usd,
        })
        per_item.append({
            "id": item.id,
            "question": item.question,
            "difficulty": item.difficulty,
            "status": result.status,
            "attempts": result.attempts,
            "correct": correct,
            "sql": result.sql,
            "latency_seconds": round(result.latency_seconds, 3),
            "cost_usd": round(result.cost_usd, 6),
        })
        mark = "OK " if correct else "MISS"
        print(f"[{mark}] {item.id} ({item.difficulty}) attempts={result.attempts} "
              f"{result.latency_seconds:.2f}s ${result.cost_usd:.6f}")

    metrics = compute_metrics(records)
    print("\n" + format_metrics_table(metrics))
    print("\nPer-category numbers only -- no blended headline (see README).")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "llm": args.llm,
                "db": args.db,
                "n": len(records),
                "per_category": {name: m.__dict__ for name, m in metrics.items()},
                "per_item": per_item,
                "records": records,
            },
            fh,
            indent=2,
        )
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
