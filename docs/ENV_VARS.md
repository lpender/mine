# Environment Variables

## Trading Configuration

### `BUY_ORDER_TIMEOUT_SECONDS`
**Default:** `5`

Timeout for buy orders (both active and orphaned). Orders older than this will be automatically cancelled.

- **Active orders**: Orders the system submitted and is tracking
- **Orphaned orders**: Orders found in broker but not tracked (e.g., after restart)

```bash
# Cancel orders after 5 seconds (default)
BUY_ORDER_TIMEOUT_SECONDS=5

# Give orders more time to fill
BUY_ORDER_TIMEOUT_SECONDS=15

# Disable auto-cancellation (not recommended)
BUY_ORDER_TIMEOUT_SECONDS=0
```

### `TRADE_SLIPPAGE_PCT`
**Default:** `1.0`

Percentage slippage for buy limit orders. Buy limit price = current price × (1 + slippage%).

```bash
TRADE_SLIPPAGE_PCT=1.0  # 1% slippage
```

### `TRADE_SELL_SLIPPAGE_PCT`
**Default:** `2.0` (2× buy slippage)

Percentage slippage for sell limit orders. Defaults to 2× the buy slippage for more aggressive fills.

```bash
TRADE_SELL_SLIPPAGE_PCT=2.0  # 2% slippage
```

## Database

### `DATABASE_URL`
**Required**

PostgreSQL connection string.

```bash
DATABASE_URL=postgresql://localhost/backtest
```

### `TEST_DATABASE_URL`
**Default:** `postgresql://localhost/backtest_test`

Test database connection string (used by pytest).

```bash
TEST_DATABASE_URL=postgresql://localhost/backtest_test
```

## Alpaca API

### `ALPACA_API_KEY`
**Required**

Your Alpaca API key.

```bash
ALPACA_API_KEY=your_key_here
```

### `ALPACA_SECRET_KEY`
**Required**

Your Alpaca secret key.

```bash
ALPACA_SECRET_KEY=your_secret_here
```

## Logging

Logging configuration is handled in code, but you can set log levels via standard Python logging environment variables if needed.

## Example `.env` File

```bash
# Database
DATABASE_URL=postgresql://localhost/backtest

# Alpaca
ALPACA_API_KEY=your_key_here
ALPACA_SECRET_KEY=your_secret_here

# Trading
BUY_ORDER_TIMEOUT_SECONDS=5
TRADE_SLIPPAGE_PCT=1.0
TRADE_SELL_SLIPPAGE_PCT=2.0
```

