#!/usr/bin/env python3
"""Bulk refetch all PAVS announcements from Polygon."""

import sys
sys.path.insert(0, '.')

from datetime import datetime
from src.postgres_client import get_postgres_client
from refetch_with_polygon import refetch_announcement_data

def main():
    pg_client = get_postgres_client()

    # Get all PAVS announcements from backfill source
    print("Finding all PAVS announcements...")
    db = pg_client._get_db()
    try:
        from src.database import AnnouncementDB

        announcements = db.query(AnnouncementDB).filter(
            AnnouncementDB.ticker == 'PAVS',
            AnnouncementDB.source == 'backfill'
        ).order_by(AnnouncementDB.timestamp).all()

        print(f"Found {len(announcements)} PAVS announcements")
        print("=" * 80)

        success_count = 0
        skip_count = 0
        fail_count = 0

        for i, ann in enumerate(announcements, 1):
            print(f"\n[{i}/{len(announcements)}] Processing {ann.ticker} at {ann.timestamp}")

            # Skip if already has fetched status (optional - remove if you want to force refetch)
            # if ann.ohlcv_status == 'fetched':
            #     print("   ⏭️  Skipping - already fetched")
            #     skip_count += 1
            #     continue

            try:
                success = refetch_announcement_data(
                    ticker=ann.ticker,
                    timestamp_str=ann.timestamp.isoformat(),
                    window_minutes=120
                )

                if success:
                    success_count += 1
                else:
                    fail_count += 1

            except Exception as e:
                print(f"   ❌ Error: {e}")
                fail_count += 1

            print("-" * 80)

        print("\n" + "=" * 80)
        print(f"SUMMARY:")
        print(f"  Total:   {len(announcements)}")
        print(f"  Success: {success_count}")
        print(f"  Skipped: {skip_count}")
        print(f"  Failed:  {fail_count}")

    finally:
        db.close()

if __name__ == "__main__":
    main()
