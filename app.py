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
    """Convert a datetime to EST for display."""
    if dt is None:
        return None
    # Assume naive datetimes are UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(EST)


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
all_channels = sorted(set(a.channel for a in all_announcements if a.channel))
all_sessions = ["premarket", "market", "postmarket", "closed"]

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar Controls
# ─────────────────────────────────────────────────────────────────────────────

# Initialize session state from URL params only on first load
# Widget keys are prefixed with underscore to avoid conflict with URL param names
if "_initialized" not in st.session_state:
    st.session_state._initialized = True
    st.session_state._sl = get_param("sl", 5.0, float)
    st.session_state._tp = get_param("tp", 10.0, float)
    st.session_state._hold = get_param("hold", 60, int)
    # Parse session list, filtering to valid values
    sess_list = get_param("sess", "premarket,market", list)
    st.session_state._sess = [s for s in sess_list if s in all_sessions] or ["premarket", "market"]
    country_list = get_param("country", "", list)
    st.session_state._country = [c for c in country_list if c in all_countries]
    author_list = get_param("author", "", list)
    st.session_state._author = [a for a in author_list if a in all_authors]
    channel_list = get_param("channel", "", list)
    st.session_state._channel = [c for c in channel_list if c in all_channels]
    st.session_state._no_fin = get_param("no_fin", False, bool)
    st.session_state._has_hl = get_param("has_hl", False, bool)
    st.session_state._float_min = get_param("float_min", 0.0, float)
    st.session_state._float_max = get_param("float_max", 1000.0, float)
    st.session_state._mc_min = get_param("mc_min", 0.0, float)
    st.session_state._mc_max = get_param("mc_max", 10000.0, float)

with st.sidebar:
    st.header("Trigger Config")

    stop_loss = st.slider(
        "Stop Loss %",
        min_value=1.0, max_value=20.0,
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

    # Update URL with current settings (for sharing/bookmarking)
    set_param("sl", stop_loss)
    set_param("tp", take_profit)
    set_param("hold", hold_time)
    set_param("sess", sessions)
    set_param("country", countries)
    set_param("author", authors)
    set_param("channel", channels)
    set_param("no_fin", exclude_financing)
    set_param("has_hl", require_headline)
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

# Channel filter
if channels:
    filtered = [a for a in filtered if a.channel in channels]

# Financing filter
if exclude_financing:
    filtered = [a for a in filtered if not a.headline_is_financing]

# Headline filter
if require_headline:
    filtered = [a for a in filtered if a.headline and a.headline.strip()]

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
        "Time": to_est(a.timestamp),
        "Ticker": a.ticker,
        "Session": a.market_session,
        "Country": a.country,
        "Channel": a.channel or "",
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
sortable_columns = ["Time", "Ticker", "Session", "Country", "Channel", "Author", "Float (M)", "MC (M)", "Return %", "Exit Type"]
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
    use_container_width=True,
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

        # Show full headline
        st.subheader(f"{ann.ticker} - {to_est(ann.timestamp).strftime('%Y-%m-%d %H:%M')} EST")
        if ann.headline:
            st.markdown(f"**Headline:** {ann.headline}")

        # Get the bars for this announcement
        key = (ann.ticker, ann.timestamp)
        bars = bars_dict.get(key, [])

        if bars:
            # Build candlestick data (convert to EST)
            bar_times = [to_est(b.timestamp) for b in bars]
            bar_open = [b.open for b in bars]
            bar_high = [b.high for b in bars]
            bar_low = [b.low for b in bars]
            bar_close = [b.close for b in bars]

            # Create candlestick chart
            fig = go.Figure()

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
            ))

            # Add entry marker (entry at close of first candle)
            if selected_result.entry_price and selected_result.entry_time:
                fig.add_trace(go.Scatter(
                    x=[to_est(selected_result.entry_time)],
                    y=[selected_result.entry_price],
                    mode="markers",
                    marker=dict(symbol="circle", size=12, color="blue", line=dict(width=2, color="white")),
                    name=f"Entry @ ${selected_result.entry_price:.2f}",
                ))

            # Add exit marker
            if selected_result.exit_price and selected_result.exit_time:
                exit_color = "green" if selected_result.return_pct > 0 else "red"
                fig.add_trace(go.Scatter(
                    x=[to_est(selected_result.exit_time)],
                    y=[selected_result.exit_price],
                    mode="markers",
                    marker=dict(symbol="x", size=12, color=exit_color, line=dict(width=3)),
                    name=f"Exit @ ${selected_result.exit_price:.2f} ({selected_result.trigger_type})",
                ))

            # Add horizontal lines for entry, TP, SL
            if selected_result.entry_price:
                entry = selected_result.entry_price
                tp_price = entry * (1 + take_profit / 100)
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

            st.plotly_chart(fig, use_container_width=True)

            # Show trade details
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Entry Price", f"${selected_result.entry_price:.2f}" if selected_result.entry_price else "N/A")
            col2.metric("Exit Price", f"${selected_result.exit_price:.2f}" if selected_result.exit_price else "N/A")
            col3.metric("Return", f"{selected_result.return_pct:+.2f}%" if selected_result.return_pct else "N/A")
            col4.metric("Exit Type", selected_result.trigger_type)
        else:
            st.warning(f"No OHLCV data available for {ann.ticker}")
