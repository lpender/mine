"""Streamlit dashboard for backtesting press release announcements."""

import logging
import os
import random
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo

from src.postgres_client import get_postgres_client

# Timezone for display
EST = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _setup_dashboard_logging() -> logging.Logger:
    """
    Configure dashboard logging to a file so "stuck" work becomes visible.
    Safe to call multiple times (Streamlit reruns).
    """
    level_name = (os.getenv("DASHBOARD_LOG_LEVEL", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_path = os.getenv("DASHBOARD_LOG_FILE", "logs/dashboard.log") or "logs/dashboard.log"

    logger = logging.getLogger("dashboard")
    logger.setLevel(level)

    # Root logger may already have handlers; we only add our dashboard file handler once.
    root = logging.getLogger()
    root.setLevel(min(root.level, level) if root.level else level)

    # Ensure log dir exists
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    already = False
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "").endswith(str(Path(log_path))):
            already = True
            break

    if not already:
        fh = logging.FileHandler(log_path)
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)

    # Avoid chatty third-party logs unless explicitly requested
    if level > logging.DEBUG:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger.info("Dashboard logging enabled (level=%s file=%s pid=%s)", level_name, log_path, os.getpid())
    return logger


LOGGER = _setup_dashboard_logging()


@contextmanager
def log_time(label: str, **fields):
    """Lightweight timing logger for dashboard hotspots."""
    t0 = perf_counter()
    if fields:
        LOGGER.info("START %s %s", label, " ".join(f"{k}={v}" for k, v in fields.items()))
    else:
        LOGGER.info("START %s", label)
    try:
        yield
    finally:
        dt = perf_counter() - t0
        LOGGER.info("END   %s took=%.2fs", label, dt)


def to_est(dt):
    """Convert a datetime to EST for display. Assumes naive datetimes are UTC."""
    if dt is None:
        return None
    # Assume naive datetimes are UTC (all DB timestamps are stored in UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(EST)


def explain_trigger_type(trigger_type: str, config) -> str:
    """Provide detailed explanation of why a backtest trade exited."""
    explanations = {
        "take_profit": f"✅ **Take Profit Hit** - Price reached the {config.take_profit_pct}% profit target",
        "stop_loss": f"🛑 **Stop Loss Hit** - Price dropped to the {config.stop_loss_pct}% stop loss level" +
                     (f" (calculated from first candle open)" if config.stop_loss_from_open else " (calculated from entry price)"),
        "trailing_stop": f"📉 **Trailing Stop Hit** - Price dropped {config.trailing_stop_pct}% from the highest point reached during the trade",
        "timeout": f"⏰ **Timeout** - Trade held for maximum duration of {config.window_minutes} minutes without hitting TP/SL",
        "no_entry": "❌ **No Entry** - Entry conditions were not met within the entry window",
    }
    return explanations.get(trigger_type, f"ℹ️ {trigger_type}")


def explain_backtest_entry(config) -> str:
    """Provide detailed explanation of backtest entry conditions."""
    consec = config.entry_after_consecutive_candles
    min_vol = config.min_candle_volume
    window = config.entry_window_minutes

    if consec > 0:
        vol_text = f" with {min_vol:,}+ volume each" if min_vol > 0 else ""
        return f"📊 **Entry Condition**: Wait for {consec} consecutive green candle{'s' if consec > 1 else ''}{vol_text} within {window} minute window after alert"
    else:
        return f"📊 **Entry Condition**: Entry at candle close within {window} minute window after alert"


def get_backtest_chart_legend() -> str:
    """Return explanation of backtest chart elements."""
    return """
    **Chart Elements:**
    - 🟢/🔴 **Candlesticks**: 1-minute OHLCV bars (green = close > open, red = close < open)
    - 🔵 **Blue Circle**: Entry point (where simulated position was opened)
    - ❌ **X Marker**: Exit point (green X = profitable exit, red X = loss)
    - 🔵 **Blue Solid Line**: Entry price level
    - 🟢 **Green Dashed Line**: Take Profit (TP) target price
    - 🔴 **Red Dashed Line**: Stop Loss (SL) trigger price

    **Note**: Bars are displayed using "end-time" convention (like WeBull), so a 10:05 bar represents trading from 10:04 to 10:05.
    """


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
    client = get_postgres_client()
    return client.load_announcements()

@st.cache_data(ttl=300)
def load_filter_options():
    """Load distinct filter options from PostgreSQL (fast; avoids loading all announcements)."""
    client = get_postgres_client()
    return client.get_announcement_filter_options(source="backfill")

