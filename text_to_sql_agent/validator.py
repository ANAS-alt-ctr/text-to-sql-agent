"""SQL validation -- the safety layer.

Every SQL string the agent generates passes through
:func:`validate_sql` before it is ever executed. It checks, in order:

1. **Exactly one statement**, starting with ``SELECT`` (or ``WITH``
   followed by a ``SELECT``).
2. **No forbidden keywords** -- DELETE / UPDATE / INSERT / DROP / ALTER /
   CREATE / TRUNCATE / GRANT / REVOKE / VACUUM / PRAGMA / ATTACH / INTO.
   Keywords inside string literals do not count (literals are stripped
   first), so ``WHERE product_category_name = 'drop'`` is fine.
3. **Every referenced table exists** in the schema.
4. **Every referenced column exists** (hallucination detection) -- a
   column the schema does not have is rejected, not guessed at.
5. **EXPLAIN runs clean** (optional, when a ``Database`` is supplied),
   so syntax errors are caught before execution.

Limits (documented honestly in README.md): alias resolution covers
plain ``FROM t AS a`` / ``JOIN t a`` forms; deeply nested subqueries
and CTEs are validated on their outer tables/columns only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlparse
from sqlparse.sql import Identifier, IdentifierList
from sqlparse.tokens import Comment, Name, String, Whitespace

from .db import Database, Schema

FORBIDDEN_KEYWORDS = {
    "DELETE",
    "UPDATE",
    "INSERT",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "VACUUM",
    "PRAGMA",
    "ATTACH",
    "INTO",  # SELECT ... INTO creates a table on Postgres
    "REPLACE",
    "CALL",
    "EXEC",
    "EXECUTE",
}

SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
    "FULL", "CROSS", "ON", "GROUP", "BY", "HAVING", "ORDER", "LIMIT",
    "OFFSET", "AS", "AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE",
    "BETWEEN", "CASE", "WHEN", "THEN", "ELSE", "END", "DISTINCT", "ALL",
    "UNION", "EXCEPT", "INTERSECT", "WITH", "ASC", "DESC", "COUNT",
    "SUM", "AVG", "MIN", "MAX", "COALESCE", "CAST", "TRUE", "FALSE",
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized_sql: str | None = None


def _strip_literals_and_comments(sql: str) -> str:
    """Remove string literals and comments so keyword checks cannot be
    fooled by words inside them."""
    no_strings = re.sub(r"'(?:[^']|'')*'", "''", sql)
    no_comments = re.sub(r"--[^\n]*", "", no_strings)
    no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.S)
    return no_comments


def _first_keyword(sql: str) -> str:
    tokens = [t for t in sqlparse.parse(sql)[0].flatten()
              if t.ttype not in (Whitespace, Comment)]
    for token in tokens:
        if token.ttype in (String,):
            continue
        return token.value.upper()
    return ""


def _find_forbidden(clean_sql: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", clean_sql)
    return sorted({w.upper() for w in words if w.upper() in FORBIDDEN_KEYWORDS})


def _alias_map(sql: str) -> dict[str, str]:
    """alias -> real table name, from FROM/JOIN clauses."""
    mapping: dict[str, str] = {}
    pattern = re.compile(
        r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        table, alias = match.group(1), match.group(2)
        if alias and alias.upper() not in SQL_KEYWORDS:
            mapping[alias.lower()] = table.lower()
        mapping[table.lower()] = table.lower()
    return mapping


def extract_tables(sql: str) -> set[str]:
    """Table names referenced after FROM/JOIN (aliases resolved)."""
    return set(_alias_map(sql).values())


def extract_columns(sql: str) -> set[str]:
    """Column names referenced in the SQL.

    Covers ``table.column`` references and bare identifiers in the
    SELECT / WHERE / GROUP BY / ORDER BY clauses (function names and
    SQL keywords are excluded).
    """
    clean = _strip_literals_and_comments(sql)
    found: set[str] = set()

    # table.column references
    for _, col in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", clean):
        found.add(col.lower())

    # Bare identifiers: walk sqlparse tokens, take Name tokens that are
    # not function calls (not followed by "(") and not keywords.
    try:
        parsed = sqlparse.parse(clean)
    except Exception:
        return found
    for stmt in parsed:
        tokens = list(stmt.flatten())
        for i, token in enumerate(tokens):
            if token.ttype is Name:
                value = token.value
                if value.upper() in SQL_KEYWORDS:
                    continue
                # Skip function names: next non-whitespace token is "(".
                nxt = next(
                    (t for t in tokens[i + 1:] if t.ttype is not Whitespace),
                    None,
                )
                if nxt is not None and nxt.value == "(":
                    continue
                found.add(value.lower())
    # Remove anything that is actually a table name or alias.
    tables = {t.lower() for t in extract_tables(sql)}
    found -= tables
    found -= {a.lower() for a in _alias_map(sql)}
    return found


def validate_sql(sql: str, schema: Schema, db: Database | None = None) -> ValidationResult:
    """Validate generated SQL against the schema (and optionally EXPLAIN)."""
    errors: list[str] = []
    warnings: list[str] = []
    raw = (sql or "").strip()
    if not raw:
        return ValidationResult(ok=False, errors=["Empty SQL string."])

    # 1. Exactly one statement.
    try:
        statements = [s for s in sqlparse.split(raw) if s.strip()]
    except Exception as exc:  # pragma: no cover - sqlparse rarely raises
        return ValidationResult(ok=False, errors=[f"Could not parse SQL: {exc}"])
    if len(statements) != 1:
        return ValidationResult(
            ok=False,
            errors=[f"Expected exactly 1 statement, found {len(statements)}."],
        )
    single = statements[0].strip().rstrip(";")

    # 2. Must start with SELECT or WITH.
    first = _first_keyword(single)
    if first not in ("SELECT", "WITH"):
        return ValidationResult(
            ok=False,
            errors=[f"Only SELECT queries are allowed; statement starts with {first!r}."],
        )

    # 3. Forbidden keywords (checked on SQL with literals stripped).
    clean = _strip_literals_and_comments(single)
    forbidden = _find_forbidden(clean)
    if forbidden:
        errors.append(
            "Forbidden keyword(s) in read-only query: " + ", ".join(forbidden) + "."
        )

    # 4. Tables must exist.
    tables = extract_tables(single)
    known_tables = {t.lower() for t in schema.table_names()}
    unknown_tables = sorted(t for t in tables if t.lower() not in known_tables)
    if unknown_tables:
        errors.append("Unknown table(s): " + ", ".join(unknown_tables) + ".")

    # 5. Columns must exist (hallucination detection).
    columns = extract_columns(single)
    known_columns = schema.all_column_names()
    known_columns_lower = {c.lower() for c in known_columns}
    unknown_columns = sorted(c for c in columns if c not in known_columns_lower)
    # Ignore the alias introduced by "AS <alias>" in the SELECT list.
    alias_defs = set(re.findall(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", clean, re.IGNORECASE))
    unknown_columns = [c for c in unknown_columns if c not in {a.lower() for a in alias_defs}]
    if unknown_columns:
        errors.append(
            "Unknown column(s) -- possible hallucination: "
            + ", ".join(unknown_columns)
            + ". These columns do not exist in the schema."
        )

    if errors:
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    # 6. EXPLAIN before execute (only if a database handle is given).
    if db is not None:
        try:
            db.explain(single)
        except Exception as exc:
            return ValidationResult(
                ok=False,
                errors=[f"EXPLAIN failed -- SQL is invalid: {exc}"],
                warnings=warnings,
            )

    if "SELECT *" in clean.upper().replace("  ", " "):
        warnings.append("SELECT * used -- prefer explicit columns for charts.")
    return ValidationResult(ok=True, errors=[], warnings=warnings, normalized_sql=single)
