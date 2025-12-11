#!/usr/bin/env python3
"""
Import Discord HTML file and fetch OHLCV data.

Usage:
    python import_html.py <html_file> --channel <name> [--include-today] [--window MINUTES]

Session filtering (for OHLCV fetch only - all announcements are always saved):
    --extended        Only fetch OHLCV for premarket + postmarket (default)
    --premarket       Only fetch OHLCV for premarket
    --postmarket      Only fetch OHLCV for postmarket
    --all-sessions    Fetch OHLCV for all sessions (including closed hours)

Examples:
    python import_html.py discord_messages.html --channel select-news
    python import_html.py discord_messages.html -c pr-spike --include-today
    python import_html.py discord_messages.html -c select-news --all-sessions
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
    parser.add_argument("--channel", "-c", type=str, required=True,
                        help="Channel name to tag announcements with (e.g., select-news)")
    parser.add_argument("--extended", action="store_true",
                        help="Only fetch OHLCV for extended hours (premarket + postmarket) - this is the default")
    parser.add_argument("--premarket", action="store_true",
                        help="Only fetch OHLCV for premarket announcements")
    parser.add_argument("--postmarket", action="store_true",
                        help="Only fetch OHLCV for postmarket announcements")
    parser.add_argument("--all-sessions", action="store_true",
                        help="Fetch OHLCV for all sessions (including market hours and closed)")
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

    # Set channel on all announcements
    for ann in announcements:
        ann.channel = args.channel

    print(f"\nAnnouncements to import ({len(announcements)}):")
    for ann in announcements:
        float_str = f"{ann.float_shares/1e6:.1f}M" if ann.float_shares else "N/A"
        io_str = f"{ann.io_percent:.1f}%" if ann.io_percent is not None else "N/A"
        mc_str = f"${ann.market_cap/1e6:.1f}M" if ann.market_cap else "N/A"
        flags = []
        if ann.high_ctb:
            flags.append("CTB")
        if ann.reg_sho:
            flags.append("RegSHO")
        if ann.short_interest:
            flags.append(f"SI:{ann.short_interest:.1f}%")
        flags_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {ann.ticker:5} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')} | ${ann.price_threshold:.2f} | {ann.country} | Float: {float_str} | IO: {io_str} | MC: {mc_str}{flags_str}")

    # Load existing data and merge (clobber metadata for existing announcements)
    client = MassiveClient()
    existing_announcements = client.load_announcements()
    existing_by_key = {(a.ticker, a.timestamp): a for a in existing_announcements}

    # Merge: update existing announcements with new metadata, add new ones
    new_count = 0
    updated_count = 0
    for ann in announcements:
        key = (ann.ticker, ann.timestamp)
        if key in existing_by_key:
            # Clobber: update all metadata fields
            existing_by_key[key] = ann
            updated_count += 1
        else:
            existing_by_key[key] = ann
            new_count += 1

    all_announcements = list(existing_by_key.values())

    print(f"\nAnnouncements: {new_count} new, {updated_count} updated")

    # Save announcements FIRST (before OHLCV fetch which can be slow/interrupted)
    client.save_announcements(all_announcements)
    print(f"Saved {len(all_announcements)} total announcements to cache")

    # Determine which sessions to fetch OHLCV for
    # Default: extended hours only (premarket + postmarket)
    if args.all_sessions:
        ohlcv_sessions = None  # None means all sessions
    elif args.premarket and args.postmarket:
        ohlcv_sessions = ["premarket", "postmarket"]
    elif args.premarket:
        ohlcv_sessions = ["premarket"]
    elif args.postmarket:
        ohlcv_sessions = ["postmarket"]
    else:
        # Default: extended hours (premarket + postmarket)
        ohlcv_sessions = ["premarket", "postmarket"]

    # Check which announcements need OHLCV data (no cached parquet file + matching session)
    announcements_needing_ohlcv = []
    skipped_by_session = 0
    for ann in announcements:
        # Filter by session if specified
        if ohlcv_sessions and ann.market_session not in ohlcv_sessions:
            skipped_by_session += 1
            continue

        cache_path = client._get_cache_path(
            ann.ticker,
            ann.timestamp,
            ann.timestamp + timedelta(minutes=args.window)
        )
        if not cache_path.exists():
            announcements_needing_ohlcv.append(ann)

    if skipped_by_session > 0:
        session_desc = ", ".join(ohlcv_sessions) if ohlcv_sessions else "all"
        print(f"Skipped {skipped_by_session} announcements outside {session_desc} sessions")

    if not announcements_needing_ohlcv:
        print(f"\nAll OHLCV data already cached.")
        sys.exit(0)

    # Fetch OHLCV data
    print(f"\nFetching OHLCV data for {len(announcements_needing_ohlcv)} announcements (window: {args.window} min)...")

    successful = 0
    failed = 0
    results = []

    for i, ann in enumerate(announcements_needing_ohlcv):
        progress = f"[{i+1}/{len(announcements_needing_ohlcv)}]"
        print(f"{progress} Fetching {ann.ticker} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')}...", end=" ", flush=True)

        try:
            bars = client.fetch_after_announcement(
                ann.ticker,
                ann.timestamp,
                window_minutes=args.window,
            )
            if bars:
                # Validate OHLCV data
                first_bar = bars[0]
                last_bar = bars[-1]
                high = max(b.high for b in bars)
                low = min(b.low for b in bars)
                total_volume = sum(b.volume for b in bars)

                # Calculate price range from announcement price
                price_change_pct = ((high - ann.price_threshold) / ann.price_threshold) * 100

                print(f"OK ({len(bars)} bars)")
                change_sign = "+" if price_change_pct >= 0 else ""
                print(f"       Open: ${first_bar.open:.2f} | High: ${high:.2f} ({change_sign}{price_change_pct:.1f}%) | Low: ${low:.2f} | Close: ${last_bar.close:.2f} | Vol: {total_volume:,}")

                results.append({
                    "ticker": ann.ticker,
                    "bars": len(bars),
                    "open": first_bar.open,
                    "high": high,
                    "low": low,
                    "close": last_bar.close,
                    "volume": total_volume,
                    "change_pct": price_change_pct,
                })
                successful += 1
            else:
                print("No data")
                results.append({"ticker": ann.ticker, "bars": 0, "error": "No data returned"})
                failed += 1
        except Exception as e:
            print(f"Error: {e}")
            results.append({"ticker": ann.ticker, "bars": 0, "error": str(e)})
            failed += 1

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Successful: {successful}")
    print(f"  Failed/No data: {failed}")
    print(f"  Total announcements saved: {len(all_announcements)}")

    if successful > 0:
        valid_results = [r for r in results if r.get("bars", 0) > 0]
        avg_bars = sum(r["bars"] for r in valid_results) / len(valid_results)
        avg_change = sum(r["change_pct"] for r in valid_results) / len(valid_results)
        print(f"  Avg bars per ticker: {avg_bars:.0f}")
        print(f"  Avg max gain from threshold: {avg_change:.1f}%")


if __name__ == "__main__":
    main()
