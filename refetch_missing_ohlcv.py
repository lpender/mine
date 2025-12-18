#!/usr/bin/env python3
"""Identify and refetch missing OHLCV data for announcements.

This script fetches OHLCV bars starting from 5 minutes BEFORE each announcement
up to 120 minutes after (configurable). This allows for pre-announcement
price action analysis.
"""

import sys
import json
import os
from datetime import date, timedelta
from src.postgres_client import PostgresClient
from src.models import get_market_session

# File to track tickers that have no data available
NO_DATA_FILE = "data/no_ohlcv_data.json"


def load_no_data_set():
    """Load set of (ticker, timestamp) that have no data available."""
    if os.path.exists(NO_DATA_FILE):
        with open(NO_DATA_FILE, "r") as f:
            data = json.load(f)
            return set(tuple(x) for x in data)
    return set()


def save_no_data_set(no_data_set):
    """Save set of (ticker, timestamp) that have no data available."""
    os.makedirs(os.path.dirname(NO_DATA_FILE), exist_ok=True)
    with open(NO_DATA_FILE, "w") as f:
        json.dump(list(no_data_set), f, indent=2)


def get_missing_announcements(client, announcements, no_data_set):
    """Find announcements missing OHLCV data."""
    missing = []
    for ann in announcements:
        # Skip if already marked as no data
        key = (ann.ticker, ann.timestamp.isoformat())
        if key in no_data_set:
            continue

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

    return missing


def main():
    client = PostgresClient()
    announcements = client.load_announcements()
    no_data_set = load_no_data_set()

    print(f"Loaded {len(announcements)} announcements")
    print(f"Already marked as no-data: {len(no_data_set)}")

    loop_count = 0
    max_loops = 10  # Safety limit

    while loop_count < max_loops:
        loop_count += 1
        missing = get_missing_announcements(client, announcements, no_data_set)

        print(f"\n{'='*60}")
        print(f"Loop {loop_count}: Found {len(missing)} announcements missing OHLCV data")

        if not missing:
            print("All announcements have OHLCV data or are marked as unavailable!")
            break

        # Show first 10
        for ann in missing[:10]:
            print(f"  {ann.timestamp} {ann.ticker}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

        if len(sys.argv) > 1 and sys.argv[1] == "--refetch":
            print(f"\nRefetching OHLCV data...")
            success_count = 0
            no_data_count = 0

            for i, ann in enumerate(missing):
                print(f"[{i+1}/{len(missing)}] Fetching {ann.ticker} @ {ann.timestamp}...")
                bars = client.fetch_after_announcement(
                    ann.ticker,
                    ann.timestamp,
                    window_minutes=120,
                    use_cache=False  # Force refetch
                )
                if bars is None:
                    # Rate limit or network failure - will retry next loop
                    print(f"  -> Failed (will retry)")
                elif bars:
                    print(f"  -> Got {len(bars)} bars")
                    success_count += 1
                else:
                    # Empty list = API confirmed no data exists
                    print(f"  -> No data available (marking as unavailable)")
                    key = (ann.ticker, ann.timestamp.isoformat())
                    no_data_set.add(key)
                    no_data_count += 1
                    # Save periodically
                    if no_data_count % 10 == 0:
                        save_no_data_set(no_data_set)

            # Save after each loop
            save_no_data_set(no_data_set)
            print(f"\nLoop {loop_count} complete: {success_count} fetched, {no_data_count} marked unavailable")

            # If we got no successful fetches this loop, we're done
            if success_count == 0:
                print("No new data fetched this loop, stopping.")
                break
        else:
            print("\nRun with --refetch to fetch missing data")
            break

    print(f"\nFinal stats:")
    print(f"  Total announcements: {len(announcements)}")
    print(f"  Marked as no-data: {len(no_data_set)}")
    remaining = get_missing_announcements(client, announcements, no_data_set)
    print(f"  Still missing: {len(remaining)}")


if __name__ == "__main__":
    main()
