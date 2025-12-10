# Press Release Backtester

A Streamlit dashboard for analyzing stock price movements following press release announcements. Parses Discord messages to extract announcement data and backtests trading strategies using historical OHLCV data from Polygon.io.

## Features

- **Discord Message Parser**: Extracts ticker symbols, timestamps, price thresholds, and metadata (float, IO%, market cap) from pasted Discord messages
- **OHLCV Data Fetching**: Retrieves minute-level price data via Polygon.io API with parquet file caching
- **Backtesting Engine**: Simulates entry/exit trades with configurable triggers:
  - Entry trigger (% move from open)
  - Take profit target
  - Stop loss
  - Volume threshold
  - Time window
- **Interactive Charts**: Candlestick charts with volume, entry/exit markers, and trigger level overlays
- **Summary Statistics**: Win rate, average return, best/worst trades

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

3. Configure your API key:
   ```bash
   cp .env.example .env
   # Edit .env and add your Polygon.io API key
   ```

## Usage

Run the Streamlit app:
```bash
streamlit run app.py
```

### Workflow

1. Paste Discord messages into the sidebar text area
2. Set the reference date for relative timestamps (e.g., "Yesterday at 9:00 AM")
3. Click "Parse Messages" to extract announcements
4. Click "Fetch OHLCV Data" to retrieve price data
5. Adjust trigger parameters using the sliders
6. Click on a row in the table to view the detailed price chart
7. Export results to CSV

### Expected Message Format

```
PR - Spike
APP
 — 8:00 AM
BNKK  < $.50c  - Bonk, Inc. Provides 2026 Guidance... - Link  ~  :flag_us:  |  Float: 139 M  |  IO: 6.04%  |  MC: 26.8 M
MNTS  < $1  - Momentus Announces... - Link  ~  :flag_us:  |  Float: 23.9 M  |  IO: 1.06%  |  MC: 18.4 M
```

## Project Structure

```
.
├── app.py                 # Streamlit dashboard
├── src/
│   ├── models.py          # Data classes (Announcement, OHLCVBar, TradeResult, etc.)
│   ├── parser.py          # Discord message parser
│   ├── massive_client.py  # Polygon.io API client with caching
│   └── backtest.py        # Backtesting engine
├── data/
│   └── ohlcv/             # Cached OHLCV parquet files
├── requirements.txt
└── .env.example
```

## Configuration

Environment variables (in `.env`):

| Variable | Description |
|----------|-------------|
| `MASSIVE_API_KEY` | Your Polygon.io API key |

## License

MIT
