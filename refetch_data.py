#!/usr/bin/env python3
"""
Refetch OHLCV data for announcements.

Usage:
    python refetch_data.py                    # Refetch all
    python refetch_data.py --extended         # Only premarket + postmarket
    python refetch_data.py --premarket        # Only premarket
    python refetch_data.py --postmarket       # Only postmarket
    python refetch_data.py --ticker AAPL      # Specific ticker
    python refetch_data.py --dry-run          # Show what would be refetched
"""

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.massive_client import MassiveClient


def main():
    parser = argparse.ArgumentParser(description="Refetch OHLCV data for announcements")
    parser.add_argument("--extended", action="store_true", help="Only refetch extended hours (premarket + postmarket)")
    parser.add_argument("--premarket", action="store_true", help="Only refetch premarket announcements")
    parser.add_argument("--postmarket", action="store_true", help="Only refetch postmarket announcements")
    parser.add_argument("--ticker", "-t", type=str, help="Only refetch specific ticker")
    parser.add_argument("--window", type=int, default=120, help="OHLCV window in minutes (default: 120)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be refetched without fetching")
    args = parser.parse_args()

    client = MassiveClient()
    announcements = client.load_announcements()

    if not announcements:
        print("No announcements found.")
        sys.exit(0)

    # Determine which sessions to refetch
    sessions = None  # None means all sessions
    if args.premarket and args.postmarket:
        sessions = ["premarket", "postmarket"]
    elif args.premarket:
        sessions = ["premarket"]
    elif args.postmarket:
        sessions = ["postmarket"]
    elif args.extended:
        sessions = ["premarket", "postmarket"]

    # Filter announcements
    to_refetch = []
    for ann in announcements:
        # Filter by session if specified
        if sessions and ann.market_session not in sessions:
            continue

        # Filter by ticker if specified
        if args.ticker and ann.ticker.upper() != args.ticker.upper():
            continue

        to_refetch.append(ann)

    if not to_refetch:
        print(f"No announcements found")
        if sessions:
            print(f"  Sessions filter: {sessions}")
        if args.ticker:
            print(f"  Ticker filter: {args.ticker}")
        sys.exit(0)

    session_desc = f" ({', '.join(sessions)})" if sessions else ""
    print(f"Announcements to refetch{session_desc}: {len(to_refetch)}")
    for ann in to_refetch:
        print(f"  {ann.ticker:5} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')} ({ann.market_session})")

    if args.dry_run:
        print("\n[DRY RUN] No data fetched.")
        sys.exit(0)

    # Delete existing cache files and refetch
    print(f"\nRefetching OHLCV data (window: {args.window} min)...")

    successful = 0
    failed = 0

    for i, ann in enumerate(to_refetch):
        progress = f"[{i+1}/{len(to_refetch)}]"
        print(f"{progress} Refetching {ann.ticker} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')}...", end=" ", flush=True)

        # Delete existing cache file
        cache_path = client._get_cache_path(
            ann.ticker,
            ann.timestamp,
            ann.timestamp + timedelta(minutes=args.window)
        )
        if cache_path.exists():
            cache_path.unlink()

        try:
            bars = client.fetch_after_announcement(
                ann.ticker,
                ann.timestamp,
                window_minutes=args.window,
            )
            if bars:
                first_bar = bars[0]
                last_bar = bars[-1]
                high = max(b.high for b in bars)
                low = min(b.low for b in bars)
                total_volume = sum(b.volume for b in bars)
                price_change_pct = ((high - ann.price_threshold) / ann.price_threshold) * 100

                print(f"OK ({len(bars)} bars)")
                change_sign = "+" if price_change_pct >= 0 else ""
                print(f"       Open: ${first_bar.open:.2f} | High: ${high:.2f} ({change_sign}{price_change_pct:.1f}%) | Low: ${low:.2f} | Close: ${last_bar.close:.2f} | Vol: {total_volume:,}")
                successful += 1
            else:
                print("No data")
                failed += 1
        except Exception as e:
            print(f"Error: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Successful: {successful}")
    print(f"  Failed/No data: {failed}")


if __name__ == "__main__":
    main()
