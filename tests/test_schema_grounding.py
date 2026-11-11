"""Schema grounding tests: the agent may only use what the schema has."""

from __future__ import annotations

from text_to_sql_agent.agents import detect_ambiguity
from text_to_sql_agent.glossary import GLOSSARY, format_glossary_for_prompt
from text_to_sql_agent.validator import validate_sql


def test_schema_has_olist_tables(db):
    schema = db.get_schema()
    expected = {
        "customers", "sellers", "products", "orders",
        "order_items", "order_payments", "order_reviews", "geolocation",
    }
    assert expected <= set(schema.table_names())


def test_schema_columns_match_olist(db):
    schema = db.get_schema()
    assert "order_status" in schema.tables["orders"].column_names()
    assert "order_delivered_customer_date" in schema.tables["orders"].column_names()
    assert "price" in schema.tables["order_items"].column_names()
    assert "review_score" in schema.tables["order_reviews"].column_names()
    assert "product_category_name" in schema.tables["products"].column_names()


def test_schema_format_lists_tables_and_keys(db):
    text = db.get_schema().format_for_prompt()
    assert "TABLE orders" in text
    assert "order_id" in text
    assert "[PK]" in text
    assert "[FK ->" in text


def test_glossary_defines_revenue_and_late_delivery():
    assert "revenue" in GLOSSARY
    assert "SUM(order_items.price)" in GLOSSARY["revenue"]["sql"]
    assert "late delivery" in GLOSSARY
    prompt = format_glossary_for_prompt()
    assert "do NOT invent a definition" in prompt


def test_ambiguity_precheck_flags_profit(db):
    schema = db.get_schema()
    ambiguous, terms = detect_ambiguity("What is our profit?", schema)
    assert ambiguous
    assert "profit" in terms


def test_ambiguity_precheck_flags_churn_and_retention(db):
    schema = db.get_schema()
    for question in (
        "How many customers churned last year?",
        "What is our customer retention rate?",
        "What is the profit margin?",
    ):
        ambiguous, _ = detect_ambiguity(question, schema)
        assert ambiguous, question


def test_ambiguity_precheck_does_not_flag_grounded_questions(db):
    schema = db.get_schema()
    for question in (
        "What is our total revenue?",
        "How many late deliveries were there?",
        "List the top 5 states by number of customers.",
        "What is the average review score?",
    ):
        ambiguous, _ = detect_ambiguity(question, schema)
        assert not ambiguous, question


def test_validator_rejects_source_column_but_allows_output_alias(db):
    schema = db.get_schema()
    # A model that reads a "profit" column that does not exist must be stopped.
    result = validate_sql("SELECT SUM(profit) FROM order_items", schema)
    assert not result.ok
    assert any("profit" in e for e in result.errors)
    # But naming a COMPUTED expression "profit" is a legitimate output
    # alias, not a hallucinated source column -- the validator cannot
    # judge the label's meaning. That misuse is caught upstream: the
    # glossary defines no "profit", and the ambiguity pre-check refuses
    # questions that ask for it.
    result = validate_sql(
        "SELECT SUM(price) - SUM(freight_value) AS profit FROM order_items",
        schema,
    )
    assert result.ok, result.errors
