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
    if "entry_by_message_second" not in st.session_state:
        st.session_state.entry_by_message_second = saved_config.get("entry_by_message_second", False)

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

    entry_by_message_second = st.checkbox(
        "Entry within first minute (by message second)",
        help="Uses the announcement timestamp seconds to approximate a fill inside the first 1-min candle. "
             "Only applies when Entry Trigger = 0% and Min Volume Threshold = 0.",
        key="entry_by_message_second",
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

    # FinBERT score range filter
    if "filter_finbert_min" not in st.session_state:
        st.session_state.filter_finbert_min = saved_config.get("filter_finbert_min", -1.0)
    if "filter_finbert_max" not in st.session_state:
        st.session_state.filter_finbert_max = saved_config.get("filter_finbert_max", 1.0)

    fin_col1, fin_col2 = st.columns(2)
    with fin_col1:
        filter_finbert_min = st.number_input(
            "FinBERT Min",
            min_value=-1.0,
            max_value=1.0,
            step=0.05,
            key="filter_finbert_min",
        )
    with fin_col2:
        filter_finbert_max = st.number_input(
            "FinBERT Max",
            min_value=-1.0,
            max_value=1.0,
            step=0.05,
            key="filter_finbert_max",
        )

    # Headline financing/dilution filter
    fin_headline_options = ["Any", "Exclude financing", "Only financing"]
    if "filter_financing" not in st.session_state:
        st.session_state.filter_financing = saved_config.get("filter_financing", "Any")
    filter_financing = st.selectbox(
        "Headline financing filter",
        fin_headline_options,
        index=fin_headline_options.index(st.session_state.filter_financing)
        if st.session_state.filter_financing in fin_headline_options
        else 0,
        key="filter_financing",
    )

    # Premarket gap + dollar volume filters
    if "filter_gap_min" not in st.session_state:
        st.session_state.filter_gap_min = saved_config.get("filter_gap_min", -100.0)
    if "filter_gap_max" not in st.session_state:
        st.session_state.filter_gap_max = saved_config.get("filter_gap_max", 100.0)
    gap_col1, gap_col2 = st.columns(2)
    with gap_col1:
        filter_gap_min = st.number_input(
            "Gap Min (%)",
            min_value=-100.0,
            max_value=100.0,
            step=1.0,
            key="filter_gap_min",
        )
    with gap_col2:
        filter_gap_max = st.number_input(
            "Gap Max (%)",
            min_value=-100.0,
            max_value=100.0,
            step=1.0,
            key="filter_gap_max",
        )

    if "filter_pre_dv_min_m" not in st.session_state:
        st.session_state.filter_pre_dv_min_m = saved_config.get("filter_pre_dv_min_m", 0.0)
    if "filter_pre_dv_max_m" not in st.session_state:
        st.session_state.filter_pre_dv_max_m = saved_config.get("filter_pre_dv_max_m", 1_000_000.0)
    dv_col1, dv_col2 = st.columns(2)
    with dv_col1:
        filter_pre_dv_min_m = st.number_input(
            "Premkt $Vol Min (M)",
            min_value=0.0,
            max_value=1_000_000.0,
            step=10.0,
            key="filter_pre_dv_min_m",
        )
    with dv_col2:
        filter_pre_dv_max_m = st.number_input(
            "Premkt $Vol Max (M)",
            min_value=0.0,
            max_value=1_000_000.0,
            step=10.0,
            key="filter_pre_dv_max_m",
        )

    # Price range filter (announcement price threshold)
    if "filter_price_min" not in st.session_state:
        st.session_state.filter_price_min = saved_config.get("filter_price_min", 0.0)
    if "filter_price_max" not in st.session_state:
        st.session_state.filter_price_max = saved_config.get("filter_price_max", 1000.0)

    price_col1, price_col2 = st.columns(2)
    with price_col1:
        filter_price_min = st.number_input(
            "Price Min ($)",
            min_value=0.0,
            max_value=1000.0,
            step=0.1,
            key="filter_price_min",
        )
    with price_col2:
        filter_price_max = st.number_input(
            "Price Max ($)",
            min_value=0.0,
            max_value=1000.0,
            step=0.1,
            key="filter_price_max",
        )

    # Float range filter (in millions for usability)
    if "filter_float_min_m" not in st.session_state:
        st.session_state.filter_float_min_m = saved_config.get("filter_float_min_m", 0.0)
    if "filter_float_max_m" not in st.session_state:
        st.session_state.filter_float_max_m = saved_config.get("filter_float_max_m", 1000.0)

    float_col1, float_col2 = st.columns(2)
    with float_col1:
        filter_float_min_m = st.number_input(
            "Float Min (M)",
            min_value=0.0,
            max_value=1000.0,
            step=1.0,
            key="filter_float_min_m",
        )
    with float_col2:
        filter_float_max_m = st.number_input(
            "Float Max (M)",
            min_value=0.0,
            max_value=1000.0,
            step=1.0,
            key="filter_float_max_m",
        )

    # Market cap range filter (in millions for usability)
    if "filter_mc_min_m" not in st.session_state:
        st.session_state.filter_mc_min_m = saved_config.get("filter_mc_min_m", 0.0)
    if "filter_mc_max_m" not in st.session_state:
        st.session_state.filter_mc_max_m = saved_config.get("filter_mc_max_m", 100000.0)

    mc_col1, mc_col2 = st.columns(2)
    with mc_col1:
        filter_mc_min_m = st.number_input(
            "MC Min (M)",
            min_value=0.0,
            max_value=100000.0,
            step=10.0,
            key="filter_mc_min_m",
        )
    with mc_col2:
        filter_mc_max_m = st.number_input(
            "MC Max (M)",
            min_value=0.0,
            max_value=100000.0,
            step=10.0,
            key="filter_mc_max_m",
        )

    # Scanner-specific filters (collapsed by default)
    with st.expander("Scanner Filters", expanded=False):
        # Scanner gain % range
        if "filter_scanner_gain_min" not in st.session_state:
            st.session_state.filter_scanner_gain_min = saved_config.get("filter_scanner_gain_min", 0.0)
        if "filter_scanner_gain_max" not in st.session_state:
            st.session_state.filter_scanner_gain_max = saved_config.get("filter_scanner_gain_max", 1000.0)

        gain_col1, gain_col2 = st.columns(2)
        with gain_col1:
            filter_scanner_gain_min = st.number_input(
                "Gain Min (%)",
                min_value=0.0,
                max_value=1000.0,
                step=5.0,
                key="filter_scanner_gain_min",
                help="Minimum percentage gain from previous close"
            )
        with gain_col2:
            filter_scanner_gain_max = st.number_input(
                "Gain Max (%)",
                min_value=0.0,
                max_value=1000.0,
                step=5.0,
                key="filter_scanner_gain_max",
                help="Maximum percentage gain from previous close"
            )

        # NHOD filter
        nhod_options = ["Any", "NHOD", "Not NHOD"]
        if "filter_nhod" not in st.session_state:
            st.session_state.filter_nhod = saved_config.get("filter_nhod", "Any")
        filter_nhod = st.selectbox(
            "New High of Day",
            nhod_options,
            index=nhod_options.index(st.session_state.filter_nhod) if st.session_state.filter_nhod in nhod_options else 0,
            key="filter_nhod",
        )

        # NSH filter
        nsh_options = ["Any", "NSH", "Not NSH"]
        if "filter_nsh" not in st.session_state:
            st.session_state.filter_nsh = saved_config.get("filter_nsh", "Any")
        filter_nsh = st.selectbox(
            "New Session High",
            nsh_options,
            index=nsh_options.index(st.session_state.filter_nsh) if st.session_state.filter_nsh in nsh_options else 0,
            key="filter_nsh",
        )

        # Has news filter
        news_options = ["Any", "Has news", "No news (scanner only)"]
        if "filter_has_news" not in st.session_state:
            st.session_state.filter_has_news = saved_config.get("filter_has_news", "Any")
        filter_has_news = st.selectbox(
            "News Source",
            news_options,
            index=news_options.index(st.session_state.filter_has_news) if st.session_state.filter_has_news in news_options else 0,
            key="filter_has_news",
            help="Filter by whether announcement has PR/AR/SEC news or is scanner-only"
        )

        # RVol range
        if "filter_rvol_min" not in st.session_state:
            st.session_state.filter_rvol_min = saved_config.get("filter_rvol_min", 0.0)
        if "filter_rvol_max" not in st.session_state:
            st.session_state.filter_rvol_max = saved_config.get("filter_rvol_max", 100.0)

        rvol_col1, rvol_col2 = st.columns(2)
        with rvol_col1:
            filter_rvol_min = st.number_input(
                "RVol Min",
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                key="filter_rvol_min",
                help="Minimum relative volume ratio"
            )
        with rvol_col2:
            filter_rvol_max = st.number_input(
                "RVol Max",
                min_value=0.0,
                max_value=100.0,
                step=0.5,
                key="filter_rvol_max",
            )

        # Mention count range
        if "filter_mentions_min" not in st.session_state:
            st.session_state.filter_mentions_min = saved_config.get("filter_mentions_min", 0)
        if "filter_mentions_max" not in st.session_state:
            st.session_state.filter_mentions_max = saved_config.get("filter_mentions_max", 100)

        mention_col1, mention_col2 = st.columns(2)
        with mention_col1:
            filter_mentions_min = st.number_input(
                "Mentions Min",
                min_value=0,
                max_value=100,
                step=1,
                key="filter_mentions_min",
                help="Minimum mention count (• N)"
            )
        with mention_col2:
            filter_mentions_max = st.number_input(
                "Mentions Max",
                min_value=0,
                max_value=100,
                step=1,
                key="filter_mentions_max",
            )

        # Scanner type filters
        scanner_test_options = ["Any", "Test scanner", "Not test scanner"]
        if "filter_scanner_test" not in st.session_state:
            st.session_state.filter_scanner_test = saved_config.get("filter_scanner_test", "Any")
        filter_scanner_test = st.selectbox(
            "Test Scanner",
            scanner_test_options,
            index=scanner_test_options.index(st.session_state.filter_scanner_test) if st.session_state.filter_scanner_test in scanner_test_options else 0,
            key="filter_scanner_test",
        )

        after_lull_options = ["Any", "After lull", "Not after lull"]
        if "filter_after_lull" not in st.session_state:
            st.session_state.filter_after_lull = saved_config.get("filter_after_lull", "Any")
        filter_after_lull = st.selectbox(
            "After Lull Scanner",
            after_lull_options,
            index=after_lull_options.index(st.session_state.filter_after_lull) if st.session_state.filter_after_lull in after_lull_options else 0,
            key="filter_after_lull",
        )

        # Green bars range
        if "filter_green_bars_min" not in st.session_state:
            st.session_state.filter_green_bars_min = saved_config.get("filter_green_bars_min", 0)
        if "filter_green_bars_max" not in st.session_state:
            st.session_state.filter_green_bars_max = saved_config.get("filter_green_bars_max", 20)

        gb_col1, gb_col2 = st.columns(2)
        with gb_col1:
            filter_green_bars_min = st.number_input(
                "Green Bars Min",
                min_value=0,
                max_value=20,
                step=1,
                key="filter_green_bars_min",
            )
        with gb_col2:
            filter_green_bars_max = st.number_input(
                "Green Bars Max",
                min_value=0,
                max_value=20,
                step=1,
                key="filter_green_bars_max",
            )

    # Save config when values change
    current_config = {
        "entry_trigger": entry_trigger,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "volume_threshold": volume_threshold,
        "window_minutes": window_minutes,
        "entry_by_message_second": entry_by_message_second,
        "filter_premarket": filter_premarket,
        "filter_market": filter_market,
        "filter_postmarket": filter_postmarket,
        "filter_closed": filter_closed,
        "filter_ctb": filter_ctb,
        "filter_io_min": filter_io_min,
        "filter_io_max": filter_io_max,
        "filter_finbert_min": filter_finbert_min,
        "filter_finbert_max": filter_finbert_max,
        "filter_financing": filter_financing,
        "filter_gap_min": filter_gap_min,
        "filter_gap_max": filter_gap_max,
        "filter_pre_dv_min_m": filter_pre_dv_min_m,
        "filter_pre_dv_max_m": filter_pre_dv_max_m,
        "filter_price_min": filter_price_min,
        "filter_price_max": filter_price_max,
        "filter_float_min_m": filter_float_min_m,
        "filter_float_max_m": filter_float_max_m,
        "filter_mc_min_m": filter_mc_min_m,
        "filter_mc_max_m": filter_mc_max_m,
        # Scanner filters
        "filter_scanner_gain_min": filter_scanner_gain_min,
        "filter_scanner_gain_max": filter_scanner_gain_max,
        "filter_nhod": filter_nhod,
        "filter_nsh": filter_nsh,
        "filter_has_news": filter_has_news,
        "filter_rvol_min": filter_rvol_min,
        "filter_rvol_max": filter_rvol_max,
        "filter_mentions_min": filter_mentions_min,
        "filter_mentions_max": filter_mentions_max,
        "filter_scanner_test": filter_scanner_test,
        "filter_after_lull": filter_after_lull,
        "filter_green_bars_min": filter_green_bars_min,
        "filter_green_bars_max": filter_green_bars_max,
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

    # Apply FinBERT score range filter
    if filter_finbert_min > -1 or filter_finbert_max < 1:
        announcements = [
            a for a in announcements
            if a.finbert_score is not None and filter_finbert_min <= a.finbert_score <= filter_finbert_max
        ]

    # Apply headline financing filter
    if filter_financing == "Exclude financing":
        announcements = [a for a in announcements if not bool(a.headline_is_financing)]
    elif filter_financing == "Only financing":
        announcements = [a for a in announcements if bool(a.headline_is_financing)]

    # Apply premarket gap filter
    if filter_gap_min > -100 or filter_gap_max < 100:
        announcements = [
            a for a in announcements
            if a.premarket_gap_pct is not None and filter_gap_min <= a.premarket_gap_pct <= filter_gap_max
        ]

    # Apply premarket dollar volume filter (stored in M, compare in dollars)
    pre_dv_min = filter_pre_dv_min_m * 1e6
    pre_dv_max = filter_pre_dv_max_m * 1e6
    if pre_dv_min > 0 or filter_pre_dv_max_m < 1_000_000:
        announcements = [
            a for a in announcements
            if a.premarket_dollar_volume is not None and pre_dv_min <= a.premarket_dollar_volume <= pre_dv_max
        ]

    # Apply Price range filter
    if filter_price_min > 0 or filter_price_max < 1000:
        announcements = [
            a for a in announcements
            if a.price_threshold is not None and filter_price_min <= a.price_threshold <= filter_price_max
        ]

    # Apply Float range filter (stored in M, compare in shares)
    float_min = filter_float_min_m * 1e6
    float_max = filter_float_max_m * 1e6
    if float_min > 0 or filter_float_max_m < 1000:
        announcements = [
            a for a in announcements
            if a.float_shares is not None and float_min <= a.float_shares <= float_max
        ]

    # Apply Market cap range filter (stored in M, compare in dollars)
    mc_min = filter_mc_min_m * 1e6
    mc_max = filter_mc_max_m * 1e6
    if mc_min > 0 or filter_mc_max_m < 100000:
        announcements = [
            a for a in announcements
            if a.market_cap is not None and mc_min <= a.market_cap <= mc_max
        ]

    # === Scanner-specific filters ===

    # Scanner gain % filter
    if filter_scanner_gain_min > 0 or filter_scanner_gain_max < 1000:
        announcements = [
            a for a in announcements
            if a.scanner_gain_pct is None or (filter_scanner_gain_min <= a.scanner_gain_pct <= filter_scanner_gain_max)
        ]

    # NHOD filter
    if filter_nhod == "NHOD":
        announcements = [a for a in announcements if a.is_nhod]
    elif filter_nhod == "Not NHOD":
        announcements = [a for a in announcements if not a.is_nhod]

    # NSH filter
    if filter_nsh == "NSH":
        announcements = [a for a in announcements if a.is_nsh]
    elif filter_nsh == "Not NSH":
        announcements = [a for a in announcements if not a.is_nsh]

    # Has news filter
    if filter_has_news == "Has news":
        announcements = [a for a in announcements if a.has_news]
    elif filter_has_news == "No news (scanner only)":
        announcements = [a for a in announcements if not a.has_news]

    # RVol filter
    if filter_rvol_min > 0 or filter_rvol_max < 100:
        announcements = [
            a for a in announcements
            if a.rvol is None or (filter_rvol_min <= a.rvol <= filter_rvol_max)
        ]

    # Mention count filter
    if filter_mentions_min > 0 or filter_mentions_max < 100:
        announcements = [
            a for a in announcements
            if a.mention_count is None or (filter_mentions_min <= a.mention_count <= filter_mentions_max)
        ]

    # Scanner test filter
    if filter_scanner_test == "Test scanner":
        announcements = [a for a in announcements if a.scanner_test]
    elif filter_scanner_test == "Not test scanner":
        announcements = [a for a in announcements if not a.scanner_test]

    # After lull filter
    if filter_after_lull == "After lull":
        announcements = [a for a in announcements if a.scanner_after_lull]
    elif filter_after_lull == "Not after lull":
        announcements = [a for a in announcements if not a.scanner_after_lull]

    # Green bars filter
    if filter_green_bars_min > 0 or filter_green_bars_max < 20:
        announcements = [
            a for a in announcements
            if a.green_bars is None or (filter_green_bars_min <= a.green_bars <= filter_green_bars_max)
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
        entry_at_candle_close=False,
        entry_by_message_second=entry_by_message_second,
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

        # Quick FinBERT alpha exploration
        if any(a.finbert_score is not None for a in announcements):
            with st.expander("FinBERT alpha scan", expanded=False):
                n = min(len(announcements), len(st.session_state.results))
                rows = []
                for i in range(n):
                    a = announcements[i]
                    r = st.session_state.results[i]
                    rows.append(
                        {
                            "ticker": a.ticker,
                            "time": a.timestamp,
                            "finbert_score": a.finbert_score,
                            "finbert_label": a.finbert_label,
                            "headline_is_financing": a.headline_is_financing,
                            "headline_financing_type": a.headline_financing_type,
                            "premarket_gap_pct": a.premarket_gap_pct,
                            "premarket_dollar_volume": a.premarket_dollar_volume,
                            "return_pct": r.return_pct,
                            "trigger_type": r.trigger_type,
                        }
                    )
                df_ab = pd.DataFrame(rows)

                # Focus on rows with realized returns (entered trades)
                df_entered = df_ab[df_ab["return_pct"].notna() & df_ab["finbert_score"].notna()].copy()
                if df_entered.empty:
                    st.info("No entered trades with FinBERT scores in the current filtered set.")
                else:
                    df_entered["win"] = df_entered["return_pct"] > 0

                    # By sentiment label
                    by_label = (
                        df_entered.groupby("finbert_label", dropna=False)
                        .agg(
                            n=("return_pct", "size"),
                            win_rate=("win", "mean"),
                            avg_return=("return_pct", "mean"),
                            median_return=("return_pct", "median"),
                        )
                        .reset_index()
                        .sort_values(["avg_return", "n"], ascending=[False, False])
                    )
                    by_label["win_rate"] = (by_label["win_rate"] * 100).round(1)
                    by_label["avg_return"] = by_label["avg_return"].round(2)
                    by_label["median_return"] = by_label["median_return"].round(2)
                    st.subheader("By FinBERT label (entered trades)")
                    st.dataframe(by_label, use_container_width=True, hide_index=True)

                    # By financing flag/type
                    st.subheader("By financing flag/type (entered trades)")
                    df_entered_fin = df_entered.copy()
                    df_entered_fin["headline_is_financing"] = df_entered_fin["headline_is_financing"].fillna(False)
                    by_fin = (
                        df_entered_fin.groupby(["headline_is_financing", "headline_financing_type"], dropna=False)
                        .agg(
                            n=("return_pct", "size"),
                            win_rate=("win", "mean"),
                            avg_return=("return_pct", "mean"),
                            median_return=("return_pct", "median"),
                        )
                        .reset_index()
                        .sort_values(["avg_return", "n"], ascending=[False, False])
                    )
                    by_fin["win_rate"] = (by_fin["win_rate"] * 100).round(1)
                    by_fin["avg_return"] = by_fin["avg_return"].round(2)
                    by_fin["median_return"] = by_fin["median_return"].round(2)
                    st.dataframe(by_fin, use_container_width=True, hide_index=True)

                    # Gap buckets (if present)
                    if df_entered["premarket_gap_pct"].notna().any():
                        st.subheader("By premarket gap bucket (entered trades)")
                        gap_bins = [-1000, -10, -5, -2, 0, 2, 5, 10, 1000]
                        gap_labels = ["<-10", "-10..-5", "-5..-2", "-2..0", "0..2", "2..5", "5..10", ">10"]
                        df_entered["gap_bucket"] = pd.cut(df_entered["premarket_gap_pct"], bins=gap_bins, labels=gap_labels)
                        by_gap = (
                            df_entered.groupby("gap_bucket", dropna=False)
                            .agg(
                                n=("return_pct", "size"),
                                win_rate=("win", "mean"),
                                avg_return=("return_pct", "mean"),
                                median_return=("return_pct", "median"),
                            )
                            .reset_index()
                        )
                        by_gap["win_rate"] = (by_gap["win_rate"] * 100).round(1)
                        by_gap["avg_return"] = by_gap["avg_return"].round(2)
                        by_gap["median_return"] = by_gap["median_return"].round(2)
                        st.dataframe(by_gap, use_container_width=True, hide_index=True)

                    # Fixed score buckets
                    bins = [-1.01, -0.6, -0.2, 0.2, 0.6, 1.01]
                    labels = ["[-1,-0.6)", "[-0.6,-0.2)", "[-0.2,0.2)", "[0.2,0.6)", "[0.6,1]"]
                    df_entered["score_bucket"] = pd.cut(df_entered["finbert_score"], bins=bins, labels=labels)
                    by_bucket = (
                        df_entered.groupby("score_bucket", dropna=False)
                        .agg(
                            n=("return_pct", "size"),
                            win_rate=("win", "mean"),
                            avg_return=("return_pct", "mean"),
                            median_return=("return_pct", "median"),
                        )
                        .reset_index()
                    )
                    by_bucket["win_rate"] = (by_bucket["win_rate"] * 100).round(1)
                    by_bucket["avg_return"] = by_bucket["avg_return"].round(2)
                    by_bucket["median_return"] = by_bucket["median_return"].round(2)
                    st.subheader("By FinBERT score bucket (entered trades)")
                    st.dataframe(by_bucket, use_container_width=True, hide_index=True)

                    # Threshold sweep (>= t and <= t)
                    thresholds = [-0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8]

                    def _sweep(mask_name: str, mask):
                        d = df_entered[mask]
                        if d.empty:
                            return None
                        return {
                            "filter": mask_name,
                            "n": int(d.shape[0]),
                            "win_rate": float((d["return_pct"] > 0).mean() * 100),
                            "avg_return": float(d["return_pct"].mean()),
                            "median_return": float(d["return_pct"].median()),
                        }

                    ge_rows = []
                    le_rows = []
                    for t in thresholds:
                        ge = _sweep(f"score >= {t:+.1f}", df_entered["finbert_score"] >= t)
                        le = _sweep(f"score <= {t:+.1f}", df_entered["finbert_score"] <= t)
                        if ge:
                            ge_rows.append(ge)
                        if le:
                            le_rows.append(le)

                    if ge_rows:
                        df_ge = pd.DataFrame(ge_rows).sort_values(["avg_return", "n"], ascending=[False, False])
                        df_ge["win_rate"] = df_ge["win_rate"].round(1)
                        df_ge["avg_return"] = df_ge["avg_return"].round(2)
                        df_ge["median_return"] = df_ge["median_return"].round(2)
                        st.subheader("Threshold sweep: score >= t (entered trades)")
                        st.dataframe(df_ge, use_container_width=True, hide_index=True)

                    if le_rows:
                        df_le = pd.DataFrame(le_rows).sort_values(["avg_return", "n"], ascending=[False, False])
                        df_le["win_rate"] = df_le["win_rate"].round(1)
                        df_le["avg_return"] = df_le["avg_return"].round(2)
                        df_le["median_return"] = df_le["median_return"].round(2)
                        st.subheader("Threshold sweep: score <= t (entered trades)")
                        st.dataframe(df_le, use_container_width=True, hide_index=True)

    # Announcements table
    st.header("Announcements")

    # Sort controls - use callbacks to persist values
    sort_options = ["Time (EST)", "Ticker", "Session", "FinBERT", "Return", "Status"]

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

        # Build scanner flags string
        scanner_flags = []
        if ann.is_nhod:
            scanner_flags.append("NHOD")
        if ann.is_nsh:
            scanner_flags.append("NSH")
        if ann.scanner_test:
            scanner_flags.append("test")
        if ann.scanner_after_lull:
            scanner_flags.append("lull")

        row = {
            "_original_idx": i,  # Hidden column to track original index
            "Ticker": ann.ticker,
            "Channel": ann.channel or "-",
            "Headline": (ann.headline[:40] + "...") if ann.headline and len(ann.headline) > 40 else (ann.headline or "-"),
            "Time (EST)": ann.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "Session": ann.market_session.capitalize(),
            "Price": f"${ann.price_threshold:.2f}" if ann.price_threshold else "N/A",
            "Float": f"{ann.float_shares/1e6:.1f}M" if ann.float_shares else "N/A",
            "IO%": f"{ann.io_percent:.1f}%" if ann.io_percent is not None else "N/A",
            "MC": f"${ann.market_cap/1e6:.1f}M" if ann.market_cap else "N/A",
            "SI%": f"{ann.short_interest:.1f}%" if ann.short_interest is not None else "N/A",
            "CTB": "High" if ann.high_ctb else "-",
            "Country": ann.country,
            "FinBERT": f"{ann.finbert_score:+.3f}" if ann.finbert_score is not None else "N/A",
            "Sentiment": ann.finbert_label or "N/A",
            "Gap%": f"{ann.premarket_gap_pct:+.1f}%" if ann.premarket_gap_pct is not None else "N/A",
            "Premkt $Vol": f"${ann.premarket_dollar_volume/1e6:.1f}M" if ann.premarket_dollar_volume is not None else "N/A",
            "Financing": "Yes" if ann.headline_is_financing else "-",
            # Scanner fields
            "Gain%": f"{ann.scanner_gain_pct:.0f}%" if ann.scanner_gain_pct is not None else "-",
            "RVol": f"{ann.rvol:.1f}" if ann.rvol is not None else "-",
            "Mentions": str(ann.mention_count) if ann.mention_count is not None else "-",
            "Flags": ",".join(scanner_flags) if scanner_flags else "-",
            "News": "Yes" if ann.has_news else "No",
            "Return": f"{result.return_pct:.2f}%" if result and result.return_pct is not None else "N/A",
            "_finbert_numeric": ann.finbert_score if ann.finbert_score is not None else float("-inf"),
            "_return_numeric": return_val,  # Hidden column for sorting
            "Status": status,
        }
        table_data.append(row)

    if table_data:
        df = pd.DataFrame(table_data)

        # Sort the dataframe
        if sort_column == "Return":
            df = df.sort_values("_return_numeric", ascending=sort_ascending)
        elif sort_column == "FinBERT":
            df = df.sort_values("_finbert_numeric", ascending=sort_ascending)
        else:
            df = df.sort_values(sort_column, ascending=sort_ascending)

        # Store mapping from display row to original index
        display_to_original = df["_original_idx"].tolist()

        # Remove hidden columns for display
        display_df = df.drop(columns=["_original_idx", "_return_numeric", "_finbert_numeric"])

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
                    "finbert_label": ann.finbert_label,
                    "finbert_score": ann.finbert_score,
                    "finbert_pos": ann.finbert_pos,
                    "finbert_neg": ann.finbert_neg,
                    "finbert_neu": ann.finbert_neu,
                    "headline_is_financing": ann.headline_is_financing,
                    "headline_financing_type": ann.headline_financing_type,
                    "headline_financing_tags": ann.headline_financing_tags,
                    "prev_close": ann.prev_close,
                    "regular_open": ann.regular_open,
                    "premarket_gap_pct": ann.premarket_gap_pct,
                    "premarket_volume": ann.premarket_volume,
                    "premarket_dollar_volume": ann.premarket_dollar_volume,
                    # Scanner fields
                    "channel": ann.channel,
                    "scanner_gain_pct": ann.scanner_gain_pct,
                    "is_nhod": ann.is_nhod,
                    "is_nsh": ann.is_nsh,
                    "rvol": ann.rvol,
                    "mention_count": ann.mention_count,
                    "has_news": ann.has_news,
                    "green_bars": ann.green_bars,
                    "bar_minutes": ann.bar_minutes,
                    "scanner_test": ann.scanner_test,
                    "scanner_after_lull": ann.scanner_after_lull,
                    # Trade results
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
