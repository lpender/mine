#!/usr/bin/env python3
"""Refetch OHLCV data for specific announcements using Polygon.

Usage:
    python scripts/refetch_with_polygon.py PAVS "2025-12-12 16:31:23.148"
    python scripts/refetch_with_polygon.py TICKER "YYYY-MM-DD HH:MM:SS.mmm"
"""

from datetime import datetime, timedelta
import sys
sys.path.insert(0, '.')

from src.massive_client import MassiveClient
from src.postgres_client import get_postgres_client
from src.data_providers import PolygonProvider

def refetch_announcement_data(ticker: str, timestamp_str: str, window_minutes: int = 120):
    """
    Refetch OHLCV data for an announcement using Polygon.

    Args:
        ticker: Stock ticker symbol
        timestamp_str: Announcement timestamp in ISO format
        window_minutes: Minutes of data to fetch after announcement
    """
    # Parse timestamp
    timestamp = datetime.fromisoformat(timestamp_str)
    print(f"Refetching OHLCV data for {ticker} at {timestamp} using Polygon...")
    print("=" * 80)

    # Initialize clients
    pg_client = get_postgres_client()
    polygon_provider = PolygonProvider()
    massive_client = MassiveClient(provider=polygon_provider)

    # Fetch data from Polygon
    print(f"\n1. Fetching data from Polygon...")
    print(f"   Window: 5 min before → {window_minutes} min after announcement")

    bars = massive_client.fetch_after_announcement(
        ticker=ticker,
        announcement_time=timestamp,
        window_minutes=window_minutes,
        pre_window_minutes=5,
    )

    if not bars:
        print("   ❌ No data returned from Polygon!")
        return False

    print(f"   ✓ Fetched {len(bars)} bars from Polygon")
    print(f"   Time range: {bars[0].timestamp} → {bars[-1].timestamp}")

    # Show sample of data
    print(f"\n2. Sample of Polygon data:")
    print(f"   {'Time':<20} {'Open':<12} {'Close':<12} {'Volume':<12}")
    print("   " + "-" * 56)
    for bar in bars[:5]:
        print(f"   {bar.timestamp.strftime('%Y-%m-%d %H:%M'):<20} "
              f"${bar.open:>10.4f} ${bar.close:>10.4f} {bar.volume:>10,}")
    if len(bars) > 5:
        print(f"   ... ({len(bars) - 5} more bars)")

    # Delete existing data
    print(f"\n3. Deleting existing OHLCV data for this announcement...")
    db = pg_client._get_db()
    try:
        from src.database import OHLCVBarDB

        deleted = db.query(OHLCVBarDB).filter(
            OHLCVBarDB.announcement_ticker == ticker,
            OHLCVBarDB.announcement_timestamp == timestamp
        ).delete()

        db.commit()
        print(f"   ✓ Deleted {deleted} existing bars")
    except Exception as e:
        db.rollback()
        print(f"   ❌ Error deleting: {e}")
        return False
    finally:
        db.close()

    # Save new data
    print(f"\n4. Saving Polygon data to database...")
    try:
        saved = pg_client.save_ohlcv_bars(
            ticker=ticker,
            bars=bars,
            announcement_ticker=ticker,
            announcement_timestamp=timestamp
        )
        print(f"   ✓ Saved {saved} bars")
    except Exception as e:
        print(f"   ❌ Error saving: {e}")
        return False

    # Update announcement status
    print(f"\n5. Updating announcement status...")
    try:
        db = pg_client._get_db()
        from src.database import AnnouncementDB

        ann = db.query(AnnouncementDB).filter(
            AnnouncementDB.ticker == ticker,
            AnnouncementDB.timestamp == timestamp
        ).first()

        if ann:
            ann.ohlcv_status = 'fetched'
            db.commit()
            print(f"   ✓ Updated announcement status to 'fetched'")
        else:
            print(f"   ⚠️  Announcement not found in database")
    except Exception as e:
        db.rollback()
        print(f"   ❌ Error updating status: {e}")
    finally:
        db.close()

    print("\n" + "=" * 80)
    print("✓ SUCCESS: OHLCV data refetched from Polygon")
    print("\nNext steps:")
    print("  - Refresh your dashboard to see the updated data")
    print("  - Consider unblacklisting this announcement if it was blacklisted")

    return True

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    ticker = sys.argv[1].upper()
    timestamp_str = sys.argv[2]

    # Optional: window minutes
    window_minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 120

    success = refetch_announcement_data(ticker, timestamp_str, window_minutes)

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
