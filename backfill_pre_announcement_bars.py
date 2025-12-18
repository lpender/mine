#!/usr/bin/env python3
"""
One-time script to backfill 5 minutes of pre-announcement OHLCV data.

This script fetches the missing 5 minutes of bars BEFORE each announcement
for all existing announcements in the database that already have OHLCV data.

Run this once after deploying the pre-fetch enhancement to fill in historical data.
"""

import sys
import os
from datetime import datetime, timedelta, date
from time import sleep
from src.postgres_client import PostgresClient
from src.models import get_market_session


def get_announcements_with_ohlcv(client):
    """Get all announcements that already have OHLCV data."""
    print("Loading announcements with existing OHLCV data...")

    # Load all announcements
    all_announcements = client.load_announcements()

    # Filter to only those with fetched OHLCV data
    with_ohlcv = [
        ann for ann in all_announcements
        if ann.ohlcv_status == 'fetched'
    ]

    print(f"Found {len(with_ohlcv)} announcements with OHLCV data (out of {len(all_announcements)} total)")
    return with_ohlcv


def get_first_bar_time(client, ticker, timestamp):
    """Get the timestamp of the first bar for an announcement."""
    from src.database import OHLCVBarDB, SessionLocal

    db = SessionLocal()
    try:
        first_bar = db.query(OHLCVBarDB).filter(
            OHLCVBarDB.announcement_ticker == ticker,
            OHLCVBarDB.announcement_timestamp == timestamp
        ).order_by(OHLCVBarDB.timestamp.asc()).first()

        return first_bar.timestamp if first_bar else None
    finally:
        db.close()


def needs_backfill(client, ann, pre_window_minutes=5):
    """Check if an announcement needs pre-announcement bars backfilled."""
    first_bar_time = get_first_bar_time(client, ann.ticker, ann.timestamp)

    if not first_bar_time:
        # No bars at all - shouldn't happen for fetched announcements
        return False

    # Calculate expected first bar time (5 minutes before announcement)
    expected_first = ann.timestamp - timedelta(minutes=pre_window_minutes)

    # If first bar is more than 2 minutes after expected start, we're missing pre-announcement data
    # (Allow 2 minute buffer for markets with no trading activity)
    time_diff = (first_bar_time - expected_first).total_seconds() / 60

    return time_diff > 2


def backfill_pre_announcement_bars(client, ann, pre_window_minutes=5, dry_run=False):
    """Fetch and store the missing pre-announcement bars for a single announcement."""
    from src.massive_client import MassiveClient

    # Calculate the time range to fetch (only the pre-announcement window)
    pre_start = ann.timestamp - timedelta(minutes=pre_window_minutes)
    pre_end = ann.timestamp

    if dry_run:
        print(f"  [DRY RUN] Would fetch {ann.ticker} from {pre_start} to {pre_end}")
        return None

    try:
        # Fetch bars directly using MassiveClient (bypasses cache to get fresh data)
        massive_client = MassiveClient()
        bars = massive_client.fetch_ohlcv(ann.ticker, pre_start, pre_end)

        if not bars:
            return 0

        # Store bars in database with announcement association
        client.save_ohlcv_bars(
            ann.ticker,
            bars,
            announcement_ticker=ann.ticker,
            announcement_timestamp=ann.timestamp
        )

        return len(bars)

    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def main():
    """Main backfill process."""
    # Parse command line arguments
    dry_run = "--dry-run" in sys.argv
    limit = None
    skip_check = "--skip-check" in sys.argv
    pre_window = 5  # Default: 5 minutes

    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
        elif arg.startswith("--pre-window="):
            pre_window = int(arg.split("=")[1])

    print("=" * 70)
    print("Pre-Announcement OHLCV Backfill Script")
    print("=" * 70)
    print(f"Pre-window: {pre_window} minutes")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if limit:
        print(f"Limit: {limit} announcements")
    if skip_check:
        print("Skip check: Backfilling ALL announcements (no timestamp check)")
    print("=" * 70)
    print()

    if not dry_run:
        response = input("This will modify the database. Continue? (yes/no): ")
        if response.lower() != "yes":
            print("Aborted.")
            return
        print()

    client = PostgresClient()

    # Get announcements with OHLCV data
    announcements = get_announcements_with_ohlcv(client)

    if not announcements:
        print("No announcements with OHLCV data found.")
        return

    # Filter announcements that need backfill
    print("Checking which announcements need backfill...")
    to_backfill = []

    for i, ann in enumerate(announcements):
        if limit and i >= limit:
            break

        if i % 100 == 0:
            print(f"  Checked {i}/{len(announcements)}...")

        if skip_check or needs_backfill(client, ann, pre_window):
            to_backfill.append(ann)

    print(f"\nFound {len(to_backfill)} announcements needing backfill")

    if not to_backfill:
        print("Nothing to backfill!")
        return

    # Process backfill
    print(f"\nStarting backfill...")
    print("=" * 70)

    success_count = 0
    no_data_count = 0
    error_count = 0
    total_bars = 0

    for i, ann in enumerate(to_backfill):
        progress = f"[{i+1}/{len(to_backfill)}]"
        session = get_market_session(ann.timestamp)
        timestamp_str = ann.timestamp.strftime("%Y-%m-%d %H:%M")

        print(f"{progress} {ann.ticker} @ {timestamp_str} ({session})")

        result = backfill_pre_announcement_bars(client, ann, pre_window, dry_run)

        if result is None:
            error_count += 1
            status = "ERROR"
        elif result == 0:
            no_data_count += 1
            status = "no pre-announcement data"
        else:
            success_count += 1
            total_bars += result
            status = f"added {result} bars"

        print(f"  -> {status}")

        # Rate limiting (respect provider limits)
        if not dry_run and (i + 1) % 5 == 0:
            sleep(1)  # 5 requests per second max

    print()
    print("=" * 70)
    print("Backfill Complete")
    print("=" * 70)
    print(f"Total announcements processed: {len(to_backfill)}")
    print(f"Success: {success_count}")
    print(f"No pre-data: {no_data_count}")
    print(f"Errors: {error_count}")
    print(f"Total bars added: {total_bars}")
    print()

    if dry_run:
        print("This was a DRY RUN. No data was modified.")
        print("Run without --dry-run to actually backfill data.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("\nUsage:")
        print("  python backfill_pre_announcement_bars.py [options]")
        print("\nOptions:")
        print("  --dry-run              Show what would be done without modifying data")
        print("  --limit=N              Only process first N announcements")
        print("  --pre-window=N         Fetch N minutes before (default: 5)")
        print("  --skip-check           Skip timestamp check, backfill all announcements")
        print("  -h, --help             Show this help message")
        print("\nExamples:")
        print("  # Dry run to see what would happen")
        print("  python backfill_pre_announcement_bars.py --dry-run")
        print()
        print("  # Process first 10 announcements")
        print("  python backfill_pre_announcement_bars.py --limit=10")
        print()
        print("  # Fetch 10 minutes of pre-announcement data")
        print("  python backfill_pre_announcement_bars.py --pre-window=10")
        print()
        print("  # Full backfill (LIVE)")
        print("  python backfill_pre_announcement_bars.py")
        sys.exit(0)

    main()

