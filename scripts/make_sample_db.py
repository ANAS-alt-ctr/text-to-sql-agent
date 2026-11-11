"""Build the built-in sample database (``data/sample.db``).

The sample uses the REAL Olist table and column names (a subset of
columns), so the golden set, the glossary and the agent prompts all work
unchanged against the full Olist download. It is deterministic
(``random.Random(42)``), so the golden-set expected rows never drift.

Contents: 10 sellers, 15 products, 30 customers, 40 orders, ~60 order
items, ~40 payments, 30 reviews, 10 geolocation rows.

Usage:
    python scripts/make_sample_db.py [--out data/sample.db]
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
from datetime import datetime, timedelta

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    customer_unique_id TEXT,
    customer_zip_code_prefix INTEGER,
    customer_city TEXT,
    customer_state TEXT
);
CREATE TABLE IF NOT EXISTS sellers (
    seller_id TEXT PRIMARY KEY,
    seller_zip_code_prefix INTEGER,
    seller_city TEXT,
    seller_state TEXT
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    product_category_name TEXT,
    product_name_length INTEGER,
    product_description_length INTEGER,
    product_photos_qty INTEGER,
    product_weight_g INTEGER,
    product_length_cm REAL,
    product_height_cm REAL,
    product_width_cm REAL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT,
    order_status TEXT,
    order_purchase_timestamp TEXT,
    order_approved_at TEXT,
    order_delivered_carrier_date TEXT,
    order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT,
    order_item_id INTEGER,
    product_id TEXT,
    seller_id TEXT,
    price REAL,
    freight_value REAL,
    PRIMARY KEY (order_id, order_item_id),
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
);
CREATE TABLE IF NOT EXISTS order_payments (
    order_id TEXT,
    payment_sequential INTEGER,
    payment_type TEXT,
    payment_installments INTEGER,
    payment_value REAL,
    PRIMARY KEY (order_id, payment_sequential),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);
CREATE TABLE IF NOT EXISTS order_reviews (
    review_id TEXT PRIMARY KEY,
    order_id TEXT,
    review_score INTEGER,
    review_comment_title TEXT,
    review_comment_message TEXT,
    review_creation_date TEXT,
    review_answer_timestamp TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);
CREATE TABLE IF NOT EXISTS geolocation (
    geolocation_zip_code_prefix INTEGER,
    geolocation_lat REAL,
    geolocation_lng REAL,
    geolocation_city TEXT,
    geolocation_state TEXT
);
"""

SELLERS = [
    ("s001", 1001, "Sao Paulo", "SP"),
    ("s002", 2001, "Rio de Janeiro", "RJ"),
    ("s003", 3001, "Belo Horizonte", "MG"),
    ("s004", 4001, "Porto Alegre", "RS"),
    ("s005", 5001, "Curitiba", "PR"),
    ("s006", 6001, "Salvador", "BA"),
    ("s007", 7001, "Florianopolis", "SC"),
    ("s008", 8001, "Recife", "PE"),
    ("s009", 9001, "Goiania", "GO"),
    ("s010", 1002, "Fortaleza", "CE"),
]

# (product_id, category, base price)
PRODUCTS = [
    ("p001", "beleza_saude", 29.90),
    ("p002", "beleza_saude", 49.90),
    ("p003", "cama_mesa_banho", 89.90),
    ("p004", "cama_mesa_banho", 129.90),
    ("p005", "esporte_lazer", 199.90),
    ("p006", "esporte_lazer", 79.90),
    ("p007", "moveis_decoracao", 349.90),
    ("p008", "moveis_decoracao", 499.90),
    ("p009", "informatica_acessorios", 59.90),
    ("p010", "informatica_acessorios", 149.90),
    ("p011", "telefonia", 899.90),
    ("p012", "utilidades_domesticas", 39.90),
    ("p013", "brinquedos", 69.90),
    ("p014", "ferramentas_jardim", 119.90),
    ("p015", "automotivo", 159.90),
]

CITIES = [
    ("Sao Paulo", "SP", 1000),
    ("Rio de Janeiro", "RJ", 2000),
    ("Belo Horizonte", "MG", 3000),
    ("Porto Alegre", "RS", 4000),
    ("Curitiba", "PR", 5000),
    ("Salvador", "BA", 6000),
    ("Florianopolis", "SC", 7000),
    ("Recife", "PE", 8000),
    ("Goiania", "GO", 9000),
    ("Fortaleza", "CE", 1003),
]


