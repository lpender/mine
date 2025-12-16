#!/usr/bin/env python3
"""Backfill headline_is_financing from source_message for existing announcements."""

import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from src.database import engine, init_db
from src.features import classify_headline


def backfill_headline_classification():
    """Update headline classification for all announcements with source_message."""
    init_db()

    with engine.connect() as conn:
        # Get all announcements with source_message
        result = conn.execute(text(
            "SELECT id, source_message, headline_is_financing, headline_financing_type FROM announcements WHERE source_message IS NOT NULL"
        ))
        rows = result.fetchall()

        updated = 0
        skipped = 0
        already_classified = 0

        for row in rows:
            ann_id, source_message, current_is_financing, current_type = row

            # Skip if already classified (unless we want to re-classify)
            # For now, re-classify everything to ensure consistency
            flags = classify_headline(source_message)

            if flags.is_financing != current_is_financing or flags.financing_type != current_type:
                tags_str = ",".join(flags.tags) if flags.tags else None
                conn.execute(
                    text("""
                        UPDATE announcements
                        SET headline_is_financing = :is_financing,
                            headline_financing_type = :fin_type,
                            headline_financing_tags = :tags
                        WHERE id = :id
                    """),
                    {
                        "is_financing": flags.is_financing,
                        "fin_type": flags.financing_type,
                        "tags": tags_str,
                        "id": ann_id,
                    }
                )
                updated += 1
                if flags.is_financing:
                    print(f"ID {ann_id}: {flags.financing_type} - {flags.tags}")
            else:
                if current_is_financing:
                    already_classified += 1
                else:
                    skipped += 1

        conn.commit()

        print(f"\nDone! Updated {updated} announcements")
        print(f"Already classified: {already_classified}")
        print(f"Skipped (no financing detected): {skipped}")


if __name__ == "__main__":
    backfill_headline_classification()
