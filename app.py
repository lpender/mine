import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from dotenv import load_dotenv

import json
from pathlib import Path

from src.parser import parse_discord_html_with_stats
from src.massive_client import MassiveClient
from src.backtest import run_backtest, calculate_summary_stats
from src.models import BacktestConfig, Announcement, OHLCVBar

# Config file for persisting settings
CONFIG_PATH = Path("data/config.json")

def load_config():
    """Load saved configuration from disk."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config: dict):
    """Save configuration to disk."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

# Load environment variables
load_dotenv()

# Page config
st.set_page_config(
    page_title="Press Release Backtester",
    page_icon="📈",
    layout="wide",
)

st.title("Press Release Backtester")

# Initialize session state
if "announcements" not in st.session_state:
    st.session_state.announcements = []
if "bars_by_announcement" not in st.session_state:
    st.session_state.bars_by_announcement = {}  # keyed by (ticker, timestamp)
if "results" not in st.session_state:
    st.session_state.results = []
if "selected_row_idx" not in st.session_state:
    st.session_state.selected_row_idx = None
if "sort_column_value" not in st.session_state:
    st.session_state.sort_column_value = "Time (EST)"
if "sort_ascending_value" not in st.session_state:
    st.session_state.sort_ascending_value = False
if "initialized" not in st.session_state:
    st.session_state.initialized = False
if "last_messages_input" not in st.session_state:
    st.session_state.last_messages_input = ""

# Load saved config defaults
saved_config = load_config()

# Auto-load cached data on first run
if not st.session_state.initialized:
    client = MassiveClient()
    cached_announcements, cached_bars = client.load_all_cached_data()
    if cached_announcements:
        st.session_state.announcements = cached_announcements
        st.session_state.bars_by_announcement = cached_bars
        st.session_state.results = []
    st.session_state.initialized = True


