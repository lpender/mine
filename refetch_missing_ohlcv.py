#!/usr/bin/env python3
"""Identify and refetch missing OHLCV data for announcements."""

import sys
from datetime import date, timedelta
from src.postgres_client import PostgresClient
from src.models import get_market_session


def main():
    client = PostgresClient()
    announcements = client.load_announcements()

    print(f"Loaded {len(announcements)} announcements")

    # Find announcements missing OHLCV data
    missing = []
    for ann in announcements:
        # Skip today's announcements (data not available yet)
        session = get_market_session(ann.timestamp)

        # Calculate effective start time
        if session == "closed":
            effective_start = ann.timestamp.replace(hour=9, minute=30, second=0, microsecond=0)
            if ann.timestamp.hour >= 20:
                effective_start += timedelta(days=1)
            while effective_start.weekday() >= 5:
                effective_start += timedelta(days=1)
        elif session == "postmarket":
            effective_start = ann.timestamp.replace(hour=9, minute=30, second=0, microsecond=0)
            effective_start += timedelta(days=1)
            while effective_start.weekday() >= 5:
                effective_start += timedelta(days=1)
        else:
            effective_start = ann.timestamp

        # Skip if trading window is today or future
        if effective_start.date() >= date.today():
            continue

        # Check if we have data
        end_time = effective_start + timedelta(minutes=120)
        has_data = client.has_ohlcv_data(ann.ticker, effective_start, end_time)

        if not has_data:
            missing.append(ann)

    print(f"\nFound {len(missing)} announcements missing OHLCV data:")
    for ann in missing[:20]:  # Show first 20
        print(f"  {ann.timestamp} {ann.ticker}")

    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more")

    if not missing:
        print("All announcements have OHLCV data!")
        return

    # Ask to refetch
    if len(sys.argv) > 1 and sys.argv[1] == "--refetch":
        print(f"\nRefetching OHLCV data for {len(missing)} announcements...")
        for i, ann in enumerate(missing):
            print(f"[{i+1}/{len(missing)}] Fetching {ann.ticker} @ {ann.timestamp}...")
            bars = client.fetch_after_announcement(
                ann.ticker,
                ann.timestamp,
                window_minutes=120,
                use_cache=False  # Force refetch
            )
            if bars:
                print(f"  -> Got {len(bars)} bars")
            else:
                print(f"  -> No data available")
    else:
        print("\nRun with --refetch to fetch missing data")


if __name__ == "__main__":
    main()
