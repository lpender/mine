# Press Release Backtester

A Streamlit dashboard for analyzing stock price movements following press release announcements. Parses Discord messages to extract announcement data and backtests trading strategies using historical OHLCV data.

## Features

- **Discord Message Parser**: Extracts ticker symbols, timestamps, price thresholds, and metadata (float, IO%, market cap) from pasted Discord messages
- **OHLCV Data Fetching**: Retrieves minute-level price data via Massive.com API with parquet file caching
- **Backtesting Engine**: Simulates entry/exit trades with configurable triggers:
  - Entry trigger (% move from open)
  - Take profit target
  - Stop loss
  - Volume threshold
  - Time window (default 120 minutes)
- **Interactive Charts**: Candlestick charts with volume, entry/exit markers, and trigger level overlays
- **Summary Statistics**: Win rate, average return, expectancy, profit factor
- **Live Trading**: Execute trades via Interactive Brokers with bracket orders (take-profit + stop-loss)
- **Premarket Support**: IB bracket orders work in premarket/afterhours
- **Real-time Alerts**: Discord message monitor for instant trade signals

## Setup

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure your API keys:
   ```bash
   cp .env.example .env
   # Edit .env and add your API keys
   ```

4. Start IB Gateway (Docker):
   ```bash
   docker compose up -d
   ```

## Usage

### Backtesting Dashboard

```bash
streamlit run app.py
```

1. Paste Discord messages into the sidebar text area
2. Set the reference date for relative timestamps
3. Adjust trigger parameters using the sliders
4. Click on a row to view the detailed price chart
5. Export results to CSV

### Live Trading (Interactive Brokers)

IB Gateway must be running via Docker (`docker compose up -d`).

#### Quick Commands

```bash
# Check account status
python trade.py status

# Get a quote
python trade.py quote AAPL

# Buy $100 of a stock with default 10% TP, 7% SL
python trade.py buy AAPL

# Buy with custom amount
python trade.py buy AAPL --dollars 200

# Buy specific number of shares
python trade.py buy AAPL --shares 10

# Custom take-profit and stop-loss
python trade.py buy AAPL --tp 15 --sl 5

# View open positions
python trade.py positions

# View pending orders (including TP/SL)
python trade.py orders

# Close a position
python trade.py close AAPL

# Cancel all pending orders
python trade.py cancel-all

# LIVE TRADING (use with caution!)
python trade.py --live buy AAPL
```

### Real-time Discord Monitor

For faster execution, run the Discord message monitor:

```bash
# Terminal 1: Start the server
python discord_server.py

# or

python discord_server.py --gui --auto-trade

# Then in Discord (browser):
# 1. Open Discord in your browser
# 2. Navigate to the alerts channel
# 3. Press F12 > Console
# 4. Paste the JavaScript from discord_monitor.js
```

When a new alert appears, you'll see it in your terminal instantly. Execute with:
```bash
python trade.py buy TICKER
```

If you want to enable auto-trading (risky!):

```bash
curl -X POST http://127.0.0.1:8765/toggle-auto-trade
```

This will automatically execute bracket orders when alerts come in. Start with paper trading!

## Project Structure

```
.
├── app.py                 # Streamlit dashboard
├── trade.py               # CLI for executing trades via IB
├── discord_server.py      # Real-time Discord message receiver
├── discord_monitor.js     # Browser script for Discord
├── docker-compose.yml     # IB Gateway Docker setup
├── refetch_data.py        # Re-fetch OHLCV data for cached announcements
├── src/
│   ├── models.py          # Data classes
│   ├── parser.py          # Discord message parser
│   ├── massive_client.py  # OHLCV API client with caching
│   ├── backtest.py        # Backtesting engine
│   └── ib_trader.py       # Interactive Brokers trading client
├── data/
│   └── ohlcv/             # Cached OHLCV parquet files
└── tests/
    └── test_backtest.py   # Unit tests
```

## Configuration

Environment variables (in `.env`):

| Variable | Description |
|----------|-------------|
| `MASSIVE_API_KEY` | API key for OHLCV data |
| `IB_USERNAME` | Interactive Brokers username (for Docker) |
| `IB_PASSWORD` | Interactive Brokers password (for Docker) |
| `IB_TRADING_MODE` | `paper` or `live` (for Docker) |

## Strategy Notes

Based on backtesting analysis:

- **Best parameters**: 0% entry trigger, 10% take-profit, 7% stop-loss, no volume filter
- **Why**: Entering at the open captures the initial gap-up before fades
- **HIGH_CTB stocks**: Showed 100% win rate with 7% TP (small sample)
- **Avoid**: IL (Israel) based stocks showed poor performance

**Caution**: Backtesting assumes execution at the first minute's open price. Real execution may differ due to slippage and liquidity.

## License

MIT