# Sidebar
with st.sidebar:
    st.header("Message Input")

    with st.expander("📋 How to copy Discord HTML", expanded=False):
        st.markdown("""
1. Open Discord in browser (F12 → Console)
2. Run: `copy(document.querySelector('[class*="messagesWrapper"]').outerHTML)`
3. Paste the HTML below
        """)

    messages_input = st.text_area(
        "Paste Discord HTML:",
        height=200,
        placeholder="""Paste HTML from Discord console...""",
    )

    # Placeholder for progress bar (will be used during data fetch)
    progress_placeholder = st.empty()
    status_placeholder = st.empty()

    st.divider()
    st.header("Trigger Configuration")

    # Initialize slider values in session state from saved config (only once)
    if "entry_trigger" not in st.session_state:
        st.session_state.entry_trigger = saved_config.get("entry_trigger", 5.0)
    if "take_profit" not in st.session_state:
        st.session_state.take_profit = saved_config.get("take_profit", 10.0)
    if "stop_loss" not in st.session_state:
        st.session_state.stop_loss = saved_config.get("stop_loss", 3.0)
    if "volume_threshold_k" not in st.session_state:
        st.session_state.volume_threshold_k = saved_config.get("volume_threshold", 0) // 1000
    if "window_minutes" not in st.session_state:
        st.session_state.window_minutes = saved_config.get("window_minutes", 120)
    if "entry_at_candle_close" not in st.session_state:
        st.session_state.entry_at_candle_close = saved_config.get("entry_at_candle_close", False)

    entry_trigger = st.slider(
        "Entry Trigger (%)",
        min_value=0.0,
        max_value=20.0,
        step=0.5,
        help="Buy when price moves up by this percentage from open",
        key="entry_trigger",
    )

    take_profit = st.slider(
        "Take Profit (%)",
        min_value=1.0,
        max_value=50.0,
        step=0.5,
        help="Sell when price moves up by this percentage from entry",
        key="take_profit",
    )

    stop_loss = st.slider(
        "Stop Loss (%)",
        min_value=1.0,
        max_value=20.0,
        step=0.5,
        help="Sell when price moves down by this percentage from entry",
        key="stop_loss",
    )

    volume_threshold = st.slider(
        "Min Volume Threshold (k)",
        min_value=0,
        max_value=500,
        step=10,
        help="Minimum volume (in thousands) required to trigger entry",
        key="volume_threshold_k",
    ) * 1000  # Convert back to actual volume

    window_minutes = st.slider(
        "Window (minutes)",
        min_value=5,
        max_value=120,
        step=5,
        help="How long to track after announcement",
        key="window_minutes",
    )

    entry_at_candle_close = st.checkbox(
        "Entry at candle close",
        help="Enter at end of first candle instead of open (more realistic)",
        key="entry_at_candle_close",
    )

    st.subheader("Filters")

    # Session filter checkboxes
    if "filter_premarket" not in st.session_state:
        st.session_state.filter_premarket = saved_config.get("filter_premarket", True)
    if "filter_market" not in st.session_state:
        st.session_state.filter_market = saved_config.get("filter_market", True)
    if "filter_postmarket" not in st.session_state:
        st.session_state.filter_postmarket = saved_config.get("filter_postmarket", True)
    if "filter_closed" not in st.session_state:
        st.session_state.filter_closed = saved_config.get("filter_closed", True)

    filter_premarket = st.checkbox(
        "Premarket (4:00-9:30)",
        key="filter_premarket",
    )
    filter_market = st.checkbox(
        "Market (9:30-16:00)",
        key="filter_market",
    )
    filter_postmarket = st.checkbox(
        "Postmarket (16:00-20:00)",
        key="filter_postmarket",
    )
    filter_closed = st.checkbox(
        "Closed (20:00-4:00)",
        key="filter_closed",
    )

    # High CTB filter
    ctb_options = ["Any", "High CTB", "Not High CTB"]
    if "filter_ctb" not in st.session_state:
        st.session_state.filter_ctb = saved_config.get("filter_ctb", "Any")

    filter_ctb = st.selectbox(
        "Cost to Borrow",
        ctb_options,
        index=ctb_options.index(st.session_state.filter_ctb) if st.session_state.filter_ctb in ctb_options else 0,
        key="filter_ctb",
    )

    # IO% range filter
    if "filter_io_min" not in st.session_state:
        st.session_state.filter_io_min = saved_config.get("filter_io_min", 0.0)
    if "filter_io_max" not in st.session_state:
        st.session_state.filter_io_max = saved_config.get("filter_io_max", 100.0)

    io_col1, io_col2 = st.columns(2)
    with io_col1:
        filter_io_min = st.number_input(
            "IO% Min",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            key="filter_io_min",
        )
    with io_col2:
        filter_io_max = st.number_input(
            "IO% Max",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            key="filter_io_max",
        )

    # Save config when values change
    current_config = {
        "entry_trigger": entry_trigger,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "volume_threshold": volume_threshold,
        "window_minutes": window_minutes,
        "entry_at_candle_close": entry_at_candle_close,
        "filter_premarket": filter_premarket,
        "filter_market": filter_market,
        "filter_postmarket": filter_postmarket,
        "filter_closed": filter_closed,
        "filter_ctb": filter_ctb,
        "filter_io_min": filter_io_min,
        "filter_io_max": filter_io_max,
    }
    if current_config != saved_config:
        save_config(current_config)


