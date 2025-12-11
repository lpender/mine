#!/usr/bin/env python3
"""
Re-fetch OHLCV data with 120 minute window for all cached announcements.

Usage:
    python refetch_data.py
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.massive_client import MassiveClient


def main():
    client = MassiveClient()

    # Load all announcements
    announcements = client.load_announcements()

    if not announcements:
        print("No announcements found in cache.")
        return

    print(f"Found {len(announcements)} announcements to re-fetch")
    print(f"Window: 120 minutes (was 30-60 minutes)")
    print("-" * 50)

    success_count = 0
    error_count = 0

    for i, ann in enumerate(announcements, 1):
        ticker = ann.ticker
        start = ann.timestamp
        end = start + timedelta(minutes=120)

        # Generate cache path
        date_str = start.strftime("%Y%m%d_%H%M")
        cache_path = client.cache_dir / f"{ticker}_{date_str}.parquet"

        print(f"\n[{i}/{len(announcements)}] {ticker} @ {start.strftime('%Y-%m-%d %H:%M')}")

        # Delete existing cache file to force re-fetch
        if cache_path.exists():
            cache_path.unlink()
            print(f"  Deleted old cache: {cache_path.name}")

        # Fetch new data with 120 minute window
        bars = client.fetch_ohlcv(ticker, start, end, use_cache=False)

        if bars:
            # Save to cache
            client._save_to_cache(ticker, start, end, bars)
            print(f"  Fetched {len(bars)} bars, saved to {cache_path.name}")
            success_count += 1
        else:
            print(f"  ERROR: No data returned")
            error_count += 1

    print("\n" + "=" * 50)
    print(f"Done! Success: {success_count}, Errors: {error_count}")


if __name__ == "__main__":
    main()