def _fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def build_sample_db(path: str) -> dict[str, int]:
    rng = random.Random(42)
    # sqlite3 cannot create the file if the parent folder does not exist yet
    # (fresh clone has no data/ folder), so create it first.
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        for table in ("order_reviews", "order_payments", "order_items",
                      "orders", "products", "sellers", "customers", "geolocation"):
            conn.execute(f"DELETE FROM {table}")

        conn.executemany("INSERT INTO sellers VALUES (?,?,?,?)", SELLERS)
        conn.executemany(
            "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (pid, cat, 12, 240, 3, 500, 20.0, 10.0, 15.0)
                for pid, cat, _ in PRODUCTS
            ],
        )
        customers = []
        for i in range(30):
            city, state, zprefix = CITIES[i % len(CITIES)]
            customers.append((f"c{i+1:03d}", f"u{i+1:03d}", zprefix + (i % 7), city, state))
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)
        conn.executemany(
            "INSERT INTO geolocation VALUES (?,?,?,?,?)",
            [(z, -23.5 + i * 0.3, -46.6 + i * 0.2, city, state)
             for i, (city, state, z) in enumerate(CITIES)],
        )

        base = datetime(2017, 1, 5, 10, 0, 0)
        statuses = ["delivered"] * 32 + ["canceled"] * 3 + ["shipped"] * 3 + ["invoiced", "processing"]
        order_rows, item_rows, pay_rows, review_rows = [], [], [], []
        for n in range(40):
            oid = f"o{n+1:03d}"
            cust = customers[rng.randrange(len(customers))][0]
            status = statuses[n]
            purchase = base + timedelta(days=rng.randrange(0, 560), hours=rng.randrange(0, 12))
            approved = purchase + timedelta(hours=2) if status != "canceled" else None
            delivered = None
            carrier = None
            estimated = None
            if status in ("delivered", "shipped"):
                est_days = rng.randrange(7, 21)
                estimated = purchase + timedelta(days=est_days)
                carrier = purchase + timedelta(days=rng.randrange(1, 4))
                if status == "delivered":
                    # ~25% of delivered orders arrive late.
                    late = rng.random() < 0.25
                    delivered = estimated + timedelta(days=rng.randrange(1, 6)) if late \
                        else estimated - timedelta(days=rng.randrange(0, 4))
            order_rows.append((
                oid, cust, status, _fmt(purchase), _fmt(approved),
                _fmt(carrier), _fmt(delivered), _fmt(estimated),
            ))
            n_items = rng.choice([1, 1, 2, 2, 3])
            order_total = 0.0
            for item_no in range(1, n_items + 1):
                pid, _cat, base_price = PRODUCTS[rng.randrange(len(PRODUCTS))]
                seller = SELLERS[rng.randrange(len(SELLERS))][0]
                price = round(base_price * rng.uniform(0.9, 1.1), 2)
                freight = round(rng.uniform(5.0, 40.0), 2)
                item_rows.append((oid, item_no, pid, seller, price, freight))
                order_total += price + freight
            if status != "canceled":
                n_pay = 1 if rng.random() < 0.7 else 2
                remaining = round(order_total, 2)
                for seq in range(1, n_pay + 1):
                    if seq == n_pay:
                        value = remaining
                    else:
                        value = round(order_total * rng.uniform(0.4, 0.6), 2)
                        remaining = round(remaining - value, 2)
                    pay_rows.append((
                        oid, seq,
                        rng.choice(["credit_card", "credit_card", "boleto", "debit_card", "voucher"]),
                        rng.choice([1, 1, 2, 3, 6]),
                        value,
                    ))
            if status == "delivered":
                score = rng.choices([1, 2, 3, 4, 5], weights=[2, 2, 4, 8, 14])[0]
                review_rows.append((
                    f"r{n+1:03d}", oid, score, None, None,
                    _fmt(delivered + timedelta(days=1)) if delivered else None,
                    _fmt(delivered + timedelta(days=2)) if delivered else None,
                ))

        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", order_rows)
        conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", item_rows)
        conn.executemany("INSERT INTO order_payments VALUES (?,?,?,?,?)", pay_rows)
        conn.executemany("INSERT INTO order_reviews VALUES (?,?,?,?,?,?,?)", review_rows)
        conn.commit()
        counts = {
            "customers": len(customers),
            "sellers": len(SELLERS),
            "products": len(PRODUCTS),
            "orders": len(order_rows),
            "order_items": len(item_rows),
            "order_payments": len(pay_rows),
            "order_reviews": len(review_rows),
            "geolocation": len(CITIES),
        }
        return counts
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the sample Olist-style database.")
    parser.add_argument("--out", default="data/sample.db")
    args = parser.parse_args()
    counts = build_sample_db(args.out)
    print(f"Wrote {args.out}")
    for table, n in counts.items():
        print(f"  {table}: {n} rows")


if __name__ == "__main__":
    main()
