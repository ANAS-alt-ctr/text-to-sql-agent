"""Business glossary / semantic layer.

This is where most text-to-SQL demos fail in production: the model
guesses what business words mean instead of being *told*. Every term
below is a fact about this dataset, written once, and pasted into the
SQL agent's prompt on every question.

Two parts:

* ``GLOSSARY`` -- business terms -> definition + the exact SQL fragment
  that implements them. If a question uses a term that is NOT here
  (e.g. "profit"), the agent must ask for clarification instead of
  inventing a definition.
* ``SYNONYMS`` -- everyday words -> the schema object they mean, so
  "customers in São Paulo" grounds to ``customers.customer_state = 'SP'``
  and "orders" grounds to the ``orders`` table instead of being guessed.
"""

from __future__ import annotations

# term -> {definition, sql} ; "sql" is the fragment the agent should use.
GLOSSARY: dict[str, dict[str, str]] = {
    "revenue": {
        "definition": "Money earned from items sold, before freight and before refunds.",
        "sql": "SUM(order_items.price) -- revenue; join order_items on orders.order_id",
    },
    "order value": {
        "definition": "Total paid for one order: sum of item prices plus freight.",
        "sql": "SUM(order_items.price + order_items.freight_value) per order",
    },
    "average order value": {
        "definition": "Mean total paid per order (item prices + freight).",
        "sql": "AVG(order_total) where order_total = SUM(order_items.price + order_items.freight_value) grouped by order_id",
    },
    "late delivery": {
        "definition": "An order delivered after the estimated delivery date.",
        "sql": "orders.order_delivered_customer_date > orders.order_estimated_delivery_date",
    },
    "on-time delivery": {
        "definition": "An order delivered on or before the estimated delivery date.",
        "sql": "orders.order_delivered_customer_date <= orders.order_estimated_delivery_date",
    },
    "delivery time": {
        "definition": "Days between purchase and delivery to the customer.",
        "sql": "julianday(orders.order_delivered_customer_date) - julianday(orders.order_purchase_timestamp) -- SQLite; use DATE_PART('day', ...) on Postgres",
    },
    "review score": {
        "definition": "Customer rating from 1 (worst) to 5 (best).",
        "sql": "order_reviews.review_score",
    },
    "seller": {
        "definition": "The merchant who sold the items in an order.",
        "sql": "sellers.seller_id, joined via order_items.seller_id",
    },
    "payment value": {
        "definition": "Amount paid in one payment record. One order can have several payments.",
        "sql": "order_payments.payment_value",
    },
    "freight": {
        "definition": "Delivery charge for an item.",
        "sql": "order_items.freight_value",
    },
}

# Everyday words -> schema objects. Used by the ambiguity check and
# pasted into the prompt so the model grounds terms instead of guessing.
SYNONYMS: dict[str, str] = {
    "orders": "orders",
    "order": "orders",
    "customers": "customers",
    "customer": "customers",
    "products": "products",
    "product": "products",
    "sellers": "sellers",
    "payments": "order_payments",
    "payment": "order_payments",
    "reviews": "order_reviews",
    "review": "order_reviews",
    "ratings": "order_reviews.review_score",
    "rating": "order_reviews.review_score",
    "sales": "order_items.price",
    "price": "order_items.price",
    "state": "customers.customer_state",
    "city": "customers.customer_city",
    "category": "products.product_category_name",
    "delivered": "orders.order_status = 'delivered'",
    "cancelled": "orders.order_status = 'canceled'",
    "shipped": "orders.order_status = 'shipped'",
}


def format_glossary_for_prompt() -> str:
    """Glossary block pasted into the SQL-agent prompt."""
    lines = ["BUSINESS GLOSSARY -- use exactly these definitions:"]
    for term, info in GLOSSARY.items():
        lines.append(f"- {term}: {info['definition']}  [SQL: {info['sql']}]")
    lines.append("")
    lines.append("SYNONYMS -- everyday words and the schema object they mean:")
    for word, target in SYNONYMS.items():
        lines.append(f"- '{word}' means {target}")
    lines.append("")
    lines.append(
        "If the question uses a business term that is NOT in this glossary "
        "(for example 'profit', 'margin', 'churn'), do NOT invent a "
        "definition. Return a clarification request instead."
    )
    return "\n".join(lines)
