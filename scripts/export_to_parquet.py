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
    """Export OHLCV bars with full announcement linkage.

    For each announcement, links ALL bars in its time window (not just those
    originally linked). This enables fast direct lookups without fallback queries.
    Uses vectorized pandas operations for speed.
    """
    output_dir = PARQUET_DIR / "ohlcv_1min"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("  Loading announcements for linkage...")
    ann_df = pd.read_sql(
        "SELECT ticker, timestamp FROM announcements WHERE source = 'backfill'",
        engine
    )
    print(f"  Found {len(ann_df):,} announcements")

    print("  Loading all OHLCV bars...")
    bars_df = pd.read_sql("""
        SELECT ticker, timestamp, open, high, low, close, volume, vwap
        FROM ohlcv_bars
        ORDER BY ticker, timestamp
    """, engine)
    print(f"  Found {len(bars_df):,} bars")

    print("  Re-linking bars to announcements (vectorized)...")
    from datetime import timedelta

    # Rename columns for clarity during merge
    ann_df = ann_df.rename(columns={
        'ticker': 'announcement_ticker',
        'timestamp': 'announcement_timestamp'
    })

    # Merge on ticker to get all possible bar-announcement pairs
    merged = bars_df.merge(
        ann_df,
        left_on='ticker',
        right_on='announcement_ticker',
        how='inner'
    )
    print(f"    Merged: {len(merged):,} candidate pairs")

    # Filter to bars within the time window: -5 to +125 minutes
    time_mask = (
        (merged['timestamp'] >= merged['announcement_timestamp'] - timedelta(minutes=5)) &
        (merged['timestamp'] <= merged['announcement_timestamp'] + timedelta(minutes=125))
    )
    linked_df = merged[time_mask].copy()
    print(f"    After time filter: {len(linked_df):,} linked records")

    if linked_df.empty:
        print("  No linked bars to export")
        return

    # Partition by month based on bar timestamp
    linked_df['month'] = linked_df['timestamp'].dt.to_period('M')

    total_rows = 0
    for month, month_df in linked_df.groupby('month'):
        month_str = str(month)
        output_path = output_dir / f"{month_str}.parquet"

        if output_path.exists() and not force:
            print(f"    {month_str}: exists, skipping")
            continue

        # Drop the month column before saving
        export_df = month_df.drop(columns=['month'])
        export_df.to_parquet(output_path, index=False, compression="snappy")

        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"    {month_str}: {len(export_df):,} rows ({size_mb:.1f} MB)")
        total_rows += len(export_df)

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
