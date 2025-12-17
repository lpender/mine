#!/usr/bin/env python3
"""Backfill OHLCV data for announcements with incomplete windows in overlapping groups."""

import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path

# Add project root to path for proper imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.postgres_client import PostgresClient
from src.massive_client import MassiveClient


def find_incomplete_announcements(client: PostgresClient, massive: MassiveClient,
                                   window_minutes: int = 120):
    """Find announcements with incomplete OHLCV data in overlapping groups."""
    announcements = client.load_announcements()
    print(f"Loaded {len(announcements)} total announcements")

    # Group by ticker
    by_ticker = defaultdict(list)
    for ann in announcements:
        by_ticker[ann.ticker].append(ann)

    # Sort each ticker's announcements by timestamp
    for ticker in by_ticker:
        by_ticker[ticker].sort(key=lambda a: a.timestamp)

    # Find overlapping groups and incomplete data
    issues = []
    window = timedelta(minutes=window_minutes)

    for ticker, anns in by_ticker.items():
        if len(anns) < 2:
            continue

        # Find groups of announcements within window_minutes of each other
        i = 0
        while i < len(anns):
            group = [anns[i]]
            j = i + 1

            while j < len(anns):
                if (anns[j].timestamp - group[-1].timestamp) <= window:
                    group.append(anns[j])
                    j += 1
                else:
                    break

            if len(group) > 1:
                # Check each announcement in the group
                for ann in group:
                    effective_start = massive.get_effective_start_time(ann.timestamp)
                    end_time = effective_start + timedelta(minutes=window_minutes)

                    # Skip if window is today or in the future
                    if effective_start.date() >= date.today():
                        continue

                    has_data = client.has_ohlcv_data(ticker, effective_start, end_time)
                    if not has_data:
                        bars = client.get_ohlcv_bars(ticker, effective_start, end_time)
                        issues.append({
                            "ticker": ticker,
                            "timestamp": ann.timestamp,
                            "effective_start": effective_start,
                            "end_time": end_time,
                            "bar_count": len(bars),
                            "announcement": ann,
                        })

            i = j if j > i + 1 else i + 1

    return issues


def backfill_ohlcv(issues: list, client: PostgresClient, massive: MassiveClient,
                   dry_run: bool = False):
    """Backfill OHLCV data for issues."""
    # Deduplicate by (ticker, effective_start, end_time) to avoid redundant fetches
    unique_windows = {}
    for issue in issues:
        key = (issue['ticker'], issue['effective_start'], issue['end_time'])
        if key not in unique_windows:
            unique_windows[key] = issue

    print(f"\n{len(unique_windows)} unique windows to fetch (from {len(issues)} issues)")

    if dry_run:
        print("\n[DRY RUN] Would fetch these windows:")
        for key, issue in sorted(unique_windows.items(), key=lambda x: (x[0][0], x[0][1])):
            ticker, start, end = key
            print(f"  {ticker}: {start.strftime('%Y-%m-%d %H:%M')} -> {end.strftime('%H:%M')} "
                  f"(currently {issue['bar_count']} bars)")
        return

    # Fetch data for each unique window
    rate_limit_delay = massive.rate_limit_delay
    fetched = 0
    skipped = 0
    failed = 0

    for i, (key, issue) in enumerate(sorted(unique_windows.items(), key=lambda x: (x[0][0], x[0][1]))):
        ticker, start, end = key

        print(f"[{i+1}/{len(unique_windows)}] Fetching {ticker} {start.strftime('%Y-%m-%d %H:%M')} -> {end.strftime('%H:%M')}...")

        try:
            # Fetch from provider
            bars = massive.fetch_ohlcv(ticker, start, end)

            if bars:
                # Save to database
                new_count = client.save_ohlcv_bars(
                    ticker, bars,
                    announcement_ticker=issue['announcement'].ticker,
                    announcement_timestamp=issue['announcement'].timestamp
                )
                print(f"  Fetched {len(bars)} bars, saved {new_count} new")
                fetched += 1
            else:
                print(f"  No bars returned")
                skipped += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

        # Rate limiting
        if i < len(unique_windows) - 1:
            time.sleep(rate_limit_delay)

    print(f"\nDone: {fetched} fetched, {skipped} skipped (no data), {failed} failed")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill OHLCV data for overlapping announcements")
    parser.add_argument('--window', type=int, default=120, help='Window in minutes')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show what would be fetched without fetching')
    args = parser.parse_args()

    client = PostgresClient()
    massive = MassiveClient()

    print(f"Finding announcements with incomplete OHLCV data (window={args.window} min)...")
    issues = find_incomplete_announcements(client, massive, args.window)

    if not issues:
        print("No incomplete announcements found!")
        return

    # Categorize
    zero_bars = [i for i in issues if i['bar_count'] == 0]
    partial_bars = [i for i in issues if i['bar_count'] > 0]

    print(f"\nFound {len(issues)} announcements with incomplete data:")
    print(f"  {len(zero_bars)} with 0 bars")
    print(f"  {len(partial_bars)} with partial data")

    backfill_ohlcv(issues, client, massive, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
