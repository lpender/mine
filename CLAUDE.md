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

## Data Flow

1. Discord plugin captures messages with emoji alt text (flags like `:flag_us:`)
2. alert_server.py parses and saves to PostgreSQL
3. OHLCV data fetched separately for historical announcements only
4. Backtest runs against cached historical data
