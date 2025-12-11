import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from dotenv import load_dotenv

import json
from pathlib import Path

from src.parser import parse_discord_messages
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

    messages_input = st.text_area(
        "Paste Discord messages here:",
        height=200,
        placeholder="""PR - Spike
APP
 — 8:00 AM
BNKK  < $.50c  - Bonk, Inc. Provides 2026 Guidance... - Link  ~  :flag_us:  |  Float: 139 M  |  IO: 6.04%  |  MC: 26.8 M""",
    )

    reference_date = st.date_input(
        "Reference date (for relative timestamps):",
        value=datetime.now().date(),
    )

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
    }
    if current_config != saved_config:
        save_config(current_config)


# Auto-parse when text input changes
if messages_input.strip() and messages_input != st.session_state.last_messages_input:
    st.session_state.last_messages_input = messages_input
    ref_datetime = datetime.combine(reference_date, datetime.min.time())
    new_announcements = parse_discord_messages(messages_input, ref_datetime)

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
            # Fetch OHLCV data for newly added announcements only
            client = MassiveClient()

            # Filter to only announcements that need fetching (not already cached)
            to_fetch = [
                ann for ann in actually_added
                if (ann.ticker, ann.timestamp) not in st.session_state.bars_by_announcement
            ]

            if to_fetch:
                progress_bar = st.progress(0, text="Preparing to fetch data...")
                status_text = st.empty()
                cancel_button = st.button("Cancel Import", type="secondary")
                cancelled = False

                for i, ann in enumerate(to_fetch):
                    if cancel_button or cancelled:
                        status_text.text(f"Import cancelled after {i} tickers")
                        cancelled = True
                        break

                    status_text.text(f"Fetching {ann.ticker} ({i + 1}/{len(to_fetch)})")
                    progress_bar.progress((i + 1) / len(to_fetch), text=f"Fetching {ann.ticker}...")

                    key = (ann.ticker, ann.timestamp)
                    bars = client.fetch_after_announcement(
                        ann.ticker,
                        ann.timestamp,
                        window_minutes,
                    )
                    st.session_state.bars_by_announcement[key] = bars

                if not cancelled:
                    progress_bar.progress(1.0, text="Complete!")
                    status_text.text(f"Fetched data for {len(to_fetch)} tickers")

            # Save all announcements to cache (even partial)
            client.save_announcements(st.session_state.announcements)

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

    # Filter announcements by session
    all_announcements = st.session_state.announcements
    if allowed_sessions:
        announcements = [a for a in all_announcements if a.market_session in allowed_sessions]
    else:
        # If no filters selected, show nothing (or could show all)
        announcements = []

    if not announcements:
        st.warning("No announcements match the current session filters. Check at least one session filter.")

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

        # Store numeric return for sorting
        return_val = result.return_pct if result and result.return_pct is not None else float('-inf')

        row = {
            "_original_idx": i,  # Hidden column to track original index
            "Ticker": ann.ticker,
            "Time (EST)": ann.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "Session": ann.market_session.capitalize(),
            "Price": f"${ann.price_threshold:.2f}" if ann.price_threshold else "N/A",
            "Float": f"{ann.float_shares/1e6:.1f}M" if ann.float_shares else "N/A",
            "IO%": f"{ann.io_percent:.1f}%" if ann.io_percent else "N/A",
            "MC": f"${ann.market_cap/1e6:.1f}M" if ann.market_cap else "N/A",
            "Country": ann.country,
            "Return": f"{result.return_pct:.2f}%" if result and result.return_pct is not None else "N/A",
            "_return_numeric": return_val,  # Hidden column for sorting
            "Status": result.trigger_type if result else "pending",
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
