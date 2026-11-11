"""Database layer.

One small wrapper around SQLAlchemy that works the same for SQLite
(the default, zero setup) and Postgres (set ``DB_URL``). It provides:

* schema introspection (tables, columns, types, primary/foreign keys),
* ``EXPLAIN``-before-execute so syntax errors are caught before a query
  ever runs,
* execution with a hard timeout (thread-based, so it works on Windows
  and Linux) and a row cap.

All queries the agent runs are read-only -- the validator
(:mod:`text_to_sql_agent.validator`) enforces that before this layer
ever sees the SQL.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


@dataclass
class ColumnInfo:
    name: str
    type: str
    primary_key: bool = False
    foreign_key: str | None = None  # "table.column" if this column references one


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class Schema:
    """Everything the SQL agent needs to know about the database."""

    tables: dict[str, TableInfo] = field(default_factory=dict)

    def table_names(self) -> list[str]:
        return sorted(self.tables)

    def all_columns(self) -> dict[str, set[str]]:
        """table -> set of column names."""
        return {name: set(t.column_names()) for name, t in self.tables.items()}

    def all_column_names(self) -> set[str]:
        out: set[str] = set()
        for table in self.tables.values():
            out.update(table.column_names())
        return out

    def format_for_prompt(self) -> str:
        """Compact schema block pasted into the SQL-agent prompt."""
        lines: list[str] = []
        for name in self.table_names():
            table = self.tables[name]
            cols = []
            for col in table.columns:
                suffix = ""
                if col.primary_key:
                    suffix = " [PK]"
                elif col.foreign_key:
                    suffix = f" [FK -> {col.foreign_key}]"
                cols.append(f"{col.name} {col.type}{suffix}")
            lines.append(f"TABLE {name} ({', '.join(cols)})")
        return "\n".join(lines)


class Database:
    """Thin SQLAlchemy wrapper with timeout + row-cap execution."""

    def __init__(self, db_url: str) -> None:
        connect_args = {}
        if db_url.startswith("sqlite"):
            # Allow use from the executor thread used for timeouts.
            connect_args = {"check_same_thread": False}
        self.engine: Engine = create_engine(db_url, connect_args=connect_args)
        self.db_url = db_url

    # -- schema ---------------------------------------------------------
    def get_schema(self) -> Schema:
        inspector = inspect(self.engine)
        schema = Schema()
        for table_name in inspector.get_table_names():
            columns: list[ColumnInfo] = []
            pk_cols = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
            fk_map: dict[str, str] = {}
            for fk in inspector.get_foreign_keys(table_name):
                for local, remote in zip(
                    fk.get("constrained_columns") or [],
                    fk.get("referred_columns") or [],
                ):
                    fk_map[local] = f"{fk.get('referred_table')}.{remote}"
            for col in inspector.get_columns(table_name):
                columns.append(
                    ColumnInfo(
                        name=col["name"],
                        type=str(col["type"]),
                        primary_key=col["name"] in pk_cols,
                        foreign_key=fk_map.get(col["name"]),
                    )
                )
            schema.tables[table_name] = TableInfo(name=table_name, columns=columns)
        return schema

    # -- safety ---------------------------------------------------------
    def explain(self, sql: str) -> None:
        """Run EXPLAIN so syntax errors surface before execution.

        Raises the database error unchanged if the SQL is invalid.
        """
        if self.db_url.startswith("sqlite"):
            stmt = f"EXPLAIN QUERY PLAN {sql.rstrip().rstrip(';')}"
        else:
            stmt = f"EXPLAIN {sql.rstrip().rstrip(';')}"
        with self.engine.connect() as conn:
            conn.execute(text(stmt))

    # -- execution ------------------------------------------------------
    def execute(self, sql: str, timeout_seconds: float, row_cap: int):
        """Execute read-only SQL, returning (columns, rows).

        Rows are capped at ``row_cap``. Execution is bounded by
        ``timeout_seconds`` via a worker thread (works on Windows too).
        """
        clean = sql.strip().rstrip(";")

        def _run():
            with self.engine.connect() as conn:
                result = conn.execute(text(clean))
                columns = list(result.keys())
                rows = result.fetchmany(row_cap)
                return columns, [tuple(r) for r in rows]

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            try:
                return future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(
                    f"Query exceeded the {timeout_seconds:g}s timeout and was stopped."
                ) from None
