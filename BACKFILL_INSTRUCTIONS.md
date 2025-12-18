# Backfilling Pre-Announcement OHLCV Data

## Quick Start

You have 3 options to run the backfill:

### Option 1: Using Task (Recommended)

```bash
# Dry run first to see what will happen
task ohlcv:backfill-pre:dry-run

# Run the actual backfill
task ohlcv:backfill-pre
```

### Option 2: Direct Python Script

```bash
# Dry run
python backfill_pre_announcement_bars.py --dry-run

# Run the backfill
python backfill_pre_announcement_bars.py
```

### Option 3: Test with Limited Sample

```bash
# Test with just 10 announcements
python backfill_pre_announcement_bars.py --limit=10

# Test with dry run + limit
python backfill_pre_announcement_bars.py --dry-run --limit=10
```

## What Does This Do?

This script fetches **5 minutes of OHLCV bars BEFORE each announcement** for all existing announcements in your database that already have OHLCV data.

### Before Backfill
```
Announcement: 2024-12-17 10:00:00
Bars in DB:   10:00, 10:01, 10:02, ... 12:00
              └─ Missing pre-announcement context!
```

### After Backfill
```
Announcement: 2024-12-17 10:00:00
Bars in DB:   09:55, 09:56, 09:57, 09:58, 09:59, 10:00, 10:01, ... 12:00
              └─────────────────────────────┘
                   New pre-announcement bars!
```

## Detailed Options

```bash
# Show all available options
python backfill_pre_announcement_bars.py --help

# Available options:
  --dry-run           # Show what would be done without modifying data
  --limit=N           # Only process first N announcements
  --pre-window=N      # Fetch N minutes before (default: 5)
  --skip-check        # Skip timestamp check, backfill all announcements
```

## Examples

### Example 1: Safe Exploration
Start with a dry run to see what will happen:

```bash
python backfill_pre_announcement_bars.py --dry-run --limit=5
```

Output:
```
======================================================================
Pre-Announcement OHLCV Backfill Script
======================================================================
Pre-window: 5 minutes
Mode: DRY RUN
Limit: 5 announcements
======================================================================

Loading announcements with existing OHLCV data...
Found 1234 announcements with OHLCV data (out of 5678 total)
Checking which announcements need backfill...
  Checked 5/1234...

Found 5 announcements needing backfill

[1/5] AAPL @ 2024-12-17 10:00 (market)
  [DRY RUN] Would fetch AAPL from 2024-12-17 09:55:00 to 2024-12-17 10:00:00
[2/5] TSLA @ 2024-12-17 14:30 (market)
  [DRY RUN] Would fetch TSLA from 2024-12-17 14:25:00 to 2024-12-17 14:30:00
...
```

### Example 2: Small Test Run
Run on a small batch first to verify it works:

```bash
python backfill_pre_announcement_bars.py --limit=10
```

This will:
1. Ask for confirmation
2. Process only the first 10 announcements
3. Show detailed progress and stats

### Example 3: Full Backfill
Once you're confident, run the full backfill:

```bash
python backfill_pre_announcement_bars.py
```

or with Task:

```bash
task ohlcv:backfill-pre
```

### Example 4: Fetch More Pre-Announcement Data
If you want more than 5 minutes of pre-announcement data:

```bash
python backfill_pre_announcement_bars.py --pre-window=10
```

This fetches 10 minutes before each announcement instead of 5.

## Progress Tracking

The script shows detailed progress:

```
[1/100] AAPL @ 2024-12-17 10:00 (market)
  -> added 5 bars

[2/100] TSLA @ 2024-12-17 14:30 (market)
  -> no pre-announcement data

[3/100] GOOGL @ 2024-12-17 09:45 (premarket)
  -> ERROR: rate limit

======================================================================
Backfill Complete
======================================================================
Total announcements processed: 100
Success: 85
No pre-data: 12
Errors: 3
Total bars added: 425
```

## Rate Limiting

The script automatically respects API rate limits:
- Pauses for 1 second after every 5 requests
- This means ~300 announcements per minute max
- For 1000 announcements, expect ~3-5 minutes runtime

## Safety Features

1. **Confirmation prompt**: Asks "yes" before making changes (unless --dry-run)
2. **Smart checking**: Only backfills announcements actually missing pre-announcement bars
3. **Error handling**: Continues on errors, reports stats at the end
4. **Dry run mode**: Test without touching the database

## Troubleshooting

### "No announcements with OHLCV data found"
- Make sure you've run `task ohlcv:refetch` or loaded announcements first
- Check that announcements have `ohlcv_status='fetched'` in the database

### "ERROR: rate limit"
- This is expected occasionally with free tier APIs
- The script will continue with other announcements
- Re-run the script later to catch missed ones

### "No pre-announcement data"
- Some stocks have no trading activity 5 minutes before the announcement
- This is normal for low-volume penny stocks or premarket periods
- These are counted separately and not considered errors

## After Backfill

Once the backfill is complete:

1. **New fetches automatically include pre-announcement bars** - The updated code in `fetch_after_announcement()` handles this
2. **Existing data now has pre-announcement context** - Use it for filters, analysis, etc.
3. **Backtest will use the full data** - Charts will show 5 minutes before each announcement

## Need Help?

Run the help command:
```bash
python backfill_pre_announcement_bars.py --help
```

Or see the full documentation:
```bash
cat CHANGELOG_OHLCV_PREFETCH.md
```

