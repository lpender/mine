# Strategy Disable Fix

## Problem
When disabling a strategy with active positions, the system would:
1. Attempt to sell the positions
2. Log any errors
3. **Disable the strategy anyway**, even if sells failed
4. Leave orphaned positions in the database

## Solution
Updated `disable_strategy()` function to:
1. Attempt to sell all positions
2. **Check if any sells failed**
3. **Only disable the strategy if ALL positions were successfully sold**
4. Return `False` and keep strategy enabled if any sells fail
5. Log clear error messages about which positions couldn't be sold

## Changes Made

### 1. `src/live_trading_service.py`
- `disable_strategy()` now tracks failed sales
- Returns `False` if any position sales fail
- Provides detailed error logging about which tickers failed
- Strategy remains **enabled** if positions can't be sold

### 2. `pages/strategies.py`
- Updated all three places where `disable_strategy()` is called
- Now checks return value and shows appropriate error/success messages
- User is notified when disabling fails due to unsold positions

## What This Fixes
- ✅ Strategies can no longer be disabled with active positions still open
- ✅ User gets immediate feedback if position sales fail
- ✅ No more orphaned positions from failed disable operations
- ✅ Clear error messages guide user to manually close positions if needed

## For Existing Orphaned Positions
You already have a script to handle these:
```bash
python cleanup_orphaned_positions.py
```

This script will:
1. Find positions in database that don't exist at broker
2. Clean up the database records
3. OR find positions that exist at broker but not in tracking, and sell them

## Testing
To test the fix:
1. Enable a test strategy
2. Let it open a position
3. Try to disable it - should work and sell the position
4. If network/broker issues prevent the sale, strategy should remain enabled
5. You'll see an error message with details

## Edge Cases Handled
- **Broker connection failure**: Strategy stays enabled, user can retry
- **Position already sold**: Automatically cleaned up (existing behavior)
- **Partial fills**: Strategy stays enabled until all shares are sold
- **Multiple positions**: Strategy stays enabled if ANY sale fails