@st.cache_data(ttl=300)
def load_sampled_filtered_announcements(
    *,
    sample_pct: int,
    sample_seed: int,
    sessions: tuple,
    countries: tuple,
    country_blacklist: tuple,
    authors: tuple,
    channels: tuple,
    directions: tuple,
    scanner_test: bool,
    scanner_after_lull: bool,
    max_mentions: int,
    exclude_financing_headlines: bool,
    require_headline: bool,
    exclude_headline: bool,
    float_min: float,
    float_max: float,
    mc_min: float,
    mc_max: float,
    prior_move_min: float,
    prior_move_max: float,
    nhod_filter: str,
    nsh_filter: str,
    rvol_min: float,
    rvol_max: float,
    exclude_financing_types: tuple,
    exclude_biotech: bool,
):
    client = get_postgres_client()
    return client.load_announcements_sampled_and_filtered(
        source="backfill",
        sample_pct=sample_pct,
        sample_seed=sample_seed,
        sessions=list(sessions) if sessions else None,
        countries=list(countries) if countries else None,
        country_blacklist=list(country_blacklist) if country_blacklist else None,
        authors=list(authors) if authors else None,
        channels=list(channels) if channels else None,
        directions=list(directions) if directions else None,
        scanner_test=scanner_test,
        scanner_after_lull=scanner_after_lull,
        max_mentions=max_mentions if max_mentions and max_mentions > 0 else None,
        exclude_financing_headlines=exclude_financing_headlines,
        require_headline=require_headline,
        exclude_headline=exclude_headline,
        float_min_m=float_min,
        float_max_m=float_max,
        mc_min_m=mc_min,
        mc_max_m=mc_max,
        prior_move_min=prior_move_min,
        prior_move_max=prior_move_max,
        nhod_filter=nhod_filter,
        nsh_filter=nsh_filter,
        rvol_min=rvol_min,
        rvol_max=rvol_max,
        exclude_financing_types=list(exclude_financing_types) if exclude_financing_types else None,
        exclude_biotech=exclude_biotech,
    )


@st.cache_data(ttl=300)
def load_ohlcv_for_announcements(announcement_keys: tuple, window_minutes: int):
    """Load OHLCV bars for a set of announcements.

    Note: Both announcements and OHLCV bars are stored in UTC (naive).
    Uses bulk query via announcement_ticker/announcement_timestamp columns
    for much faster loading (single query instead of N queries).
    """
    client = get_postgres_client()

    # Convert string timestamps to datetime for bulk query.
    # Avoid pandas here: this runs on every cache miss and can get expensive for 10k+ keys.
    keys_with_dt = []
    for ticker, timestamp_str in announcement_keys:
        ts = datetime.fromisoformat(timestamp_str)
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.replace(tzinfo=None)
        keys_with_dt.append((ticker, ts))

    # Single bulk query for all announcements
    bars_by_announcement = client.get_ohlcv_bars_bulk(keys_with_dt)

    return bars_by_announcement


# ─────────────────────────────────────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────────────────────────────────────

st.title("PR Backtest Dashboard")

# Load distinct filter values (fast)
with log_time("load_filter_options"):
    opts = load_filter_options()

all_countries = opts.get("countries", [])
all_authors = opts.get("authors", [])
all_channels = opts.get("channels", [])
all_sessions = ["premarket", "market", "postmarket", "closed"]
all_directions = opts.get("directions", [])

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
    set_if_missing("_tp", max(1.0, min(1000.0, tp_val)) if tp_val > 0 else 10.0)
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
    entry_window_val = get_param("entry_window", 5, int)
    set_if_missing("_entry_window", max(1, min(30, entry_window_val)) if entry_window_val > 0 else 5)
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
    # New filters from strategy
    set_if_missing("_max_mentions", get_param("max_mentions", 0, int))
    set_if_missing("_exclude_biotech", get_param("exclude_biotech", False, bool))
    # Prior move filter
    prior_move_val = get_param("max_prior_move", 0.0, float)
    set_if_missing("_prior_move_max", prior_move_val if prior_move_val > 0 else 0.0)
    # Market cap filter from URL (convert to the mc_max widget if provided)
    max_mcap_val = get_param("max_mcap", 0.0, float)
    if max_mcap_val > 0:
        set_if_missing("_mc_max", max_mcap_val)

init_session_state()

