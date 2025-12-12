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
import time
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.massive_client import MassiveClient


def _format_eta(seconds: float) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return "?"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def main():
    parser = argparse.ArgumentParser(description="Refetch OHLCV data for announcements")
    parser.add_argument("--extended", action="store_true", help="Only refetch extended hours (premarket + postmarket)")
    parser.add_argument("--premarket", action="store_true", help="Only refetch premarket announcements")
    parser.add_argument("--postmarket", action="store_true", help="Only refetch postmarket announcements")
    parser.add_argument("--ticker", "-t", type=str, help="Only refetch specific ticker")
    parser.add_argument("--window", type=int, default=120, help="OHLCV window in minutes (default: 120)")
    parser.add_argument("--cache-dir", type=str, default="data/ohlcv", help="Cache directory for parquet files (default: data/ohlcv)")
    parser.add_argument("--force", action="store_true", help="Force re-download (ignore cache)")
    parser.add_argument("--resume", action="store_true", help="Skip symbols that already have a cached parquet file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be refetched without fetching")
    args = parser.parse_args()

    client = MassiveClient(cache_dir=args.cache_dir)
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
    print(f"Cache dir: {Path(args.cache_dir).resolve()}")
    print(f"Window: {args.window} min | force={args.force} | resume={args.resume}")

    if args.dry_run:
        print("\n[DRY RUN] No data fetched.")
        sys.exit(0)

    # Delete existing cache files and refetch
    print("\nRefetching OHLCV data...")

    successful = 0
    failed = 0
    skipped = 0
    started = time.time()

    for i, ann in enumerate(to_refetch):
        progress = f"[{i+1}/{len(to_refetch)}]"

        # Determine the *actual* cache path (must match MassiveClient.fetch_after_announcement start_time logic)
        effective_start = client.get_effective_start_time(ann.timestamp)
        cache_path = client._get_cache_path(
            ann.ticker,
            effective_start,
            effective_start + timedelta(minutes=args.window),
        )

        if args.resume and cache_path.exists() and not args.force:
            skipped += 1
            elapsed = time.time() - started
            per_item = elapsed / max(1, (successful + failed + skipped))
            eta = per_item * (len(to_refetch) - (successful + failed + skipped))
            print(f"{progress} SKIP {ann.ticker:5} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')} ({ann.market_session}) | cached | elapsed={_format_eta(elapsed)} eta={_format_eta(eta)}")
            continue

        elapsed = time.time() - started
        per_item = elapsed / max(1, (successful + failed + skipped))
        eta = per_item * (len(to_refetch) - (successful + failed + skipped))
        print(
            f"{progress} Fetch {ann.ticker:5} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')} ({ann.market_session}) "
            f"| start={effective_start.strftime('%Y-%m-%d %H:%M')} "
            f"| elapsed={_format_eta(elapsed)} eta={_format_eta(eta)}",
            flush=True,
        )

        try:
            bars = client.fetch_after_announcement(
                ann.ticker,
                ann.timestamp,
                window_minutes=args.window,
                use_cache=not args.force,
            )
            if bars:
                first_bar = bars[0]
                last_bar = bars[-1]
                high = max(b.high for b in bars)
                low = min(b.low for b in bars)
                total_volume = sum(b.volume for b in bars)
                price_change_pct = ((high - ann.price_threshold) / ann.price_threshold) * 100

                print(f"      OK ({len(bars)} bars) -> {cache_path.name}")
                change_sign = "+" if price_change_pct >= 0 else ""
                print(f"       Open: ${first_bar.open:.2f} | High: ${high:.2f} ({change_sign}{price_change_pct:.1f}%) | Low: ${low:.2f} | Close: ${last_bar.close:.2f} | Vol: {total_volume:,}")
                successful += 1
            else:
                print("      No data")
                failed += 1
        except Exception as e:
            print(f"      Error: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Successful: {successful}")
    print(f"  Failed/No data: {failed}")
    print(f"  Skipped: {skipped}")


if __name__ == "__main__":
    main()
