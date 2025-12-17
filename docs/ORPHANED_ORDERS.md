# Orphaned Order Detection & Auto-Cancellation

## What Are Orphaned Orders?

**Orphaned orders** are orders that exist in your broker (Alpaca) but aren't being tracked by the trading system. This typically happens when:

1. The system submits buy orders
2. The system crashes or restarts before the orders fill
3. The in-memory tracking (`pending_orders`) is lost
4. On restart, the system finds orders in the broker it doesn't recognize

## Example

```
15:57:41 [353k from 40k] ⚠️  FOUND 3 UNTRACKED ORDERS IN BROKER ⚠️
15:57:41 [353k from 40k] [MGRT] 🚨 Orphaned buy order: 5 shares @ $13.9900 (new, 45.2s old, order_id=abc123)
15:57:41 [353k from 40k] [MGRT] 🚨 Orphaned buy order: 5 shares @ $13.9900 (new, 45.3s old, order_id=def456)
15:57:41 [353k from 40k] [MGRT] 🚨 Orphaned buy order: 1030 shares @ $13.9900 (new, 45.4s old, order_id=ghi789)
```

## Auto-Cancellation

The system automatically cancels orphaned orders that exceed the timeout threshold.

### Configuration

Set the timeout via environment variable (defaults to 5 seconds):

```bash
# .env
BUY_ORDER_TIMEOUT_SECONDS=5  # Cancel orders older than 5 seconds
```

This same timeout applies to:
- **Active buy orders** - orders the system is actively tracking
- **Orphaned buy orders** - orders discovered during startup/recovery

### Behavior

When orphaned orders are detected:

1. **Log warnings** with prominent emoji markers (⚠️ 🚨)
2. **Record to database** in `orphaned_orders` table for audit trail
3. **Auto-cancel** if age exceeds `BUY_ORDER_TIMEOUT_SECONDS`
4. **Update database** with cancellation timestamp and reason

## Database Schema

### `orphaned_orders` Table

```sql
CREATE TABLE orphaned_orders (
    id SERIAL PRIMARY KEY,
    broker_order_id VARCHAR(50) NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    side VARCHAR(4) NOT NULL,  -- "buy" or "sell"
    shares INTEGER NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    limit_price FLOAT,

    -- Timestamps
    order_created_at TIMESTAMP,  -- When order was created at broker
    discovered_at TIMESTAMP DEFAULT NOW(),  -- When we discovered it
    cancelled_at TIMESTAMP,  -- When we cancelled it (if auto-cancelled)

    -- Context
    strategy_name VARCHAR(100),
    reason VARCHAR(200),  -- Why it was orphaned/cancelled
    paper BOOLEAN DEFAULT TRUE
);
```

## Querying Orphaned Orders

```sql
-- Recent orphaned orders
SELECT * FROM orphaned_orders
ORDER BY discovered_at DESC
LIMIT 10;

-- Orphaned orders by ticker
SELECT ticker, COUNT(*), SUM(shares)
FROM orphaned_orders
GROUP BY ticker
ORDER BY COUNT(*) DESC;

-- Auto-cancelled orders
SELECT * FROM orphaned_orders
WHERE cancelled_at IS NOT NULL
ORDER BY cancelled_at DESC;
```

## Prevention

To minimize orphaned orders:

1. **Use process managers** - Run with systemd, supervisor, or similar to auto-restart
2. **Monitor logs** - Watch for crash patterns
3. **Set appropriate timeouts** - Balance between fill opportunity and cleanup
4. **Check broker dashboard** - Periodically verify no stale orders exist

## Troubleshooting

### Orders Not Being Cancelled

Check your timeout setting:
```bash
echo $BUY_ORDER_TIMEOUT_SECONDS
```

If not set, it defaults to 5 seconds. Increase if needed:
```bash
export BUY_ORDER_TIMEOUT_SECONDS=10
```

### Orders Cancelled Too Quickly

If legitimate orders are being cancelled before they can fill, increase the timeout:
```bash
export BUY_ORDER_TIMEOUT_SECONDS=15  # Give orders 15 seconds to fill
```

### Disable Auto-Cancellation

Set timeout to 0 to disable auto-cancellation (not recommended):
```bash
export BUY_ORDER_TIMEOUT_SECONDS=0  # Never auto-cancel
```

## Related Code

- **Detection**: `src/strategy.py` - `_recover_pending_orders()`
- **Storage**: `src/orphaned_order_store.py` - `OrphanedOrderStore`
- **Database**: `src/database.py` - `OrphanedOrderDB`
- **Order API**: `src/trading/alpaca.py` - `get_open_orders()`

