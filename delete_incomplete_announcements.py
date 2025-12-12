#!/usr/bin/env python3
"""Delete announcements with missing source_message or UNKNOWN country."""

import sys
from src.database import SessionLocal, AnnouncementDB, OHLCVBarDB
from sqlalchemy import or_


def main():
    db = SessionLocal()

    try:
        # Find incomplete announcements
        incomplete = db.query(AnnouncementDB).filter(
            or_(
                AnnouncementDB.source_message == None,
                AnnouncementDB.country == 'UNKNOWN'
            )
        ).all()

        print(f"Found {len(incomplete)} incomplete announcements:")
        for ann in incomplete[:10]:
            print(f"  {ann.timestamp} {ann.ticker} country={ann.country!r}")
        if len(incomplete) > 10:
            print(f"  ... and {len(incomplete) - 10} more")

        if not incomplete:
            print("Nothing to delete!")
            return

        if len(sys.argv) > 1 and sys.argv[1] == "--confirm":
            # Delete associated OHLCV bars first
            deleted_bars = 0
            for ann in incomplete:
                count = db.query(OHLCVBarDB).filter(
                    OHLCVBarDB.announcement_ticker == ann.ticker,
                    OHLCVBarDB.announcement_timestamp == ann.timestamp
                ).delete()
                deleted_bars += count

            # Delete the announcements
            for ann in incomplete:
                db.delete(ann)

            db.commit()
            print(f"\nDeleted {len(incomplete)} announcements and {deleted_bars} associated OHLCV bars")
        else:
            print("\nRun with --confirm to delete these records")

    finally:
        db.close()


if __name__ == "__main__":
    main()
