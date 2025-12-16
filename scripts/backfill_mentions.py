#!/usr/bin/env python3
"""Backfill mention_count from source_message for existing announcements."""

import re
import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from src.database import engine, init_db


def extract_mention_count(source_message: str) -> int | None:
    """Extract mention count from source message text.

    Examples:
        '16:05 ↑ ATPC < $.15c · 2 ~ Float: 4.7 M' -> 2
        'JFBR < $6 • 3 ~ :flag_us:' -> 3
    """
    if not source_message:
        return None

    # Match both bullet point (•) and middle dot (·)
    match = re.search(r'[•·]\s*(\d+)', source_message)
    if match:
        return int(match.group(1))
    return None


def backfill_mentions():
    """Update mention_count for all announcements with source_message."""
    init_db()

    with engine.connect() as conn:
        # Get all announcements with source_message
        result = conn.execute(text(
            "SELECT id, source_message, mention_count FROM announcements WHERE source_message IS NOT NULL"
        ))
        rows = result.fetchall()

        updated = 0
        skipped = 0

        for row in rows:
            ann_id, source_message, current_mention_count = row

            # Extract mention count from source_message
            mention_count = extract_mention_count(source_message)

            if mention_count is not None:
                if current_mention_count != mention_count:
                    conn.execute(
                        text("UPDATE announcements SET mention_count = :count WHERE id = :id"),
                        {"count": mention_count, "id": ann_id}
                    )
                    updated += 1
                    print(f"Updated ID {ann_id}: mention_count = {mention_count}")
                else:
                    skipped += 1
            else:
                skipped += 1

        conn.commit()

        print(f"\nDone! Updated {updated} announcements, skipped {skipped}")


if __name__ == "__main__":
    backfill_mentions()