with st.sidebar:
    # ─────────────────────────────────────────────────────────────────────────
    # Sampling (for faster iteration) - FIRST in execution order
    # ─────────────────────────────────────────────────────────────────────────
    st.header("Sampling")

    sample_pct = st.slider(
        "Sample Size %",
        min_value=1,
        max_value=100,
        value=int(get_param("sample_pct", 100) or 100),
        step=1,
        key="_sample_pct",
        help="Test on random subset for faster iteration (100% = all data)"
    )

    sample_seed = st.number_input(
        "Random Seed",
        value=int(get_param("sample_seed", 0) or 0),
        min_value=0,
        step=1,
        key="_sample_seed",
        help="0 = different sample each run, >0 = reproducible sample"
    )

    set_param("sample_pct", sample_pct if sample_pct < 100 else "")
    set_param("sample_seed", sample_seed if sample_seed > 0 else "")

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
    _max_mentions_key = "_max_mentions"
    if _max_mentions_key not in st.session_state or st.query_params.get("max_mentions"):
        st.session_state[_max_mentions_key] = int(get_param("max_mentions", 0) or 0)
    max_mentions = st.number_input(
        "Max Intraday Mentions",
        min_value=0,
        max_value=100,
        key=_max_mentions_key,
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

    # Financing filter (boolean)
    exclude_financing_headlines = st.checkbox(
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

    # NHOD / NSH filters
    st.subheader("Price Action Filters")
    nhod_filter = st.selectbox(
        "NHOD (New High of Day)",
        options=["Any", "Yes", "No"],
        key="_nhod_filter",
        help="Filter by New High of Day status"
    )
    nsh_filter = st.selectbox(
        "NSH (New Session High)",
        options=["Any", "Yes", "No"],
        key="_nsh_filter",
        help="Filter by New Session High status. Note: NSH=No may perform better!"
    )

    # RVol filter
    st.subheader("Relative Volume")
    col1, col2 = st.columns(2)
    rvol_min = col1.number_input("Min RVol", min_value=0.0, step=1.0, key="_rvol_min",
                                  help="Minimum relative volume")
    rvol_max = col2.number_input("Max RVol", min_value=0.0, value=0.0, step=5.0, key="_rvol_max",
                                  help="Maximum relative volume (0 = no limit). Note: Lower RVol may perform better!")

    # Float range
    st.subheader("Float (millions)")
    col1, col2 = st.columns(2)
    float_min = col1.number_input("Min", min_value=0.0, step=1.0, key="_float_min")
    float_max = col2.number_input("Max", min_value=0.0, step=10.0, key="_float_max")

    # Market cap range
    st.subheader("Market Cap (millions)")
    col1, col2 = st.columns(2)
    mc_min = col1.number_input("Min", min_value=0.0, step=1.0, key="_mc_min")
    _mc_max_key = "_mc_max"
    if _mc_max_key not in st.session_state or st.query_params.get("max_mcap"):
        _mc_max_val = get_param("max_mcap", 0.0, float)
        st.session_state[_mc_max_key] = _mc_max_val if _mc_max_val > 0 else 0.0
    mc_max = col2.number_input("Max", min_value=0.0, step=100.0, key=_mc_max_key)

    # Prior move filter (scanner_gain_pct)
    st.subheader("Prior Move Filter")
    col1, col2 = st.columns(2)
    prior_move_min = col1.number_input("Min %", min_value=0.0, step=5.0, key="_prior_move_min",
                                        help="Only include if stock already moved at least this % before alert")
    _prior_move_max_key = "_prior_move_max"
    if _prior_move_max_key not in st.session_state or st.query_params.get("max_prior_move"):
        st.session_state[_prior_move_max_key] = get_param("max_prior_move", 0.0, float)
    prior_move_max = col2.number_input("Max %", min_value=0.0, step=10.0, key=_prior_move_max_key,
                                        help="Exclude if stock already moved more than this % before alert (0 = no limit)")

    # Headline type filter (financing)
    st.subheader("Headline Type Filter")
    exclude_financing_types = st.multiselect(
        "Exclude financing types",
        options=["offering", "warrants", "convertible", "atm", "shelf", "reverse_split", "compliance", "sec_filing"],
        default=["offering", "warrants", "convertible"],
        key="_exclude_financing",
        help="Exclude announcements with these financing types in headline. Offerings/warrants/convertible tend to underperform."
    )
    _exclude_biotech_key = "_exclude_biotech"
    if _exclude_biotech_key not in st.session_state or st.query_params.get("exclude_biotech"):
        st.session_state[_exclude_biotech_key] = get_param("exclude_biotech", False, bool)
    exclude_biotech = st.checkbox(
        "Exclude biotech/pharma (clinical trials)",
        key=_exclude_biotech_key,
        help="Exclude announcements mentioning 'therapeutics', 'clinical', 'trial', 'phase' (tend to underperform)"
    )

    # Price range (filters by actual entry price from OHLCV, not announcement price)
    st.subheader("Entry Price ($)")
    col1, col2 = st.columns(2)
    price_min = col1.number_input("Min", min_value=0.0, step=0.5, key="_price_min", help="Exclude if entry price ≤ this")
    price_max = col2.number_input("Max", min_value=0.0, step=1.0, key="_price_max", help="Exclude if entry price > this")

    # ─────────────────────────────────────────────────────────────────────────
    # Trigger Config
    # ─────────────────────────────────────────────────────────────────────────
    st.divider()
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
        min_value=1.0, max_value=1000.0,
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

    entry_window = st.slider(
        "Entry Window (min)",
        min_value=1, max_value=30,
        step=1,
        key="_entry_window",
        help="How long to wait for entry conditions after alert"
    )

    entry_at_open = st.checkbox(
        "Entry at first bar OPEN (optimistic)",
        key="_entry_at_open",
        help="Enter at first candle's open price. Most optimistic - ignores green candle/volume filters."
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
    set_param("entry_window", entry_window)
    set_param("sl_open", sl_from_open)
    set_param("sess", sessions)
    set_param("country", countries)
    set_param("author", authors)
    set_param("channel", channels)
    set_param("no_fin", exclude_financing_headlines)
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
                    entry_window_minutes=entry_window,
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
    st.markdown("[View Trades →](trades)")
    st.markdown("[View Orders →](orders)")

    # Cache control
    st.divider()
    if st.button("Clear Cache"):
        st.cache_data.clear()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Filter Announcements
# ─────────────────────────────────────────────────────────────────────────────

# Load sampled + filtered announcements from SQL (huge speedup vs loading all + filtering in Python)
with log_time("load_sampled_filtered_announcements"):
    total_before_sampling, filtered = load_sampled_filtered_announcements(
        sample_pct=int(sample_pct),
        sample_seed=int(sample_seed),
        sessions=tuple(sessions) if sessions else tuple(),
        countries=tuple(countries) if countries else tuple(),
        country_blacklist=tuple(country_blacklist) if country_blacklist else tuple(),
        authors=tuple(authors) if authors else tuple(),
        channels=tuple(channels) if channels else tuple(),
        directions=tuple(directions) if directions else tuple(),
        scanner_test=bool(scanner_test),
        scanner_after_lull=bool(scanner_after_lull),
        max_mentions=int(max_mentions or 0),
        exclude_financing_headlines=bool(exclude_financing_headlines),
        require_headline=bool(require_headline),
        exclude_headline=bool(exclude_headline),
        float_min=float(float_min or 0.0),
        float_max=float(float_max or 0.0),
        mc_min=float(mc_min or 0.0),
        mc_max=float(mc_max or 0.0),
        prior_move_min=float(prior_move_min or 0.0),
        prior_move_max=float(prior_move_max or 0.0),
        nhod_filter=str(nhod_filter),
        nsh_filter=str(nsh_filter),
        rvol_min=float(rvol_min or 0.0),
        rvol_max=float(rvol_max or 0.0),
        exclude_financing_types=tuple(exclude_financing_types) if exclude_financing_types else tuple(),
        exclude_biotech=bool(exclude_biotech),
    )

# Note: Price filter is applied after backtest based on actual entry price (see below)


# ─────────────────────────────────────────────────────────────────────────────
# Run Backtest
# ─────────────────────────────────────────────────────────────────────────────

if not filtered:
    st.warning("No announcements match the current filters.")
    st.stop()

# Create cache key from announcement identifiers
announcement_keys = tuple((a.ticker, a.timestamp.isoformat()) for a in filtered)

# Load OHLCV data (bulk query - returns dict with (ticker, datetime) keys)
with log_time("load_ohlcv_for_announcements", keys=len(announcement_keys), window_minutes=hold_time):
    bars_dict = load_ohlcv_for_announcements(announcement_keys, hold_time)

# Run backtest
config = BacktestConfig(
    entry_trigger_pct=0,  # Enter immediately at announcement
    take_profit_pct=take_profit,
    stop_loss_pct=stop_loss,
    stop_loss_from_open=sl_from_open,
    window_minutes=hold_time,
    entry_window_minutes=entry_window,
    entry_at_open=entry_at_open,  # Most optimistic - enter at first bar's open
    entry_at_candle_close=(consec_candles == 0 and not entry_at_open),  # Only use candle close if not waiting for consecutive candles
    entry_after_consecutive_candles=consec_candles if not entry_at_open else 0,
    min_candle_volume=int(min_candle_vol) if not entry_at_open else 0,
    trailing_stop_pct=trailing_stop,
)

with log_time("run_backtest", announcements=len(filtered)):
    summary = run_backtest(filtered, bars_dict, config)

# Price filter (applied after backtest based on actual entry price)
# Filter out results where entry price is outside the min/max range
if price_min > 0 or price_max < 100:
    summary.results = [
        r for r in summary.results
        if r.entry_price is None or (price_min < r.entry_price <= price_max)
    ]

with log_time("calculate_summary_stats", trades=len(summary.results)):
    stats = calculate_summary_stats(summary.results)


# ─────────────────────────────────────────────────────────────────────────────
# Display Summary Stats
# ─────────────────────────────────────────────────────────────────────────────

st.header("Summary")

# Show sampling notice if active
if sample_pct < 100:
    st.info(f"Sampling {sample_pct}% of data ({len(filtered)} of {total_before_sampling} announcements)" +
            (f" - seed: {sample_seed}" if sample_seed > 0 else " - random"))

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

        # Show trade outcome explanation
        st.info(explain_trigger_type(selected_result.trigger_type, config))

        # Show entry conditions
        st.info(explain_backtest_entry(config))

        # Show chart legend
        with st.expander("📖 Chart Legend - What do the colors and markers mean?"):
            st.markdown(get_backtest_chart_legend())

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
                # Round to minute to align with bar timestamps, then shift +1 min for display
                entry_time_aligned = pd.Timestamp(to_est(selected_result.entry_time)).floor('1min') + timedelta(minutes=1)
                fig.add_trace(go.Scatter(
                    x=[entry_time_aligned],
                    y=[selected_result.entry_price],
                    mode="markers",
                    marker=dict(symbol="circle", size=12, color="blue", line=dict(width=2, color="white")),
                    name=f"Entry @ ${selected_result.entry_price:.2f}",
                ))

            # Add exit marker (+1 min for end-time display)
            if selected_result.exit_price and selected_result.exit_time:
                # Round to minute to align with bar timestamps, then shift +1 min for display
                exit_time_aligned = pd.Timestamp(to_est(selected_result.exit_time)).floor('1min') + timedelta(minutes=1)
                exit_color = "green" if selected_result.return_pct > 0 else "red"
                fig.add_trace(go.Scatter(
                    x=[exit_time_aligned],
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
            st.subheader("Trade Metrics")
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Entry Price", f"${selected_result.entry_price:.2f}" if selected_result.entry_price else "N/A")
            col2.metric("Exit Price", f"${selected_result.exit_price:.2f}" if selected_result.exit_price else "N/A")
            col3.metric("Return", f"{selected_result.return_pct:+.2f}%" if selected_result.return_pct else "N/A")
            col4.metric("Exit Type", selected_result.trigger_type.replace('_', ' ').title())

            # Calculate duration if both times exist
            if selected_result.entry_time and selected_result.exit_time:
                duration = (selected_result.exit_time - selected_result.entry_time).total_seconds() / 60
                col5.metric("Duration", f"{duration:.1f} min")
            else:
                col5.metric("Duration", "N/A")

            # Additional context
            if ann.market_session:
                st.caption(f"📅 Market Session: **{ann.market_session}** | Country: **{ann.country}** | " +
                          (f"Float: **{ann.float_shares/1e6:.1f}M** | " if ann.float_shares else "") +
                          (f"Market Cap: **${ann.market_cap/1e6:.1f}M**" if ann.market_cap else ""))
        else:
            # Show different messages based on ohlcv_status
            status = getattr(ann, 'ohlcv_status', 'pending')
            if status == 'no_data':
                st.warning(f"No OHLCV data exists for {ann.ticker} (confirmed empty from data provider)")
            elif status == 'error':
                st.error(f"Failed to fetch OHLCV data for {ann.ticker} (API error)")
            else:  # pending or fetched but bars not found in dict
                st.info(f"OHLCV data not yet fetched for {ann.ticker}")
