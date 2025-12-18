#!/usr/bin/env python3
"""Compare PAVS OHLCV data from database (Alpaca) vs Polygon (Massive)."""

from datetime import datetime
import sys
sys.path.insert(0, '.')

from src.massive_client import MassiveClient
from src.postgres_client import get_postgres_client
from src.data_providers import PolygonProvider

# PAVS announcement with reverse split issue
TICKER = "PAVS"
ANNOUNCEMENT_TIME = datetime.fromisoformat("2025-12-12T16:31:23.148")  # UTC

def main():
    print(f"Comparing OHLCV data for {TICKER} at {ANNOUNCEMENT_TIME}")
    print("=" * 80)

    # Fetch from database (Alpaca data)
    print("\n1. Fetching data from DATABASE (Alpaca)...")
    pg_client = get_postgres_client()
    start_time = datetime.fromisoformat("2025-12-12T16:27:00")
    end_time = datetime.fromisoformat("2025-12-12T16:50:00")

    db_bars = pg_client.get_ohlcv_bars(TICKER, start_time, end_time)
    print(f"   Found {len(db_bars)} bars in database")

    # Fetch from Polygon
    print("\n2. Fetching data from POLYGON (Massive)...")
    polygon_provider = PolygonProvider()
    massive_client = MassiveClient(provider=polygon_provider)

    polygon_bars = massive_client.fetch_ohlcv(TICKER, start_time, end_time)
    print(f"   Found {len(polygon_bars)} bars from Polygon")

    # Compare
    print("\n3. COMPARISON:")
    print("-" * 80)
    print(f"{'Time':<20} {'DB Open':<12} {'DB Close':<12} {'PG Open':<12} {'PG Close':<12} {'Match':<8}")
    print("-" * 80)

    # Create dict for easy lookup
    polygon_dict = {bar.timestamp: bar for bar in polygon_bars}

    mismatches = 0
    for db_bar in db_bars[:30]:  # Show first 30 bars
        pg_bar = polygon_dict.get(db_bar.timestamp)

        if pg_bar:
            match = (abs(db_bar.open - pg_bar.open) < 0.001 and
                    abs(db_bar.close - pg_bar.close) < 0.001)
            match_str = "✓" if match else "✗ DIFF"
            if not match:
                mismatches += 1

            print(f"{db_bar.timestamp.strftime('%Y-%m-%d %H:%M')} "
                  f"${db_bar.open:>10.4f} ${db_bar.close:>10.4f} "
                  f"${pg_bar.open:>10.4f} ${pg_bar.close:>10.4f} "
                  f"{match_str:<8}")
        else:
            print(f"{db_bar.timestamp.strftime('%Y-%m-%d %H:%M')} "
                  f"${db_bar.open:>10.4f} ${db_bar.close:>10.4f} "
                  f"{'N/A':>12} {'N/A':>12} "
                  f"MISSING")
            mismatches += 1

    print("-" * 80)
    print(f"\nSummary:")
    print(f"  Database bars: {len(db_bars)}")
    print(f"  Polygon bars:  {len(polygon_bars)}")
    print(f"  Mismatches:    {mismatches}")

    # Show the critical reverse split candle
    print("\n4. CRITICAL CANDLE (16:31 - reverse split):")
    print("-" * 80)
    split_time = datetime.fromisoformat("2025-12-12T16:31:00")

    db_split = next((b for b in db_bars if b.timestamp == split_time), None)
    pg_split = polygon_dict.get(split_time)

    if db_split:
        print(f"Database (Alpaca): Open=${db_split.open:.4f}, Close=${db_split.close:.4f}, Volume={db_split.volume}")
    else:
        print("Database (Alpaca): NOT FOUND")

    if pg_split:
        print(f"Polygon (Massive): Open=${pg_split.open:.4f}, Close=${pg_split.close:.4f}, Volume={pg_split.volume}")
    else:
        print("Polygon (Massive): NOT FOUND")

if __name__ == "__main__":
    main()
