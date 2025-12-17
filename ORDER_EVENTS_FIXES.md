# Order Events Display Fixes

## Issues Fixed

### 1. **Timezone Conversion - CRITICAL BUG**
**Problem**: Times in order events table were showing 5-hour difference between SUBMITTED and FILL events.

**Root Cause**: Several places in the code were using `datetime.now()` (local time) instead of `datetime.utcnow()` (UTC). Per project rules, all timestamps must be stored as naive UTC and converted to EST only for display.

**Files Fixed**:
- `src/quote_provider.py` - Changed `datetime.now()` to `datetime.utcnow()` in quote handler
- `src/live_trading_service.py` - Fixed 2 instances of `datetime.now()` to `datetime.utcnow()`

### 2. **Order Event Display Issues**
**Problem**: Order events table was missing critical information:
- SUBMITTED events didn't show requested shares or limit price
- Floating point precision issues (6.768000000000001 vs 6.79)
- Times not converted to EST for display

**Files Fixed**:
- `pages/trade_history.py`:
  - Added `to_est_display()` function to properly convert UTC → EST
  - Modified `get_order_events_for_trade()` to:
    - Show requested shares and limit price for SUBMITTED events
    - Show fill price separately from limit price
    - Format all prices to 2 decimal places
    - Convert all timestamps to EST for display
    - Show cumulative fill progress (e.g., "400/400")

### 3. **Table Structure**
**New Columns**:
- `Time (EST)` - All times displayed in EST
- `Event` - SUBMITTED, FILL, CANCELLED, etc.
- `Side` - BUY or SELL
- `Type` - market, limit, etc.
- `Shares` - Requested (for SUBMITTED) or filled (for FILL)
- `Limit Price` - The limit price set on the order
- `Fill Price` - Actual fill price (for FILL events)
- `Filled` - Progress like "400/400"
- `Status` - Order status (pending, filled, etc.)

## Project Timezone Rules

**CRITICAL**: All code MUST follow these rules:

1. **Storage**: All timestamps stored in database as **naive UTC**
   - Use `datetime.utcnow()` for "now"
   - Never use `datetime.now()` (local time)

2. **Display**: Convert to EST only when showing to user
   - Use `to_est()` or similar helper functions
   - Format as "YYYY-MM-DD HH:MM:SS"

3. **External APIs**: Convert to naive UTC immediately
   - Alpaca timestamps: Parse, convert to UTC, remove timezone
   - InsightSentry timestamps: Already UTC, ensure naive

## Tests Added

`tests/test_order_events_timezone.py`:
- `test_order_event_timestamps_are_utc` - Verify naive UTC storage
- `test_display_conversion_to_est` - Verify EST display conversion
- `test_submitted_vs_fill_timestamps` - Verify consistent timezone handling

All tests pass ✅

## Example Output

Before (BROKEN):
```
2025-12-17 10:02:37  SUBMITTED  BUY  limit   -    -      -         d8de566c...
2025-12-17 15:02:38  FILL       BUY  limit  400  $6.81  400/400   d8de566c...
```
(5-hour gap - looks like orders took 5 hours to fill!)

After (FIXED):
```
Time (EST)           Event      Side  Type   Shares  Limit Price  Fill Price  Filled   Status
2025-12-17 10:02:37  SUBMITTED  BUY   limit  400     $6.85        -           -        pending
2025-12-17 10:02:40  FILL       BUY   limit  400     $6.85        $6.81       400/400  filled
```
(3 seconds between submit and fill - correct!)

## Related Files

- `src/strategy.py` - Still uses some `datetime.now()` but for non-critical display purposes
- `src/database.py` - All timestamp columns store naive datetime (no timezone)
- `src/order_store.py` - Correctly stores timestamps as-is (expects naive UTC from callers)