# Auto-parse when HTML input changes
if messages_input.strip() and messages_input != st.session_state.last_messages_input:
    st.session_state.last_messages_input = messages_input

    # Check if it looks like HTML at all
    if '<' not in messages_input:
        st.sidebar.error("Invalid input. Paste Discord HTML (use Cmd+Shift+E in Discord or copy element from DevTools).")
        new_announcements = []
    else:
        new_announcements, parse_stats = parse_discord_html_with_stats(messages_input)

        if parse_stats.get("error"):
            st.sidebar.error(f"Parse error: {parse_stats['error']}")
            new_announcements = []
        elif not new_announcements:
            # Show detailed feedback about why no announcements were found
            if parse_stats["total_messages"] == 0:
                st.sidebar.error("No messages found in HTML. Make sure to copy the messages wrapper element.")
            elif parse_stats["filtered_by_cutoff"] == parse_stats["total_messages"]:
                st.sidebar.warning(f"All {parse_stats['total_messages']} messages are from today (ET) and were excluded.")
            elif parse_stats["not_ticker_pattern"] > 0:
                st.sidebar.warning(f"Found {parse_stats['total_messages']} messages, but none matched ticker pattern (TICKER < $X).")
            else:
                st.sidebar.warning("No announcements found. Messages from today (ET) are excluded.")
        else:
            st.sidebar.success(f"Parsed {parse_stats['parsed']} announcements ({parse_stats['filtered_by_cutoff']} from today excluded)")

            # Debug: show first few parsed announcements with all fields
            with st.sidebar.expander("Debug: Parsed Data"):
                for ann in new_announcements[:3]:
                    st.write(f"**{ann.ticker}** @ {ann.timestamp}")
                    st.write(f"- Price: ${ann.price_threshold}")
                    st.write(f"- Float: {ann.float_shares}")
                    st.write(f"- IO%: {ann.io_percent}")
                    st.write(f"- MC: {ann.market_cap}")
                    st.write(f"- SI%: {ann.short_interest}")
                    st.write(f"- High CTB: {ann.high_ctb}")
                    st.write(f"- Country: {ann.country}")
                    st.write("---")

    if new_announcements:
        # Add new announcements to existing ones (dedup by ticker+timestamp)
        existing_keys = {(a.ticker, a.timestamp) for a in st.session_state.announcements}
        actually_added = []
        for ann in new_announcements:
            key = (ann.ticker, ann.timestamp)
            if key not in existing_keys:
                st.session_state.announcements.append(ann)
                existing_keys.add(key)
                actually_added.append(ann)

        if actually_added:
            # Fetch OHLCV data synchronously with progress bar
            client = MassiveClient()

            # Find announcements needing OHLCV fetch
            to_fetch = [
                ann for ann in actually_added
                if (ann.ticker, ann.timestamp) not in st.session_state.bars_by_announcement
            ]

            if to_fetch:
                progress_bar = progress_placeholder.progress(0, text="Fetching OHLCV data...")
                for i, ann in enumerate(to_fetch):
                    progress_bar.progress(
                        (i + 1) / len(to_fetch),
                        text=f"Fetching {ann.ticker} ({i + 1}/{len(to_fetch)})"
                    )
                    key = (ann.ticker, ann.timestamp)
                    bars = client.fetch_after_announcement(
                        ann.ticker,
                        ann.timestamp,
                        window_minutes,
                    )
                    st.session_state.bars_by_announcement[key] = bars
                progress_placeholder.empty()

            # Save announcements to cache
            client.save_announcements(st.session_state.announcements)
            st.session_state.results = []
            st.rerun()

# Check if there are any announcements missing OHLCV data
missing_data = [
    ann for ann in st.session_state.announcements
    if (ann.ticker, ann.timestamp) not in st.session_state.bars_by_announcement
]
if missing_data:
    status_placeholder.warning(f"⚠️ {len(missing_data)} tickers missing OHLCV data")
    with st.sidebar:
        if st.button("📥 Fetch Missing Data", use_container_width=True):
            client = MassiveClient()
            progress_bar = progress_placeholder.progress(0, text="Fetching OHLCV data...")
            for i, ann in enumerate(missing_data):
                progress_bar.progress(
                    (i + 1) / len(missing_data),
                    text=f"Fetching {ann.ticker} ({i + 1}/{len(missing_data)})"
                )
                key = (ann.ticker, ann.timestamp)
                bars = client.fetch_after_announcement(
                    ann.ticker,
                    ann.timestamp,
                    window_minutes,
                )
                st.session_state.bars_by_announcement[key] = bars
            progress_placeholder.empty()
            status_placeholder.empty()
            st.session_state.results = []
            st.rerun()

