"""Streamlit dashboard for backtesting press release announcements."""

import streamlit as st
import pandas as pd
from datetime import timedelta

from src.postgres_client import PostgresClient
from src.backtest import run_backtest, calculate_summary_stats
from src.models import BacktestConfig

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
        return val.split(",") if val else []
    return val


def set_param(key: str, value):
    """Set a query parameter."""
    if isinstance(value, bool):
        st.query_params[key] = "1" if value else "0"
    elif isinstance(value, list):
        st.query_params[key] = ",".join(value) if value else ""
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
    """Load OHLCV bars for a set of announcements."""
    client = PostgresClient()
    bars_by_announcement = {}

    for ticker, timestamp_str in announcement_keys:
        timestamp = pd.to_datetime(timestamp_str)
        start = timestamp
        end = timestamp + timedelta(minutes=window_minutes)
        bars = client.get_ohlcv_bars(ticker, start, end)
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
all_sessions = ["premarket", "market", "postmarket", "closed"]

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar Controls
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Trigger Config")

    # Read defaults from URL
    default_sl = get_param("sl", 5, float)
    default_tp = get_param("tp", 10, float)
    default_hold = get_param("hold", 60, int)

    stop_loss = st.slider(
        "Stop Loss %",
        min_value=1.0, max_value=20.0,
        value=default_sl, step=0.5,
        help="Exit when price drops by this percentage"
    )

    take_profit = st.slider(
        "Take Profit %",
        min_value=1.0, max_value=50.0,
        value=default_tp, step=0.5,
        help="Exit when price rises by this percentage"
    )

    hold_time = st.slider(
        "Hold Time (min)",
        min_value=5, max_value=120,
        value=default_hold, step=5,
        help="Maximum time to hold before timeout exit"
    )

    st.divider()
    st.header("Filters")

    # Session filter
    default_sessions = get_param("sess", "premarket,market", list)
    sessions = st.multiselect(
        "Market Session",
        options=all_sessions,
        default=[s for s in default_sessions if s in all_sessions] or ["premarket", "market"],
    )

    # Country filter
    default_countries = get_param("country", "", list)
    countries = st.multiselect(
        "Country",
        options=all_countries,
        default=[c for c in default_countries if c in all_countries],
        help="Leave empty for all countries"
    )

    # Author filter
    default_authors = get_param("author", "", list)
    authors = st.multiselect(
        "Author",
        options=all_authors,
        default=[a for a in default_authors if a in all_authors],
        help="Leave empty for all authors"
    )

    # Financing filter
    default_no_fin = get_param("no_fin", False, bool)
    exclude_financing = st.checkbox(
        "Exclude financing headlines",
        value=default_no_fin,
        help="Filter out offerings, ATMs, warrants, etc."
    )

    # Float range
    st.subheader("Float (millions)")
    col1, col2 = st.columns(2)
    default_float_min = get_param("float_min", 0, float)
    default_float_max = get_param("float_max", 1000, float)
    float_min = col1.number_input("Min", value=default_float_min, min_value=0.0, step=1.0, key="float_min")
    float_max = col2.number_input("Max", value=default_float_max, min_value=0.0, step=10.0, key="float_max")

    # Market cap range
    st.subheader("Market Cap (millions)")
    col1, col2 = st.columns(2)
    default_mc_min = get_param("mc_min", 0, float)
    default_mc_max = get_param("mc_max", 10000, float)
    mc_min = col1.number_input("Min", value=default_mc_min, min_value=0.0, step=1.0, key="mc_min")
    mc_max = col2.number_input("Max", value=default_mc_max, min_value=0.0, step=100.0, key="mc_max")

    # Update URL with current settings
    set_param("sl", stop_loss)
    set_param("tp", take_profit)
    set_param("hold", hold_time)
    set_param("sess", sessions)
    set_param("country", countries)
    set_param("author", authors)
    set_param("no_fin", exclude_financing)
    set_param("float_min", float_min)
    set_param("float_max", float_max)
    set_param("mc_min", mc_min)
    set_param("mc_max", mc_max)


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

# Author filter
if authors:
    filtered = [a for a in filtered if a.author in authors]

# Financing filter
if exclude_financing:
    filtered = [a for a in filtered if not a.headline_is_financing]

# Float filter (convert from shares to millions)
filtered = [a for a in filtered if a.float_shares is None or
            (float_min * 1e6 <= a.float_shares <= float_max * 1e6)]

# Market cap filter (stored in dollars, filter in millions)
filtered = [a for a in filtered if a.market_cap is None or
            (mc_min * 1e6 <= a.market_cap <= mc_max * 1e6)]


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
    window_minutes=hold_time,
    entry_at_candle_close=True,  # More realistic entry
)

summary = run_backtest(filtered, bars_dict, config)
stats = calculate_summary_stats(summary.results)


# ─────────────────────────────────────────────────────────────────────────────
# Display Summary Stats
# ─────────────────────────────────────────────────────────────────────────────

st.header("Summary")

col1, col2, col3, col4, col5, col6 = st.columns(6)

col1.metric("Announcements", stats["total_announcements"])
col2.metric("Trades", stats["total_trades"])
col3.metric("Win Rate", f"{stats['win_rate']:.1f}%")
col4.metric("Expectancy", f"{stats['expectancy']:+.2f}%")
col5.metric("Best", f"{stats['best_trade']:+.1f}%")
col6.metric("Worst", f"{stats['worst_trade']:+.1f}%")

# Second row
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Winners", stats["winners"])
col2.metric("Losers", stats["losers"])
col3.metric("Avg Return", f"{stats['avg_return']:+.2f}%")
col4.metric("Profit Factor", f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "inf")
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
    rows.append({
        "Time": a.timestamp,
        "Ticker": a.ticker,
        "Session": a.market_session,
        "Country": a.country,
        "Author": a.author or "",
        "Float (M)": a.float_shares / 1e6 if a.float_shares else None,
        "MC (M)": a.market_cap / 1e6 if a.market_cap else None,
        "Headline": a.headline[:60] + "..." if len(a.headline) > 60 else a.headline,
        "Entry": r.entry_price,
        "Exit": r.exit_price,
        "Return %": r.return_pct,
        "Exit Type": r.trigger_type,
    })

df = pd.DataFrame(rows)

# Sort controls (persisted to URL)
sortable_columns = ["Time", "Ticker", "Session", "Country", "Author", "Float (M)", "MC (M)", "Return %", "Exit Type"]
default_sort_col = get_param("sort", "Time")
default_sort_asc = get_param("asc", "0") == "1"

col1, col2, col3 = st.columns([2, 2, 6])
sort_column = col1.selectbox(
    "Sort by",
    options=sortable_columns,
    index=sortable_columns.index(default_sort_col) if default_sort_col in sortable_columns else 0,
)
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

st.dataframe(
    df,
    column_config=column_config,
    use_container_width=True,
    hide_index=True,
)

# Show filter summary at bottom
st.caption(f"Showing {len(filtered)} announcements | Filters: sessions={sessions}, countries={countries or 'all'}, authors={authors or 'all'}, exclude_financing={exclude_financing}")
