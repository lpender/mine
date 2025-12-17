#!/usr/bin/env python3
"""Find announcements on the same stock within 120 minutes and check OHLCV completeness."""

import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

# Add project root to path for proper imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.postgres_client import PostgresClient
from src.massive_client import MassiveClient


def find_overlapping_announcements(window_minutes: int = 120):
    """Find all pairs of announcements on the same stock within window_minutes of each other."""
    client = PostgresClient()
    massive = MassiveClient()

    # Load all announcements
    announcements = client.load_announcements()
    print(f"Loaded {len(announcements)} total announcements")

    # Group by ticker
    by_ticker = defaultdict(list)
    for ann in announcements:
        by_ticker[ann.ticker].append(ann)

    # Sort each ticker's announcements by timestamp
    for ticker in by_ticker:
        by_ticker[ticker].sort(key=lambda a: a.timestamp)

    # Find overlapping groups
    overlapping_groups = []
    window = timedelta(minutes=window_minutes)

    for ticker, anns in by_ticker.items():
        if len(anns) < 2:
            continue

        # Find groups of announcements within window_minutes of each other
        i = 0
        while i < len(anns):
            group = [anns[i]]
            j = i + 1

            # Keep adding announcements that are within window of any in group
            while j < len(anns):
                # Check if this announcement is within window of the previous one
                if (anns[j].timestamp - group[-1].timestamp) <= window:
                    group.append(anns[j])
                    j += 1
                else:
                    break

            if len(group) > 1:
                overlapping_groups.append((ticker, group))

            i = j if j > i + 1 else i + 1

    print(f"\nFound {len(overlapping_groups)} groups of overlapping announcements")
    print("=" * 80)

    # Check OHLCV data for each group
    issues = []

    for ticker, group in overlapping_groups:
        print(f"\n{ticker}: {len(group)} announcements within {window_minutes} minutes")

        for ann in group:
            # Get effective start time (handles market hours logic)
            effective_start = massive.get_effective_start_time(ann.timestamp)
            end_time = effective_start + timedelta(minutes=window_minutes)

            # Check OHLCV data
            has_data = client.has_ohlcv_data(ticker, effective_start, end_time)
            bars = client.get_ohlcv_bars(ticker, effective_start, end_time)

            status = "OK" if has_data else "MISSING"

            print(f"  {ann.timestamp.strftime('%Y-%m-%d %H:%M')} UTC -> "
                  f"effective {effective_start.strftime('%H:%M')} ET | "
                  f"{len(bars)} bars | {status}")

            if not has_data:
                issues.append({
                    "ticker": ticker,
                    "timestamp": ann.timestamp,
                    "effective_start": effective_start,
                    "end_time": end_time,
                    "bar_count": len(bars),
                })

    # Summary
    print("\n" + "=" * 80)
    print(f"SUMMARY: {len(issues)} announcements with incomplete OHLCV data out of {sum(len(g[1]) for g in overlapping_groups)} in overlapping groups")

    # Categorize issues
    zero_bars = [i for i in issues if i['bar_count'] == 0]
    partial_bars = [i for i in issues if i['bar_count'] > 0]

    if zero_bars:
        print(f"\n--- {len(zero_bars)} with 0 bars (likely no data available) ---")
        by_ticker = {}
        for issue in zero_bars:
            by_ticker.setdefault(issue['ticker'], []).append(issue)
        for ticker, ticker_issues in sorted(by_ticker.items()):
            print(f"  {ticker}: {len(ticker_issues)} announcements")

    if partial_bars:
        print(f"\n--- {len(partial_bars)} with partial data (window not fully covered) ---")
        for issue in partial_bars:
            print(f"  {issue['ticker']} @ {issue['timestamp'].strftime('%Y-%m-%d %H:%M')} UTC: "
                  f"{issue['bar_count']} bars (expected ~120)")

    return overlapping_groups, issues


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', type=int, default=120, help='Window in minutes')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show all groups')
    args = parser.parse_args()

    if not args.verbose:
        # Quieter output - just show issues
        import sys
        from io import StringIO

        # Capture output
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        groups, issues = find_overlapping_announcements(args.window)

        # Get captured output and restore
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout

        # Only print summary section
        lines = output.split('\n')
        summary_started = False
        for line in lines:
            if 'SUMMARY:' in line or summary_started:
                summary_started = True
                print(line)
    else:
        find_overlapping_announcements(args.window)
