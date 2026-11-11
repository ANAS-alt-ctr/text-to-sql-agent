"""Download the real Olist dataset from Kaggle and load it into a database.

Dataset: Olist Brazilian E-Commerce Public Dataset
(https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
~99,441 orders, 9 tables, ~126 MB, CC BY-NC-SA 4.0.

Kaggle API setup (one time, on your laptop):
  1. Go to https://www.kaggle.com/settings and create an API token.
     It downloads ``kaggle.json``.
  2. Put it where the Kaggle client expects it:
       Windows:  C:\\Users\\<you>\\.kaggle\\kaggle.json
       Linux:    ~/.kaggle/kaggle.json
  3. ``pip install kaggle`` (already in requirements.txt).

Usage:
    python scripts/load_olist.py --out data/olist.db
    python scripts/load_olist.py --out data/olist.db --db-url postgresql+psycopg2://user:pass@localhost:5432/olist

Then point the agent at it with DB_URL, e.g.:
    DB_URL=sqlite:///./data/olist.db uvicorn app.main:app
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

import pandas as pd

DATASET = "olistbr/brazilian-ecommerce"

# Kaggle CSV file -> (target table, columns to keep as-is).
# Types are inferred by pandas and written with to_sql; dates stay as text.
TABLES = {
    "olist_customers_dataset.csv": "customers",
    "olist_sellers_dataset.csv": "sellers",
    "olist_products_dataset.csv": "products",
    "olist_orders_dataset.csv": "orders",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "order_items",
}


def download_dataset(download_dir: str) -> str:
    """Download + unzip via the Kaggle CLI. Returns the extracted dir."""
    os.makedirs(download_dir, exist_ok=True)
    print(f"Downloading {DATASET} with the Kaggle CLI ...")
    result = subprocess.run(
        [sys.executable, "-m", "kaggle", "datasets", "download", "-d", DATASET,
         "-p", download_dir, "--unzip"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(
            "Kaggle download failed. Check that kaggle.json is installed "
            "(see the module docstring) and that you accepted the dataset "
            "rules on its Kaggle page."
        )
    return download_dir


def load_into_sqlite(extracted_dir: str, out_path: str) -> dict[str, int]:
    if os.path.exists(out_path):
        os.remove(out_path)
    conn = sqlite3.connect(out_path)
    counts: dict[str, int] = {}
    try:
        for csv_name, table in TABLES.items():
            path = os.path.join(extracted_dir, csv_name)
            if not os.path.exists(path):
                print(f"  skip missing {csv_name}")
                continue
            df = pd.read_csv(path)
            df.to_sql(table, conn, if_exists="replace", index=False)
            counts[table] = len(df)
            print(f"  {table}: {len(df)} rows")
        # Minimal indexes the agent's typical queries benefit from.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id)",
            "CREATE INDEX IF NOT EXISTS idx_items_product ON order_items(product_id)",
            "CREATE INDEX IF NOT EXISTS idx_payments_order ON order_payments(order_id)",
            "CREATE INDEX IF NOT EXISTS idx_reviews_order ON order_reviews(order_id)",
        ):
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return counts


def load_into_postgres(extracted_dir: str, db_url: str) -> dict[str, int]:
    from sqlalchemy import create_engine

    engine = create_engine(db_url)
    counts: dict[str, int] = {}
    for csv_name, table in TABLES.items():
        path = os.path.join(extracted_dir, csv_name)
        if not os.path.exists(path):
            print(f"  skip missing {csv_name}")
            continue
        df = pd.read_csv(path)
        df.to_sql(table, engine, if_exists="replace", index=False)
        counts[table] = len(df)
        print(f"  {table}: {len(df)} rows")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/olist.db",
                        help="SQLite file to create (ignored with --db-url).")
    parser.add_argument("--db-url", default="",
                        help="SQLAlchemy URL for Postgres instead of SQLite.")
    parser.add_argument("--download-dir", default="",
                        help="Reuse an already-downloaded+unzipped folder.")
    args = parser.parse_args()

    if args.download_dir:
        extracted = args.download_dir
    else:
        tmp = tempfile.mkdtemp(prefix="olist-")
        try:
            extracted = download_dataset(tmp)
            if args.db_url:
                counts = load_into_postgres(extracted, args.db_url)
            else:
                counts = load_into_sqlite(extracted, args.out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        if args.db_url:
            counts = load_into_postgres(extracted, args.db_url)
        else:
            counts = load_into_sqlite(extracted, args.out)

    print(f"Done. Total rows: {sum(counts.values())}")


if __name__ == "__main__":
    main()
