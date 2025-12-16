# Backtest Project Rules

## Critical Data Rules

**NEVER ingest/save announcements from TODAY.**

- Market data for today is incomplete (Polygon free tier doesn't provide same-day minute data)
- OHLCV fetches for today will fail with 403 errors
- Always filter out `ann.timestamp.date() == date.today()` before saving
- The `--include-today` flag exists but should only be used for debugging, not production backfills

## Polygon API Limits

- Free tier: **5 requests per minute** (12 second delay between calls)
- No same-day minute data on free tier
- Some tickers may require paid plans (403 errors)

## Timezone Rules

**Always store in UTC, convert to ET only for display.**

- All timestamps in the database (announcements, OHLCV bars) are stored as naive UTC
- Alpaca returns UTC timestamps - store them as-is (naive UTC)
- Convert to ET only in the presentation layer (Streamlit, logs)
- Use `to_est()` in app.py to convert UTC naive → ET aware for display

## Trading Hours

**We trade during EXTENDED hours: premarket, market, AND postmarket.**

- Alpaca provides extended hours data (4am-8pm ET)
- For ANY announcement during trading hours (premarket/market/postmarket), fetch OHLCV starting from the announcement time
- Only roll forward to next market open for announcements during "closed" hours (8pm-4am ET, weekends, holidays)

**get_effective_start_time() logic:**
- Premarket (4am-9:30am ET): return announcement time (NOT market open)
- Market (9:30am-4pm ET): return announcement time
- Postmarket (4pm-8pm ET): return announcement time
- Closed (8pm-4am ET): return next market open

## Data Flow

1. Discord plugin captures messages with emoji alt text (flags like `:flag_us:`)
2. alert_server.py parses and saves to PostgreSQL
3. OHLCV data fetched separately for historical announcements only
4. Backtest runs against cached historical data
