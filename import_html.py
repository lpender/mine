#!/usr/bin/env python3
"""
Import Discord HTML file and fetch OHLCV data.

Usage:
    python import_html.py <html_file> [--include-today] [--window MINUTES]

Examples:
    python import_html.py discord_messages.html
    python import_html.py discord_messages.html --include-today
    python import_html.py discord_messages.html --window 60
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.parser import parse_discord_html_with_stats
from src.massive_client import MassiveClient


def main():
    parser = argparse.ArgumentParser(description="Import Discord HTML and fetch OHLCV data")
    parser.add_argument("html_file", help="Path to Discord HTML file")
    parser.add_argument("--include-today", action="store_true",
                        help="Include today's messages (normally excluded)")
    parser.add_argument("--window", type=int, default=120,
                        help="OHLCV window in minutes (default: 120)")
    args = parser.parse_args()

    html_path = Path(args.html_file)
    if not html_path.exists():
        print(f"Error: File not found: {html_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {html_path}...")
    html_content = html_path.read_text()

    # Parse HTML
    cutoff = datetime(2099, 12, 31) if args.include_today else None
    announcements, stats = parse_discord_html_with_stats(html_content, cutoff)

    print(f"\nParse Results:")
    print(f"  Total messages found: {stats['total_messages']}")
    print(f"  Filtered (today): {stats['filtered_by_cutoff']}")
    print(f"  Not ticker pattern: {stats['not_ticker_pattern']}")
    print(f"  Parsed: {stats['parsed']}")

    if stats.get("error"):
        print(f"\nError: {stats['error']}", file=sys.stderr)
        sys.exit(1)

    if not announcements:
        print("\nNo announcements to import.")
        if stats['filtered_by_cutoff'] > 0:
            print("Tip: Use --include-today to include today's messages")
        sys.exit(0)

    print(f"\nAnnouncements to import ({len(announcements)}):")
    for ann in announcements[:10]:
        print(f"  {ann.ticker:5} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')} - ${ann.price_threshold:.2f}")
    if len(announcements) > 10:
        print(f"  ... and {len(announcements) - 10} more")

    # Load existing data
    client = MassiveClient()
    existing_announcements = client.load_announcements()
    existing_keys = {(a.ticker, a.timestamp) for a in existing_announcements}

    # Find new announcements
    new_announcements = [
        ann for ann in announcements
        if (ann.ticker, ann.timestamp) not in existing_keys
    ]

    if not new_announcements:
        print(f"\nAll {len(announcements)} announcements already imported.")
        sys.exit(0)

    print(f"\nNew announcements to fetch: {len(new_announcements)}")

    # Fetch OHLCV data
    print(f"\nFetching OHLCV data (window: {args.window} min)...")

    successful = 0
    failed = 0

    for i, ann in enumerate(new_announcements):
        progress = f"[{i+1}/{len(new_announcements)}]"
        print(f"{progress} Fetching {ann.ticker} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')}...", end=" ", flush=True)

        try:
            bars = client.fetch_after_announcement(
                ann.ticker,
                ann.timestamp,
                window_minutes=args.window,
            )
            if bars:
                print(f"OK ({len(bars)} bars)")
                successful += 1
            else:
                print("No data")
                failed += 1
        except Exception as e:
            print(f"Error: {e}")
            failed += 1

    # Save announcements
    all_announcements = existing_announcements + new_announcements
    client.save_announcements(all_announcements)

    print(f"\nDone!")
    print(f"  Successful: {successful}")
    print(f"  Failed/No data: {failed}")
    print(f"  Total announcements saved: {len(all_announcements)}")


if __name__ == "__main__":
    main()
