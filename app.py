"""Streamlit dashboard for backtesting press release announcements."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta
from zoneinfo import ZoneInfo

from src.postgres_client import PostgresClient

# Timezone for display
EST = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def to_est(dt):
    """Convert a datetime to EST for display. Assumes naive datetimes are UTC."""
    if dt is None:
        return None
    # Assume naive datetimes are UTC (all DB timestamps are stored in UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(EST)


from src.backtest import run_backtest, calculate_summary_stats
from src.models import BacktestConfig
from src.strategy import StrategyConfig
from src.live_trading_service import (
    get_live_trading_status,
    is_live_trading_active,
)
# Note: Alert service is now started by run_trading.py, not the dashboard
# This ensures alerts go to the trading engine's callback handler

st.set_page_config(
    page_title="PR Backtest Dashboard",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────────────────────
# URL State Management
# ─────────────────────────────────────────────────────────────────────────────

def get_param(key: str, default, param_type=str):
    """Get a query parameter with type conversion."""
    val = st.query_params.get(key, default)
    if param_type == float:
        return float(val)
    elif param_type == int:
        return int(val)
    elif param_type == bool:
        return val == "1" or val == "true"
    elif param_type == list:
        # Handle empty string properly - "".split(",") returns [''] not []
        if not val or val == "":
            return []
        return [x for x in val.split(",") if x]  # Filter empty strings
    return val


def set_param(key: str, value):
    """Set a query parameter. Skip empty lists to keep URL clean."""
    if isinstance(value, bool):
        st.query_params[key] = "1" if value else "0"
    elif isinstance(value, list):
        if value:  # Only set if non-empty
            st.query_params[key] = ",".join(value)
        elif key in st.query_params:
            del st.query_params[key]  # Remove empty lists from URL
    else:
        st.query_params[key] = str(value)


# ─────────────────────────────────────────────────────────────────────────────
# Load Data
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_announcements():
    """Load announcements from PostgreSQL."""
    client = PostgresClient()
    return client.load_announcements()


@st.cache_data(ttl=300)
def load_ohlcv_for_announcements(announcement_keys: tuple, window_minutes: int):
    """Load OHLCV bars for a set of announcements.

    Note: Both announcements and OHLCV bars are stored in UTC (naive).
    Uses get_effective_start_time() to match the time range used when data was fetched.
    """
    from src.massive_client import MassiveClient

    client = PostgresClient()
    massive = MassiveClient()
    bars_by_announcement = {}

    for ticker, timestamp_str in announcement_keys:
        timestamp = pd.to_datetime(timestamp_str)
        # Both are UTC - just ensure naive
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)

        # Use the same effective start time calculation as the data fetcher
        # This ensures we query the correct time range for premarket/postmarket/closed
        start = massive.get_effective_start_time(timestamp)
        end = start + timedelta(minutes=window_minutes)
        # Use get_ohlcv_bars to read from cache only (don't trigger API fetches)
        bars = client.get_ohlcv_bars(ticker, start, end) or []
        bars_by_announcement[(ticker, timestamp)] = bars

    return bars_by_announcement


# ─────────────────────────────────────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────────────────────────────────────

st.title("PR Backtest Dashboard")

# Load all announcements
all_announcements = load_announcements()

if not all_announcements:
    st.warning("No announcements found in database.")
    st.stop()

# Extract unique values for filters
all_countries = sorted(set(a.country for a in all_announcements if a.country))
all_authors = sorted(set(a.author for a in all_announcements if a.author))
all_channels = sorted(set(a.channel for a in all_announcements if a.channel))
all_sessions = ["premarket", "market", "postmarket", "closed"]
all_directions = sorted(set(a.direction for a in all_announcements if a.direction))

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar Controls
# ─────────────────────────────────────────────────────────────────────────────

# Initialize session state from URL params ONLY if not already set
# This ensures URL params are used on first load, but widget changes aren't overwritten
# Widget keys are prefixed with underscore to avoid conflict with URL param names
def init_session_state():
    """Initialize session state from URL params only for missing keys."""
    # Helper to set if missing
    def set_if_missing(key, value):
        if key not in st.session_state:
            st.session_state[key] = value

    # Validate slider values against their min/max ranges
    sl_val = get_param("sl", 5.0, float)
    set_if_missing("_sl", max(1.0, min(30.0, sl_val)) if sl_val > 0 else 5.0)
    tp_val = get_param("tp", 10.0, float)
    set_if_missing("_tp", max(1.0, min(50.0, tp_val)) if tp_val > 0 else 10.0)
    hold_val = get_param("hold", 60, int)
    set_if_missing("_hold", max(5, min(120, hold_val)) if hold_val > 0 else 60)
    # Parse session list, filtering to valid values
    sess_list = get_param("sess", "premarket,market", list)
    set_if_missing("_sess", [s for s in sess_list if s in all_sessions] or ["premarket", "market"])
    country_list = get_param("country", "", list)
    set_if_missing("_country", [c for c in country_list if c in all_countries])
    author_list = get_param("author", "", list)
    set_if_missing("_author", [a for a in author_list if a in all_authors])
    channel_list = get_param("channel", "", list)
    set_if_missing("_channel", [c for c in channel_list if c in all_channels])
    set_if_missing("_no_fin", get_param("no_fin", False, bool))
    set_if_missing("_has_hl", get_param("has_hl", False, bool))
    set_if_missing("_no_hl", get_param("no_hl", False, bool))
    set_if_missing("_float_min", get_param("float_min", 0.0, float))
    # float_max=0 is invalid, use default
    float_max_val = get_param("float_max", 1000.0, float)
    set_if_missing("_float_max", float_max_val if float_max_val > 0 else 1000.0)
    set_if_missing("_mc_min", get_param("mc_min", 0.0, float))
    # mc_max=0 is invalid, use default
    mc_max_val = get_param("mc_max", 10000.0, float)
    set_if_missing("_mc_max", mc_max_val if mc_max_val > 0 else 10000.0)
    set_if_missing("_sl_from_open", get_param("sl_open", False, bool))
    set_if_missing("_consec_candles", get_param("consec", 0, int))
    set_if_missing("_min_candle_vol", get_param("min_vol", 0, int))
    set_if_missing("_price_min", get_param("price_min", 0.0, float))
    # price_max=0 is invalid (would filter everything), use default
    price_max_val = get_param("price_max", 100.0, float)
    set_if_missing("_price_max", price_max_val if price_max_val > 0 else 100.0)
    set_if_missing("_trailing_stop", get_param("trail", 0.0, float))
    direction_list = get_param("direction", "", list)
    set_if_missing("_direction", [d for d in direction_list if d in all_directions])
    set_if_missing("_scanner_test", get_param("scanner_test", False, bool))
    set_if_missing("_scanner_after_lull", get_param("scanner_lull", False, bool))
    # Position sizing
    set_if_missing("_stake_mode", get_param("stake_mode", "fixed"))
    set_if_missing("_stake_amount", get_param("stake", 1000.0, float))
    set_if_missing("_volume_pct", get_param("vol_pct", 1.0, float))
    set_if_missing("_max_stake", get_param("max_stake", 10000.0, float))

init_session_state()

with st.sidebar:
    st.header("Trigger Config")

    stop_loss = st.slider(
        "Stop Loss %",
        min_value=1.0, max_value=30.0,
        step=0.5,
        key="_sl",
        help="Exit when price drops by this percentage"
    )

    take_profit = st.slider(
        "Take Profit %",
        min_value=1.0, max_value=50.0,
        step=0.5,
        key="_tp",
        help="Exit when price rises by this percentage"
    )

    hold_time = st.slider(
        "Hold Time (min)",
        min_value=5, max_value=120,
        step=5,
        key="_hold",
        help="Maximum time to hold before timeout exit"
    )

    consec_candles = st.slider(
        "Entry after X green candles",
        min_value=0, max_value=10,
        step=1,
        key="_consec_candles",
        help="Wait for X consecutive green candles (close > open) before entry (0 = disabled)"
    )

    min_candle_vol = st.number_input(
        "Min volume per candle",
        min_value=0,
        step=1000,
        key="_min_candle_vol",
        help="Minimum volume each candle must have for consecutive entry (0 = no minimum)"
    )

    sl_from_open = st.checkbox(
        "SL from first candle open",
        key="_sl_from_open",
        help="Calculate stop loss from first candle's open instead of entry price"
    )

    trailing_stop = st.slider(
        "Trailing Stop %",
        min_value=0.0, max_value=30.0,
        step=0.5,
        key="_trailing_stop",
        help="Exit if price drops this % from highest point since entry (0 = disabled)"
    )

    st.divider()
    st.header("Filters")

    # Session filter
    sessions = st.multiselect(
        "Market Session",
        options=all_sessions,
        key="_sess",
    )

    # Country filter
    countries = st.multiselect(
        "Country",
        options=all_countries,
        key="_country",
        help="Leave empty for all countries"
    )

    # Country blacklist
    country_blacklist = st.multiselect(
        "Country Blacklist",
        options=all_countries,
        key="_country_blacklist",
        help="Exclude these countries"
    )

    # Max intraday mentions filter
    max_mentions = st.number_input(
        "Max Intraday Mentions",
        min_value=0,
        max_value=100,
        value=0,
        key="_max_mentions",
        help="Only alerts with mentions <= this value (0 = no filter)"
    )

    # Author filter
    authors = st.multiselect(
        "Author",
        options=all_authors,
        key="_author",
        help="Leave empty for all authors"
    )

    # Channel filter
    channels = st.multiselect(
        "Channel",
        options=all_channels,
        key="_channel",
        help="Leave empty for all channels"
    )

    # Financing filter
    exclude_financing = st.checkbox(
        "Exclude financing headlines",
        key="_no_fin",
        help="Filter out offerings, ATMs, warrants, etc."
    )

    # Has headline filter
    require_headline = st.checkbox(
        "Has headline",
        key="_has_hl",
        help="Only show announcements with a headline"
    )

    # No headline filter
    exclude_headline = st.checkbox(
        "No headline",
        key="_no_hl",
        help="Only show announcements WITHOUT a headline"
    )

    # Direction filter (up arrow vs up-right arrow)
    directions = st.multiselect(
        "Direction",
        options=all_directions,
        key="_direction",
        help="Arrow direction: 'up' (↑) or 'up_right' (↗). Leave empty for all."
    )

    # Scanner flags
    scanner_test = st.checkbox(
        "Scanner: test only",
        key="_scanner_test",
        help="Only show announcements detected by the 'test' scanner"
    )

    scanner_after_lull = st.checkbox(
        "Scanner: after-lull only",
        key="_scanner_after_lull",
        help="Only show announcements detected by the 'after-lull' scanner"
    )

    # Float range
    st.subheader("Float (millions)")
    col1, col2 = st.columns(2)
    float_min = col1.number_input("Min", min_value=0.0, step=1.0, key="_float_min")
    float_max = col2.number_input("Max", min_value=0.0, step=10.0, key="_float_max")

    # Market cap range
    st.subheader("Market Cap (millions)")
    col1, col2 = st.columns(2)
    mc_min = col1.number_input("Min", min_value=0.0, step=1.0, key="_mc_min")
    mc_max = col2.number_input("Max", min_value=0.0, step=100.0, key="_mc_max")

    # Price range (filters by actual entry price from OHLCV, not announcement price)
    st.subheader("Entry Price ($)")
    col1, col2 = st.columns(2)
    price_min = col1.number_input("Min", min_value=0.0, step=0.5, key="_price_min", help="Exclude if entry price ≤ this")
    price_max = col2.number_input("Max", min_value=0.0, step=1.0, key="_price_max", help="Exclude if entry price > this")

    # ─────────────────────────────────────────────────────────────────────────
    # Position Sizing
    # ─────────────────────────────────────────────────────────────────────────
    st.divider()
    st.header("Position Sizing")

    stake_mode = st.radio(
        "Sizing Mode",
        ["fixed", "volume_pct"],
        format_func=lambda x: "Fixed $" if x == "fixed" else "% of Volume",
        horizontal=True,
        key="_stake_mode",
    )

    if stake_mode == "fixed":
        stake_amount = st.number_input(
            "Stake per Trade ($)",
            value=st.session_state.get("_stake_amount", 1000.0),
            min_value=1.0,
            step=100.0,
            key="_stake_amount",
            help="Fixed dollar amount per trade"
        )
        volume_pct = st.session_state.get("_volume_pct", 1.0)
        max_stake = st.session_state.get("_max_stake", 10000.0)
    else:
        col1, col2 = st.columns(2)
        volume_pct = col1.number_input(
            "Volume %",
            value=st.session_state.get("_volume_pct", 1.0),
            min_value=0.1,
            max_value=100.0,
            step=0.1,
            key="_volume_pct",
            help="Buy this % of the previous candle's volume"
        )
        max_stake = col2.number_input(
            "Max Cost ($)",
            value=st.session_state.get("_max_stake", 10000.0),
            min_value=1.0,
            step=100.0,
            key="_max_stake",
            help="Maximum position cost cap"
        )
        stake_amount = st.session_state.get("_stake_amount", 1000.0)

    # Update URL with current settings (for sharing/bookmarking)
    set_param("sl", stop_loss)
    set_param("tp", take_profit)
    set_param("hold", hold_time)
    set_param("consec", consec_candles)
    set_param("min_vol", min_candle_vol)
    set_param("sl_open", sl_from_open)
    set_param("sess", sessions)
    set_param("country", countries)
    set_param("author", authors)
    set_param("channel", channels)
    set_param("no_fin", exclude_financing)
    set_param("has_hl", require_headline)
    set_param("no_hl", exclude_headline)
    set_param("float_min", float_min)
    set_param("float_max", float_max)
    set_param("mc_min", mc_min)
    set_param("mc_max", mc_max)
    set_param("price_min", price_min)
    set_param("price_max", price_max)
    set_param("trail", trailing_stop)
    set_param("direction", directions)
    set_param("scanner_test", scanner_test)
    set_param("scanner_lull", scanner_after_lull)
    # Position sizing params
    set_param("stake_mode", stake_mode)
    set_param("stake", stake_amount)
    set_param("vol_pct", volume_pct)
    set_param("max_stake", max_stake)

    # ─────────────────────────────────────────────────────────────────────────
    # Save Strategy / Live Trading
    # ─────────────────────────────────────────────────────────────────────────
    st.divider()
    st.header("Strategy")

    # Show current strategy summary
    st.caption(f"Entry: {consec_candles} green candles, {min_candle_vol}+ vol")
    st.caption(f"Exit: TP {take_profit}%, SL {stop_loss}%, Trail {trailing_stop}%")

    # Save as Strategy button
    strategy_name = st.text_input("Strategy Name", placeholder="e.g., My Scalper", key="save_strategy_name")

    if st.button("Save as Strategy"):
        if not strategy_name:
            st.error("Please enter a strategy name")
        else:
            from src.strategy_store import get_strategy_store
            store = get_strategy_store()

            if store.get_strategy_by_name(strategy_name):
                st.error(f"Strategy '{strategy_name}' already exists")
            else:
                strategy_config = StrategyConfig(
                    channels=channels if channels else [],
                    directions=directions if directions else [],
                    price_min=price_min,
                    price_max=price_max,
                    sessions=sessions if sessions else ["premarket", "market"],
                    country_blacklist=country_blacklist if country_blacklist else [],
                    max_intraday_mentions=max_mentions if max_mentions > 0 else None,
                    consec_green_candles=consec_candles,
                    min_candle_volume=int(min_candle_vol),
                    take_profit_pct=take_profit,
                    stop_loss_pct=stop_loss,
                    stop_loss_from_open=sl_from_open,
                    trailing_stop_pct=trailing_stop,
                    timeout_minutes=hold_time,
                    stake_mode=stake_mode,
                    stake_amount=stake_amount,
                    volume_pct=volume_pct,
                    max_stake=max_stake,
                )
                store.save_strategy(strategy_name, strategy_config)
                st.success(f"Saved strategy '{strategy_name}'")

    st.divider()

    # Trading status summary
    trading_active = is_live_trading_active()

    if trading_active:
        status = get_live_trading_status()
        is_paper_mode = status.get("paper", True) if status else True
        strategy_count = status.get("strategy_count", 0) if status else 0

        if is_paper_mode:
            st.success(f"Trading Active ({strategy_count} strategies)")
        else:
            st.error(f"LIVE Trading ({strategy_count} strategies)")

        # Quick stats
        if status:
            col1, col2, col3 = st.columns(3)
            col1.metric("Watching", len(status.get("pending_entries", [])))
            col2.metric("Positions", len(status.get("active_trades", {})))
            col3.metric("Completed", status.get("completed_trades", 0))
    else:
        st.info("Trading engine not running")

    # Links
    st.markdown("[Manage Strategies →](strategies)")
    st.markdown("[View Trade History →](trade_history)")

    # Cache control
    st.divider()
    if st.button("Clear Cache"):
        st.cache_data.clear()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Filter Announcements
# ─────────────────────────────────────────────────────────────────────────────

filtered = all_announcements

# Session filter
if sessions:
    filtered = [a for a in filtered if a.market_session in sessions]

# Country filter
if countries:
    filtered = [a for a in filtered if a.country in countries]

# Country blacklist filter
if country_blacklist:
    filtered = [a for a in filtered if a.country not in country_blacklist]

# Author filter
if authors:
    filtered = [a for a in filtered if a.author in authors]

# Channel filter
if channels:
    filtered = [a for a in filtered if a.channel in channels]

# Financing filter
if exclude_financing:
    filtered = [a for a in filtered if not a.headline_is_financing]

# Headline filter
if require_headline:
    filtered = [a for a in filtered if a.headline and a.headline.strip()]
if exclude_headline:
    filtered = [a for a in filtered if not a.headline or not a.headline.strip()]

# Direction filter
if directions:
    filtered = [a for a in filtered if a.direction in directions]

# Scanner test filter
if scanner_test:
    filtered = [a for a in filtered if a.scanner_test]

# Scanner after-lull filter
if scanner_after_lull:
    filtered = [a for a in filtered if a.scanner_after_lull]

# Max intraday mentions filter
if max_mentions > 0:
    filtered = [a for a in filtered if a.mention_count is not None and a.mention_count <= max_mentions]

# Float filter (convert from shares to millions)
filtered = [a for a in filtered if a.float_shares is None or
            (float_min * 1e6 <= a.float_shares <= float_max * 1e6)]

# Market cap filter (stored in dollars, filter in millions)
filtered = [a for a in filtered if a.market_cap is None or
            (mc_min * 1e6 <= a.market_cap <= mc_max * 1e6)]

# Note: Price filter is applied after backtest based on actual entry price (see below)


# ─────────────────────────────────────────────────────────────────────────────
# Run Backtest
# ─────────────────────────────────────────────────────────────────────────────

if not filtered:
    st.warning("No announcements match the current filters.")
    st.stop()

# Create cache key from announcement identifiers
announcement_keys = tuple((a.ticker, a.timestamp.isoformat()) for a in filtered)

# Load OHLCV data
bars_by_announcement = load_ohlcv_for_announcements(announcement_keys, hold_time)

# Convert keys back to proper format
bars_dict = {}
for (ticker, ts_str), bars in bars_by_announcement.items():
    ts = pd.to_datetime(ts_str)
    bars_dict[(ticker, ts)] = bars

# Run backtest
config = BacktestConfig(
    entry_trigger_pct=0,  # Enter immediately at announcement
    take_profit_pct=take_profit,
    stop_loss_pct=stop_loss,
    stop_loss_from_open=sl_from_open,
    window_minutes=hold_time,
    entry_at_candle_close=(consec_candles == 0),  # Only use candle close if not waiting for consecutive candles
    entry_after_consecutive_candles=consec_candles,
    min_candle_volume=int(min_candle_vol),
    trailing_stop_pct=trailing_stop,
)

summary = run_backtest(filtered, bars_dict, config)

# Price filter (applied after backtest based on actual entry price)
# Filter out results where entry price is outside the min/max range
if price_min > 0 or price_max < 100:
    summary.results = [
        r for r in summary.results
        if r.entry_price is None or (price_min < r.entry_price <= price_max)
    ]

stats = calculate_summary_stats(summary.results)


# ─────────────────────────────────────────────────────────────────────────────
# Display Summary Stats
# ─────────────────────────────────────────────────────────────────────────────

st.header("Summary")

col1, col2, col3, col4, col5, col6 = st.columns(6)

# Calculate weeks in the data for weekly return
if filtered:
    dates = [a.timestamp for a in filtered]
    date_range_days = (max(dates) - min(dates)).days
    weeks = max(1, date_range_days / 7)  # At least 1 week
else:
    weeks = 1

# Calculate P/L using position sizing settings
total_pnl = sum(
    r.pnl_with_sizing(stake_mode, stake_amount, volume_pct, max_stake)
    for r in summary.results
    if r.pnl_with_sizing(stake_mode, stake_amount, volume_pct, max_stake) is not None
)
trades_with_pnl = sum(
    1 for r in summary.results
    if r.pnl_with_sizing(stake_mode, stake_amount, volume_pct, max_stake) is not None
)
weekly_pnl = total_pnl / weeks if weeks > 0 else 0

# Build sizing description for metric help
if stake_mode == "volume_pct":
    sizing_desc = f"{volume_pct}% vol (max ${max_stake:,.0f})"
else:
    sizing_desc = f"${stake_amount:,.0f} fixed"

col1.metric("Announcements", stats["total_announcements"])
col2.metric("Trades", stats["total_trades"])
col3.metric("Win Rate", f"{stats['win_rate']:.1f}%")
col4.metric("Weekly P/L", f"${weekly_pnl:+,.0f}", help=f"Total ${total_pnl:+,.0f} over {weeks:.1f} weeks | Sizing: {sizing_desc} | {trades_with_pnl} trades")
col5.metric("Profit Factor", f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "inf")
col6.metric("Expectancy", f"{stats['expectancy']:+.2f}%", help="Average return per trade")

# Second row
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Winners", stats["winners"])
col2.metric("Losers", stats["losers"])
col3.metric("Avg Return", f"{stats['avg_return']:+.2f}%")
col4.metric("Best/Worst", f"{stats['best_trade']:+.1f}% / {stats['worst_trade']:.1f}%")
col5.metric("Avg Win", f"{stats['avg_win']:+.2f}%")
col6.metric("Avg Loss", f"{stats['avg_loss']:.2f}%")


# ─────────────────────────────────────────────────────────────────────────────
# Display Results Table
# ─────────────────────────────────────────────────────────────────────────────

st.header("Trade Results")

# Build dataframe from results
rows = []
for r in summary.results:
    a = r.announcement
    msg = a.source_message or ""
    headline = a.headline or ""
    rows.append({
        "Time": to_est(a.timestamp),
        "Ticker": a.ticker,
        "Session": a.market_session,
        "Country": a.country,
        "Channel": a.channel or "",
        "Author": a.author or "",
        "Mentions": a.mention_count,
        "Float (M)": a.float_shares / 1e6 if a.float_shares else None,
        "MC (M)": a.market_cap / 1e6 if a.market_cap else None,
        "Headline": headline[:60] + "..." if len(headline) > 60 else headline,
        "Message": msg[:80] + "..." if len(msg) > 80 else msg,
        "Entry": r.entry_price,
        "Exit": r.exit_price,
        "Return %": r.return_pct,
        "Exit Type": r.trigger_type,
    })

df = pd.DataFrame(rows)

# Sort controls (persisted to URL)
sortable_columns = ["Time", "Ticker", "Session", "Country", "Channel", "Author", "Mentions", "Float (M)", "MC (M)", "Return %", "Exit Type"]
default_sort_col = get_param("sort", "Time")
default_sort_asc = get_param("asc", "0") == "1"

st.caption("Use these controls to sort (persists to URL). Table header clicks don't persist.")
col1, col2, col3 = st.columns([2, 2, 6])
sort_column = col1.selectbox(
    "Sort by",
    options=sortable_columns,
    index=sortable_columns.index(default_sort_col) if default_sort_col in sortable_columns else 0,
)
col2.write("")  # Vertical spacer to align checkbox with selectbox
sort_ascending = col2.checkbox("Ascending", value=default_sort_asc)

# Update URL with sort settings
set_param("sort", sort_column)
set_param("asc", sort_ascending)

# Apply sorting
if sort_column in df.columns:
    df = df.sort_values(by=sort_column, ascending=sort_ascending, na_position="last")

# Configure column display
column_config = {
    "Time": st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm"),
    "Mentions": st.column_config.NumberColumn("Mentions", format="%d"),
    "Float (M)": st.column_config.NumberColumn("Float (M)", format="%.1f"),
    "MC (M)": st.column_config.NumberColumn("MC (M)", format="%.1f"),
    "Entry": st.column_config.NumberColumn("Entry", format="$%.2f"),
    "Exit": st.column_config.NumberColumn("Exit", format="$%.2f"),
    "Return %": st.column_config.NumberColumn(
        "Return %",
        format="%.2f%%",
        help="Percent return on trade"
    ),
}

# Map dataframe row index to result index (since df may be sorted differently)
df = df.reset_index(drop=True)
df_to_result_idx = {}
for df_idx, row in df.iterrows():
    # Find matching result by time and ticker
    for r_idx, r in enumerate(summary.results):
        # Compare EST-converted times
        if to_est(r.announcement.timestamp) == row["Time"] and r.announcement.ticker == row["Ticker"]:
            df_to_result_idx[df_idx] = r_idx
            break

# Use dataframe with single-row selection
event = st.dataframe(
    df,
    column_config=column_config,
    width="stretch",
    hide_index=True,
    selection_mode="single-row",
    on_select="rerun",
    key="trade_table",
)

# Show filter summary at bottom
st.caption(f"Showing {len(filtered)} announcements | Filters: sessions={sessions}, countries={countries or 'all'}, channels={channels or 'all'}, authors={authors or 'all'}, exclude_financing={exclude_financing}")

# ─────────────────────────────────────────────────────────────────────────────
# Row Selection & OHLCV Chart
# ─────────────────────────────────────────────────────────────────────────────

st.header("Trade Detail")

# Get selected row from the dataframe selection event
selected_rows = event.selection.rows if hasattr(event, 'selection') else []

if not selected_rows:
    st.info("Click a row in the table above to view trade details and chart.")
    # Clear row param from URL if no selection
    if "row" in st.query_params:
        del st.query_params["row"]
else:
    selected_df_idx = selected_rows[0]
    # Persist selection to URL
    set_param("row", selected_df_idx)

    # Map to result index
    if selected_df_idx in df_to_result_idx:
        result_idx = df_to_result_idx[selected_df_idx]
        selected_result = summary.results[result_idx]
        ann = selected_result.announcement

        # Show full headline and message
        st.subheader(f"{ann.ticker} - {to_est(ann.timestamp).strftime('%Y-%m-%d %H:%M')} EST")
        if ann.headline:
            st.markdown(f"**Headline:** {ann.headline}")
        if ann.source_message:
            st.markdown(f"**Full Message:** {ann.source_message}")

        # Get the bars for this announcement
        key = (ann.ticker, ann.timestamp)
        bars = bars_dict.get(key, [])

        if bars:
            # Build candlestick data (convert to EST, shift +1 min for end-time display like WeBull)
            bar_times = [to_est(b.timestamp) + timedelta(minutes=1) for b in bars]
            bar_open = [b.open for b in bars]
            bar_high = [b.high for b in bars]
            bar_low = [b.low for b in bars]
            bar_close = [b.close for b in bars]
            bar_volume = [b.volume for b in bars]

            # Create candlestick chart
            fig = go.Figure()

            # Build hover text with volume (vertical layout)
            hover_text = [
                f"Open: ${o:.2f}<br>High: ${h:.2f}<br>Low: ${l:.2f}<br>Close: ${c:.2f}<br>Volume: {v:,.0f}"
                for o, h, l, c, v in zip(bar_open, bar_high, bar_low, bar_close, bar_volume)
            ]

            # Add candlestick
            fig.add_trace(go.Candlestick(
                x=bar_times,
                open=bar_open,
                high=bar_high,
                low=bar_low,
                close=bar_close,
                name="Price",
                increasing_line_color="green",
                decreasing_line_color="red",
                text=hover_text,
                hoverinfo="text+x",
            ))

            # Add entry marker (entry at close of first candle, +1 min for end-time display)
            if selected_result.entry_price and selected_result.entry_time:
                fig.add_trace(go.Scatter(
                    x=[to_est(selected_result.entry_time) + timedelta(minutes=1)],
                    y=[selected_result.entry_price],
                    mode="markers",
                    marker=dict(symbol="circle", size=12, color="blue", line=dict(width=2, color="white")),
                    name=f"Entry @ ${selected_result.entry_price:.2f}",
                ))

            # Add exit marker (+1 min for end-time display)
            if selected_result.exit_price and selected_result.exit_time:
                exit_color = "green" if selected_result.return_pct > 0 else "red"
                fig.add_trace(go.Scatter(
                    x=[to_est(selected_result.exit_time) + timedelta(minutes=1)],
                    y=[selected_result.exit_price],
                    mode="markers",
                    marker=dict(symbol="x", size=12, color=exit_color, line=dict(width=3)),
                    name=f"Exit @ ${selected_result.exit_price:.2f} ({selected_result.trigger_type})",
                ))

            # Add horizontal lines for entry, TP, SL
            if selected_result.entry_price:
                entry = selected_result.entry_price
                first_open = bars[0].open if bars else entry
                tp_price = entry * (1 + take_profit / 100)
                # SL from first candle open or entry price
                if sl_from_open:
                    sl_price = first_open * (1 - stop_loss / 100)
                else:
                    sl_price = entry * (1 - stop_loss / 100)

                fig.add_hline(y=entry, line_dash="solid", line_color="blue", opacity=0.5,
                              annotation_text=f"Entry ${entry:.2f}")
                fig.add_hline(y=tp_price, line_dash="dash", line_color="green", opacity=0.5,
                              annotation_text=f"TP ${tp_price:.2f}")
                fig.add_hline(y=sl_price, line_dash="dash", line_color="red", opacity=0.5,
                              annotation_text=f"SL ${sl_price:.2f}")

            # Layout
            fig.update_layout(
                xaxis_title="Time (EST)",
                yaxis_title="Price",
                xaxis_rangeslider_visible=False,
                height=500,
            )

            st.plotly_chart(fig, width="stretch")

            # Show trade details
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Entry Price", f"${selected_result.entry_price:.2f}" if selected_result.entry_price else "N/A")
            col2.metric("Exit Price", f"${selected_result.exit_price:.2f}" if selected_result.exit_price else "N/A")
            col3.metric("Return", f"{selected_result.return_pct:+.2f}%" if selected_result.return_pct else "N/A")
            col4.metric("Exit Type", selected_result.trigger_type)
        else:
            st.warning(f"No OHLCV data available for {ann.ticker}")