# Main area
if st.session_state.announcements:
    # Build list of allowed sessions based on filter checkboxes
    allowed_sessions = []
    if filter_premarket:
        allowed_sessions.append("premarket")
    if filter_market:
        allowed_sessions.append("market")
    if filter_postmarket:
        allowed_sessions.append("postmarket")
    if filter_closed:
        allowed_sessions.append("closed")

    # Filter announcements by session
    all_announcements = st.session_state.announcements
    if allowed_sessions:
        announcements = [a for a in all_announcements if a.market_session in allowed_sessions]
    else:
        # If no filters selected, show nothing (or could show all)
        announcements = []

    # Apply CTB filter
    if filter_ctb == "High CTB":
        announcements = [a for a in announcements if a.high_ctb]
    elif filter_ctb == "Not High CTB":
        announcements = [a for a in announcements if not a.high_ctb]

    # Apply IO% range filter
    if filter_io_min > 0 or filter_io_max < 100:
        announcements = [
            a for a in announcements
            if a.io_percent is not None and filter_io_min <= a.io_percent <= filter_io_max
        ]

    if not announcements:
        st.warning("No announcements match the current filters.")

    # Run backtest
    config = BacktestConfig(
        entry_trigger_pct=entry_trigger,
        take_profit_pct=take_profit,
        stop_loss_pct=stop_loss,
        volume_threshold=volume_threshold,
        window_minutes=window_minutes,
        entry_at_candle_close=entry_at_candle_close,
    )

    if st.session_state.bars_by_announcement:
        # Convert to format expected by backtest (dict keyed by ticker+timestamp tuple)
        summary = run_backtest(
            announcements,
            st.session_state.bars_by_announcement,
            config,
        )
        st.session_state.results = summary.results

        # Summary stats
        st.header("Summary Statistics")
        stats = calculate_summary_stats(summary.results)

        col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)
        with col1:
            st.metric("Trades", stats["total_trades"])
        with col2:
            st.metric("Win Rate", f"{stats['win_rate']:.1f}%")
        with col3:
            st.metric("Avg Return", f"{stats['avg_return']:.2f}%")
        with col4:
            st.metric("Expectancy", f"{stats['expectancy']:.2f}%")
        with col5:
            pf = stats['profit_factor']
            pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
            st.metric("Profit Factor", pf_str)
        with col6:
            st.metric("Best Trade", f"{stats['best_trade']:.2f}%")
        with col7:
            st.metric("Worst Trade", f"{stats['worst_trade']:.2f}%")
        with col8:
            st.metric("No Entry", stats["no_entry"])

        st.divider()

    # Announcements table
    st.header("Announcements")

    # Sort controls - use callbacks to persist values
    sort_options = ["Time (EST)", "Ticker", "Session", "Return", "Status"]

    def on_sort_change():
        st.session_state.sort_column_value = st.session_state.sort_selectbox

    def on_ascending_change():
        st.session_state.sort_ascending_value = st.session_state.sort_checkbox

    sort_col1, sort_col2 = st.columns([3, 1])
    with sort_col1:
        sort_column = st.selectbox(
            "Sort by",
            sort_options,
            index=sort_options.index(st.session_state.sort_column_value),
            key="sort_selectbox",
            on_change=on_sort_change,
        )
    with sort_col2:
        sort_ascending = st.checkbox(
            "Ascending",
            value=st.session_state.sort_ascending_value,
            key="sort_checkbox",
            on_change=on_ascending_change,
        )

    # Build table data with original index preserved
    table_data = []
    for i, ann in enumerate(announcements):
        result = st.session_state.results[i] if i < len(st.session_state.results) else None
        key = (ann.ticker, ann.timestamp)
        has_bars = key in st.session_state.bars_by_announcement

        # Store numeric return for sorting
        return_val = result.return_pct if result and result.return_pct is not None else float('-inf')

        # Determine status
        if not has_bars:
            status = "no data"
        elif result:
            status = result.trigger_type
        else:
            status = "pending"

        row = {
            "_original_idx": i,  # Hidden column to track original index
            "Ticker": ann.ticker,
            "Time (EST)": ann.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "Session": ann.market_session.capitalize(),
            "Price": f"${ann.price_threshold:.2f}" if ann.price_threshold else "N/A",
            "Float": f"{ann.float_shares/1e6:.1f}M" if ann.float_shares else "N/A",
            "IO%": f"{ann.io_percent:.1f}%" if ann.io_percent is not None else "N/A",
            "MC": f"${ann.market_cap/1e6:.1f}M" if ann.market_cap else "N/A",
            "SI%": f"{ann.short_interest:.1f}%" if ann.short_interest is not None else "N/A",
            "CTB": "High" if ann.high_ctb else "-",
            "Country": ann.country,
            "Return": f"{result.return_pct:.2f}%" if result and result.return_pct is not None else "N/A",
            "_return_numeric": return_val,  # Hidden column for sorting
            "Status": status,
        }
        table_data.append(row)

    if table_data:
        df = pd.DataFrame(table_data)

        # Sort the dataframe
        if sort_column == "Return":
            df = df.sort_values("_return_numeric", ascending=sort_ascending)
        else:
            df = df.sort_values(sort_column, ascending=sort_ascending)

        # Store mapping from display row to original index
        display_to_original = df["_original_idx"].tolist()

        # Remove hidden columns for display
        display_df = df.drop(columns=["_original_idx", "_return_numeric"])

        # Display as interactive table
        selected_idx = st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        # Update stored selection when user clicks a row (map back to original index)
        if selected_idx and selected_idx.selection.rows:
            display_row = selected_idx.selection.rows[0]
            st.session_state.selected_row_idx = display_to_original[display_row]

    # Chart for selected announcement (use stored index so it persists across slider changes)
    if announcements and st.session_state.selected_row_idx is not None and st.session_state.selected_row_idx < len(announcements):
        idx = st.session_state.selected_row_idx
        selected_ann = announcements[idx]
        key = (selected_ann.ticker, selected_ann.timestamp)
        bars = st.session_state.bars_by_announcement.get(key, [])

        if bars:
            st.header(f"Price Action: {selected_ann.ticker}")
            st.caption(selected_ann.headline[:100] + "..." if len(selected_ann.headline) > 100 else selected_ann.headline)

            # Create candlestick chart with volume
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.7, 0.3],
            )

            # Candlestick
            fig.add_trace(
                go.Candlestick(
                    x=[b.timestamp for b in bars],
                    open=[b.open for b in bars],
                    high=[b.high for b in bars],
                    low=[b.low for b in bars],
                    close=[b.close for b in bars],
                    name="Price",
                ),
                row=1, col=1,
            )

            # Add entry/exit markers if we have results
            if idx < len(st.session_state.results):
                result = st.session_state.results[idx]
                if result.entry_price and result.entry_time:
                    fig.add_trace(
                        go.Scatter(
                            x=[result.entry_time],
                            y=[result.entry_price],
                            mode="markers",
                            marker=dict(size=15, color="green", symbol="triangle-up"),
                            name="Entry",
                        ),
                        row=1, col=1,
                    )

                if result.exit_price and result.exit_time:
                    color = "green" if result.return_pct and result.return_pct > 0 else "red"
                    fig.add_trace(
                        go.Scatter(
                            x=[result.exit_time],
                            y=[result.exit_price],
                            mode="markers",
                            marker=dict(size=15, color=color, symbol="triangle-down"),
                            name="Exit",
                        ),
                        row=1, col=1,
                    )

                if result.entry_price:
                    # Show actual entry price line
                    fig.add_hline(
                        y=result.entry_price,
                        line_dash="dash",
                        line_color="blue",
                        annotation_text=f"Entry (${result.entry_price:.2f})",
                        row=1, col=1,
                    )

                    tp_line = result.entry_price * (1 + take_profit / 100)
                    sl_line = result.entry_price * (1 - stop_loss / 100)
                    fig.add_hline(
                        y=tp_line,
                        line_dash="dash",
                        line_color="green",
                        annotation_text=f"Take Profit ({take_profit}%)",
                        row=1, col=1,
                    )
                    fig.add_hline(
                        y=sl_line,
                        line_dash="dash",
                        line_color="red",
                        annotation_text=f"Stop Loss ({stop_loss}%)",
                        row=1, col=1,
                    )

            # Volume bars
            colors = ["green" if bars[i].close >= bars[i].open else "red" for i in range(len(bars))]
            fig.add_trace(
                go.Bar(
                    x=[b.timestamp for b in bars],
                    y=[b.volume for b in bars],
                    marker_color=colors,
                    name="Volume",
                ),
                row=2, col=1,
            )

            fig.update_layout(
                height=600,
                xaxis_rangeslider_visible=False,
                showlegend=True,
            )
            fig.update_xaxes(title_text="Time", row=2, col=1)
            fig.update_yaxes(title_text="Price", row=1, col=1)
            fig.update_yaxes(title_text="Volume", row=2, col=1)

            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"No OHLCV data available for {selected_ann.ticker}. Click 'Fetch OHLCV Data' first.")

    # Export results
    if st.session_state.results:
        st.divider()
        if st.button("Export Results to CSV"):
            export_data = []
            for i, (ann, result) in enumerate(zip(announcements, st.session_state.results)):
                export_data.append({
                    "ticker": ann.ticker,
                    "timestamp": ann.timestamp,
                    "market_session": ann.market_session,
                    "price_threshold": ann.price_threshold,
                    "headline": ann.headline,
                    "country": ann.country,
                    "float_shares": ann.float_shares,
                    "io_percent": ann.io_percent,
                    "market_cap": ann.market_cap,
                    "entry_price": result.entry_price,
                    "entry_time": result.entry_time,
                    "exit_price": result.exit_price,
                    "exit_time": result.exit_time,
                    "return_pct": result.return_pct,
                    "trigger_type": result.trigger_type,
                })
            export_df = pd.DataFrame(export_data)
            csv = export_df.to_csv(index=False)
            st.download_button(
                "Download CSV",
                csv,
                "backtest_results.csv",
                "text/csv",
            )

else:
    st.info("Paste Discord messages in the sidebar to automatically parse and fetch data.")
    st.markdown("""
    ### Expected Message Format

    ```
    PR - Spike
    APP
     — 8:00 AM
    BNKK  < $.50c  - Bonk, Inc. Provides 2026 Guidance... - Link  ~  :flag_us:  |  Float: 139 M  |  IO: 6.04%  |  MC: 26.8 M
    MNTS  < $1  - Momentus Announces... - Link  ~  :flag_us:  |  Float: 23.9 M  |  IO: 1.06%  |  MC: 18.4 M
     — 9:00 AM
    JAGX  < $2  - FDA Approves... - Link  ~  :flag_us:  |  Float: 2.6 M  |  IO: 2.55%  |  MC: 4.4 M
    ```

    ### How to Use

    1. **Paste messages** from Discord into the sidebar - data will auto-fetch
    2. **Set reference date** (for "Yesterday" and "Today" timestamps)
    3. **Adjust triggers** using the sliders to see different strategy results
    4. **Click on a row** to see the detailed price chart
    """)
