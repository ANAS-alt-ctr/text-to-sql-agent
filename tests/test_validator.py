"""Unit tests for the SQL validator (the safety layer)."""

from __future__ import annotations

import pytest

from text_to_sql_agent.validator import extract_columns, extract_tables, validate_sql


def _schema(db):
    return db.get_schema()


def test_blocks_delete(db):
    result = validate_sql("DELETE FROM orders WHERE order_id = 'o001'", _schema(db))
    assert not result.ok
    assert any("DELETE" in e for e in result.errors)


def test_blocks_update_insert_drop(db):
    for sql in (
        "UPDATE orders SET order_status = 'canceled'",
        "INSERT INTO orders VALUES ('o999', 'c001', 'delivered', NULL, NULL, NULL, NULL, NULL)",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x TEXT",
        "CREATE TABLE evil (x TEXT)",
        "TRUNCATE TABLE orders",
    ):
        result = validate_sql(sql, _schema(db))
        assert not result.ok, sql
        assert result.errors


def test_blocks_second_statement(db):
    result = validate_sql("SELECT COUNT(*) FROM orders; DROP TABLE orders", _schema(db))
    assert not result.ok


def test_blocks_write_inside_cte(db):
    result = validate_sql(
        "WITH x AS (SELECT order_id FROM orders) DELETE FROM orders", _schema(db)
    )
    assert not result.ok


def test_keyword_inside_string_literal_is_allowed(db):
    # "drop" inside a quoted value must not trip the blocklist.
    result = validate_sql(
        "SELECT * FROM products WHERE product_category_name = 'drop'", _schema(db)
    )
    assert result.ok, result.errors


def test_rejects_unknown_table(db):
    result = validate_sql("SELECT * FROM nosuchtable", _schema(db))
    assert not result.ok
    assert any("nosuchtable" in e for e in result.errors)


def test_rejects_hallucinated_column(db):
    result = validate_sql("SELECT profit FROM orders", _schema(db))
    assert not result.ok
    assert any("profit" in e for e in result.errors)


def test_rejects_hallucinated_column_in_join(db):
    result = validate_sql(
        "SELECT o.order_id, c.lifetime_value FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id",
        _schema(db),
    )
    assert not result.ok
    assert any("lifetime_value" in e for e in result.errors)


def test_allows_valid_select(db):
    result = validate_sql("SELECT COUNT(*) FROM orders", _schema(db))
    assert result.ok, result.errors
    assert result.normalized_sql == "SELECT COUNT(*) FROM orders"


def test_allows_join_and_aggregation(db):
    result = validate_sql(
        "SELECT p.product_category_name, SUM(oi.price) AS revenue "
        "FROM order_items oi JOIN products p ON oi.product_id = p.product_id "
        "GROUP BY p.product_category_name ORDER BY revenue DESC",
        _schema(db),
    )
    assert result.ok, result.errors


def test_explain_catches_syntax_error(db):
    result = validate_sql("SELECT FROM orders", _schema(db), db=db)
    assert not result.ok


def test_extract_tables_and_columns(db):
    sql = (
        "SELECT o.order_id, c.customer_city FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id"
    )
    assert extract_tables(sql) == {"orders", "customers"}
    cols = extract_columns(sql)
    assert {"order_id", "customer_city", "customer_id"} <= cols
    assert "o" not in cols and "c" not in cols


def test_function_names_are_not_columns(db):
    cols = extract_columns(
        "SELECT AVG(julianday(order_delivered_customer_date) - "
        "julianday(order_purchase_timestamp)) FROM orders"
    )
    assert "julianday" not in cols
    assert "avg" not in cols
    assert "order_delivered_customer_date" in cols
