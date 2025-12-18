# OHLCV Pre-Fetch Enhancement

## Summary

The OHLCV refetch has been rewritten to fetch bars for **5 minutes BEFORE** each announcement in addition to the bars after the announcement. This allows for analysis of pre-announcement price action.

## Changes Made

### 1. `src/postgres_client.py` - PostgresClient.fetch_after_announcement()
- **Added parameter**: `pre_window_minutes: int = 5` (default: 5 minutes)
- **Behavior change**: Now fetches from `announcement_time - 5 minutes` to `effective_start + window_minutes`
- **Backward compatible**: Existing code will automatically get the 5-minute pre-window

### 2. `src/massive_client.py` - MassiveClient.fetch_after_announcement()
- **Added parameter**: `pre_window_minutes: int = 5` (default: 5 minutes)
- **Behavior change**: Now fetches from `announcement_time - 5 minutes` to `effective_start + window_minutes`
- **Backward compatible**: Existing code will automatically get the 5-minute pre-window

### 3. Updated Documentation
- `refetch_missing_ohlcv.py`: Added docstring explaining the pre-fetch behavior
- `src/alert_service.py`: Updated comment to clarify "5min pre + 120min post"
- `discord-monitor/alert_server.py`: Updated comment to clarify "5min pre + 120min post"
- `todo.md`: Marked item #36 as complete

## How It Works

### Before This Change
```
Announcement Time: 10:00:00
Fetch window: 10:00:00 → 12:00:00 (120 minutes after)
```

### After This Change
```
Announcement Time: 10:00:00
Fetch window: 09:55:00 → 12:00:00 (5 minutes before + 120 minutes after)
```

## Use Cases

1. **Pre-announcement volume analysis**: Check if volume was building before the announcement
2. **Price momentum detection**: Identify if the stock was already moving before the news
3. **Better entry timing**: Understand the full context of price action around the announcement
4. **Filter development**: Build filters based on pre-announcement conditions

## Example

```python
from src.postgres_client import get_postgres_client
from datetime import datetime

client = get_postgres_client()

# Fetch OHLCV with default 5-minute pre-window
bars = client.fetch_after_announcement(
    ticker="AAPL",
    announcement_time=datetime(2024, 12, 17, 10, 0, 0),
    window_minutes=120,  # 120 minutes after
    # pre_window_minutes=5 is the default
)

# Customize pre-window
bars = client.fetch_after_announcement(
    ticker="AAPL",
    announcement_time=datetime(2024, 12, 17, 10, 0, 0),
    window_minutes=120,
    pre_window_minutes=10,  # Fetch 10 minutes before instead of 5
)
```

## Important Notes

1. **All existing code continues to work** - The new parameter has a default value
2. **Pre-window timing**: The pre-window is calculated from the actual `announcement_time`, not from the `effective_start_time` (which may be market open for premarket announcements)
3. **Data availability**: Pre-announcement bars may not exist if the stock wasn't trading (e.g., low volume penny stocks)
4. **Database storage**: All bars (pre and post) are stored with the same `announcement_ticker` and `announcement_timestamp` for efficient bulk retrieval

## Testing

To verify the change is working:

1. Run the refetch script: `python refetch_missing_ohlcv.py`
2. Check that bars now start 5 minutes before each announcement
3. Query the database to verify:

```sql
SELECT
    announcement_timestamp,
    MIN(timestamp) as first_bar,
    MAX(timestamp) as last_bar,
    COUNT(*) as bar_count
FROM ohlcv_bars
WHERE announcement_ticker = 'YOUR_TICKER'
    AND announcement_timestamp = 'YOUR_TIMESTAMP'
GROUP BY announcement_timestamp;
```

The `first_bar` should be approximately 5 minutes before `announcement_timestamp`.

## Backfilling Existing Data

A one-time backfill script has been created: **`backfill_pre_announcement_bars.py`**

This script will fetch the missing 5 minutes of pre-announcement bars for all existing announcements in the database.

### Usage

```bash
# Dry run to see what would happen (recommended first step)
python backfill_pre_announcement_bars.py --dry-run

# Test with first 10 announcements
python backfill_pre_announcement_bars.py --limit=10

# Full backfill (will prompt for confirmation)
python backfill_pre_announcement_bars.py

# Fetch 10 minutes instead of 5
python backfill_pre_announcement_bars.py --pre-window=10

# See all options
python backfill_pre_announcement_bars.py --help
```

### Features

- **Smart checking**: Only backfills announcements that are actually missing pre-announcement bars
- **Progress tracking**: Shows detailed progress and statistics
- **Rate limiting**: Respects API rate limits (5 requests/second)
- **Dry run mode**: Test without modifying data
- **Flexible**: Configurable pre-window size and announcement limit
- **Safe**: Prompts for confirmation before modifying data

### What It Does

1. Loads all announcements with `ohlcv_status='fetched'`
2. Checks each announcement to see if it's missing pre-announcement bars
3. Fetches the missing 5 minutes of bars before each announcement
4. Stores them with the same `announcement_ticker` and `announcement_timestamp`
5. Reports statistics on success/failure

## Todo Item Completed

- [x] pre-fetch data from 5 minutes before? (todo.md line 36)

