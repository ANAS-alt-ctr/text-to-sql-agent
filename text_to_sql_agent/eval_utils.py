"""Eval helpers: golden-set loading, result comparison, metrics.

Metrics are ALWAYS reported per difficulty category, never as one
blended number -- a single blended accuracy hides exactly the failures
a recruiter or interviewer will ask about.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field


@dataclass
class GoldenItem:
    id: str
    question: str
    difficulty: str  # single-table | join | aggregation | ambiguous
    gold_sql: str | None = None
    expected_rows: list | None = None
    expects: str = "result"  # "result" | "clarification"


def load_golden_set(path: str) -> list[GoldenItem]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    items = []
    for entry in raw["questions"]:
        items.append(
            GoldenItem(
                id=entry["id"],
                question=entry["question"],
                difficulty=entry["difficulty"],
                gold_sql=entry.get("gold_sql"),
                expected_rows=entry.get("expected_rows"),
                expects=entry.get("expects", "result"),
            )
        )
    return items


def _normalise(value):
    """Make values comparable: floats with tolerance, None stays None."""
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return round(value, 4)
    return value


def rows_match(got: list[tuple], expected: list | None) -> bool:
    """Compare result rows as multisets (order-insensitive), with float
    tolerance. ``expected`` is a list of lists from JSON."""
    if expected is None:
        return False
    got_norm = sorted(tuple(_normalise(v) for v in row) for row in got)
    exp_norm = sorted(tuple(_normalise(v) for v in row) for row in expected)
    return got_norm == exp_norm


@dataclass
class CategoryMetrics:
    n: int = 0
    correct: int = 0
    valid_sql: int = 0
    valid_sql_n: int = 0  # items where a SQL-validity verdict applies
    self_healed: int = 0
    first_attempt_failures: int = 0
    hallucinated_columns: int = 0
    latencies: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)

    @property
    def accuracy(self) -> float | None:
        return (self.correct / self.n) if self.n else None

    @property
    def valid_sql_rate(self) -> float | None:
        return (self.valid_sql / self.valid_sql_n) if self.valid_sql_n else None

    @property
    def self_heal_rate(self) -> float | None:
        if not self.first_attempt_failures:
            return None
        return self.self_healed / self.first_attempt_failures

    @property
    def hallucination_rate(self) -> float | None:
        return (self.hallucinated_columns / self.n) if self.n else None

    @property
    def median_latency(self) -> float | None:
        if not self.latencies:
            return None
        ordered = sorted(self.latencies)
        mid = len(ordered) // 2
        return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    @property
    def p95_latency(self) -> float | None:
        if not self.latencies:
            return None
        ordered = sorted(self.latencies)
        idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[idx]

    @property
    def cost_per_query(self) -> float | None:
        if not self.costs:
            return None
        return sum(self.costs) / len(self.costs)


def compute_metrics(per_item: list[dict]) -> dict[str, CategoryMetrics]:
    """Aggregate per-item eval records into per-category metrics.

    Each record: {difficulty, correct, valid_sql, self_healed,
    first_attempt_failure, hallucinated_columns, latency, cost}.
    """
    out: dict[str, CategoryMetrics] = {}
    for record in per_item:
        cat = out.setdefault(record["difficulty"], CategoryMetrics())
        cat.n += 1
        cat.correct += int(bool(record.get("correct")))
        if record.get("valid_sql") is not None:
            cat.valid_sql_n += 1
            cat.valid_sql += int(bool(record.get("valid_sql")))
        cat.self_healed += int(bool(record.get("self_healed")))
        cat.first_attempt_failures += int(bool(record.get("first_attempt_failure")))
        cat.hallucinated_columns += int(record.get("hallucinated_columns", 0))
        cat.latencies.append(float(record.get("latency", 0.0)))
        cat.costs.append(float(record.get("cost", 0.0)))
    return out


def format_metrics_table(metrics: dict[str, CategoryMetrics]) -> str:
    """Plain-text per-category metrics table (no blended headline)."""
    header = (
        f"{'category':<14}{'n':>4}{'accuracy':>10}{'valid_sql':>11}"
        f"{'self_heal':>11}{'halluc.':>9}{'med_lat':>9}{'p95_lat':>9}{'$/query':>9}"
    )
    lines = [header, "-" * len(header)]
    for category in sorted(metrics):
        m = metrics[category]
        def pct(v):
            return f"{v * 100:.1f}%" if v is not None else "  --  "
        lines.append(
            f"{category:<14}{m.n:>4}{pct(m.accuracy):>10}{pct(m.valid_sql_rate):>11}"
            f"{pct(m.self_heal_rate):>11}{pct(m.hallucination_rate):>9}"
            f"{(m.median_latency or 0):>8.2f}s{(m.p95_latency or 0):>8.2f}s"
            f" ${(m.cost_per_query or 0):>7.4f}"
        )
    return "\n".join(lines)
