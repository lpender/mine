#!/usr/bin/env python3
"""
Export Postgres data to Parquet files for fast DuckDB queries.

Usage:
    python scripts/export_to_parquet.py [--force]

This exports:
    - announcements → data/parquet/announcements.parquet
    - ohlcv_bars → data/parquet/ohlcv_1min/ (partitioned by month)
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

from dotenv import load_dotenv

load_dotenv()

PARQUET_DIR = Path(__file__).parent.parent / "data" / "parquet"


def get_engine():
    """Create SQLAlchemy engine from DATABASE_URL."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")
    return create_engine(database_url)


def export_announcements(engine, force: bool = False):
    """Export announcements table to Parquet."""
    output_path = PARQUET_DIR / "announcements.parquet"

    if output_path.exists() and not force:
        print(f"  {output_path} exists, skipping (use --force to overwrite)")
        return

    print("  Loading announcements from Postgres...")
    df = pd.read_sql("SELECT * FROM announcements", engine)
    print(f"  Loaded {len(df):,} rows")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to Parquet with compression
    df.to_parquet(output_path, index=False, compression="snappy")
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Wrote {output_path} ({size_mb:.1f} MB)")


def export_ohlcv(engine, force: bool = False):
    """Export OHLCV bars to partitioned Parquet files by month."""
    output_dir = PARQUET_DIR / "ohlcv_1min"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("  Querying OHLCV date range...")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                DATE_TRUNC('month', timestamp) as month,
                COUNT(*) as cnt
            FROM ohlcv_bars
            GROUP BY DATE_TRUNC('month', timestamp)
            ORDER BY month
        """))
        months = [(row[0], row[1]) for row in result]

    if not months:
        print("  No OHLCV data found")
        return

    print(f"  Found {len(months)} months of data")

    total_rows = 0
    for month_dt, count in months:
        month_str = month_dt.strftime("%Y-%m")
        output_path = output_dir / f"{month_str}.parquet"

        if output_path.exists() and not force:
            print(f"    {month_str}: exists, skipping")
            continue

        # Query this month's data
        next_month = (month_dt.replace(day=28) + pd.Timedelta(days=4)).replace(day=1)

        query = f"""
            SELECT
                ticker,
                timestamp,
                open,
                high,
                low,
                close,
                volume,
                vwap,
                announcement_ticker,
                announcement_timestamp
            FROM ohlcv_bars
            WHERE timestamp >= '{month_dt}'
              AND timestamp < '{next_month}'
        """

        df = pd.read_sql(query, engine)
        df.to_parquet(output_path, index=False, compression="snappy")

        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"    {month_str}: {len(df):,} rows ({size_mb:.1f} MB)")
        total_rows += len(df)

    print(f"  Total: {total_rows:,} rows exported")


def main():
    parser = argparse.ArgumentParser(description="Export Postgres data to Parquet")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    print("=" * 60)
    print("Exporting Postgres → Parquet")
    print("=" * 60)

    engine = get_engine()

    print("\n[1/2] Announcements")
    export_announcements(engine, force=args.force)

    print("\n[2/2] OHLCV Bars (by month)")
    export_ohlcv(engine, force=args.force)

    print("\n" + "=" * 60)
    print("Export complete!")
    print(f"Files written to: {PARQUET_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
