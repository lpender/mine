# Orphaned Order Detection - Implementation Summary

## What Was the Problem?

You noticed logs showing:
```
15:57:41 [353k from 40k] Broker has 3 open orders (not tracking)
15:57:41 [353k from 40k] [MGRT] Pending buy order: 5 shares (new)
15:57:41 [353k from 40k] [MGRT] Pending buy order: 5 shares (new)
15:57:41 [353k from 40k] [MGRT] Pending buy order: 1030 shares (new)
```

But these orders weren't visible in your Alpaca dashboard. These were **orphaned orders** - orders that existed in Alpaca but the system wasn't tracking (likely from a previous run that crashed).

## What Was Implemented?

### 1. ✅ Enhanced Order Metadata
- **Updated `Order` dataclass** to include `created_at` and `limit_price`
- **Enhanced Alpaca API** to fetch order creation timestamps from broker
- Enables age calculation for orphaned orders

### 2. ✅ Auto-Cancel Logic
- **Detects orphaned orders** during system startup/recovery
- **Auto-cancels** orders older than `BUY_ORDER_TIMEOUT_SECONDS` (default: 5s)
- **Configurable via ENV var**: `BUY_ORDER_TIMEOUT_SECONDS=5`
- Uses the same timeout for both active and orphaned orders

### 3. ✅ Warning Alerts
- **Prominent logging** with emoji markers (⚠️ 🚨 ♻️)
- **Detailed info** including order age, ticker, shares, price
- **Summary warnings** to check broker dashboard

### 4. ✅ Database Tracking
- **New table**: `orphaned_orders` for audit trail
- **Records**: order details, discovery time, cancellation time, reason
- **Store implementation**: `OrphanedOrderStore` for CRUD operations

## Files Changed

### Core Changes
- `src/trading/base.py` - Added `created_at` and `limit_price` to `Order` dataclass
- `src/trading/alpaca.py` - Enhanced `get_open_orders()` to fetch timestamps
- `src/strategy.py` - Rewrote `_recover_pending_orders()` with auto-cancel logic
- `src/database.py` - Added `OrphanedOrderDB` table model

### New Files
- `src/orphaned_order_store.py` - Store for persisting orphaned orders
- `docs/ORPHANED_ORDERS.md` - Comprehensive documentation
- `docs/ENV_VARS.md` - Environment variable reference

## Configuration

### Environment Variable
```bash
# .env
BUY_ORDER_TIMEOUT_SECONDS=5  # Default: 5 seconds
```

This timeout applies to:
- **Active buy orders** - orders being actively tracked
- **Orphaned buy orders** - orders discovered during recovery

### Strategy Config
The timeout is now read from ENV var in `StrategyConfig`:
```python
buy_order_timeout_seconds: int = int(os.getenv("BUY_ORDER_TIMEOUT_SECONDS", "5"))
```

## Example Output

### Before (old behavior):
```
15:57:41 [353k from 40k] Broker has 3 open orders (not tracking)
15:57:41 [353k from 40k] [MGRT] Pending buy order: 5 shares (new)
```
*Just logged, no action taken*

### After (new behavior):
```
15:57:41 [353k from 40k] ⚠️  FOUND 3 UNTRACKED ORDERS IN BROKER ⚠️
15:57:41 [353k from 40k] [MGRT] 🚨 Orphaned buy order: 5 shares @ $13.9900 (new, 45.2s old, order_id=abc123)
15:57:41 [353k from 40k] [MGRT] ♻️  Auto-canceling order abc123 (age 45.2s > threshold 5s)
15:57:41 [353k from 40k] [MGRT] ✅ Successfully cancelled orphaned order abc123
15:57:41 [353k from 40k] ⚠️  Check your broker dashboard - orphaned orders detected!
```
*Logged, recorded to DB, and auto-cancelled*

## Database Migration

Run to create the new `orphaned_orders` table:
```bash
task db:migrate
```

## Usage

### Query Orphaned Orders
```sql
-- Recent orphaned orders
SELECT * FROM orphaned_orders
ORDER BY discovered_at DESC
LIMIT 10;

-- Auto-cancelled orders
SELECT * FROM orphaned_orders
WHERE cancelled_at IS NOT NULL;
```

### Adjust Timeout
```bash
# Give orders more time before cancelling
export BUY_ORDER_TIMEOUT_SECONDS=15

# Restart your trading system
```

## Benefits

1. **No more ghost orders** - Automatically cleans up orphaned orders
2. **Audit trail** - All orphaned orders recorded in database
3. **Visibility** - Clear warnings when untracked orders are found
4. **Configurable** - Adjust timeout based on your needs
5. **Prevents issues** - Avoids unexpected fills from old orders

## Next Steps

1. ✅ Database migrated (`orphaned_orders` table created)
2. Monitor logs on next restart for orphaned order detection
3. Check `orphaned_orders` table periodically to see if any patterns emerge
4. Adjust `BUY_ORDER_TIMEOUT_SECONDS` if needed based on fill rates

## Related Documentation

- Full details: `docs/ORPHANED_ORDERS.md`
- ENV vars: `docs/ENV_VARS.md`

