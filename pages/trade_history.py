"""Trade History Dashboard - View live/paper trading performance."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from src.trade_history import get_trade_history_client
from src.database import init_db

# Initialize database tables
init_db()

st.set_page_config(
    page_title="Trade History",
    page_icon="📊",
    layout="wide",
)

st.title("Trade History")

# Get trade history client
client = get_trade_history_client()

# Sidebar filters
st.sidebar.header("Filters")

# Paper/Live filter
trade_type = st.sidebar.radio(
    "Trade Type",
    options=["All", "Paper", "Live"],
    index=1,  # Default to Paper
)
paper_filter = None if trade_type == "All" else (trade_type == "Paper")

# Date range filter
date_range = st.sidebar.selectbox(
    "Date Range",
    options=["Today", "Last 7 days", "Last 30 days", "All time"],
    index=1,
)

if date_range == "Today":
    start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
elif date_range == "Last 7 days":
    start_date = datetime.now() - timedelta(days=7)
elif date_range == "Last 30 days":
    start_date = datetime.now() - timedelta(days=30)
else:
    start_date = None

# Ticker filter
ticker_filter = st.sidebar.text_input("Ticker (optional)")
ticker_filter = ticker_filter.upper() if ticker_filter else None

# Load trades
trades = client.get_trades(
    paper=paper_filter,
    ticker=ticker_filter,
    start=start_date,
    limit=500,
)

# Stats section
st.header("Performance Summary")

stats = client.get_trade_stats(paper=paper_filter)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Total Trades", stats["total_trades"])

with col2:
    win_rate = stats["win_rate"]
    st.metric("Win Rate", f"{win_rate:.1f}%")

with col3:
    total_pnl = stats["total_pnl"]
    st.metric(
        "Total P&L",
        f"${total_pnl:,.2f}",
        delta=f"{'+' if total_pnl > 0 else ''}{total_pnl:.2f}",
    )

with col4:
    avg_return = stats["avg_return_pct"]
    st.metric("Avg Return", f"{avg_return:+.2f}%")

# Additional stats row
col5, col6, col7, col8 = st.columns(4)

with col5:
    st.metric("Wins", stats["wins"])

with col6:
    st.metric("Losses", stats["losses"])

with col7:
    st.metric("Best Trade", f"{stats['best_trade_pct']:+.2f}%")

with col8:
    st.metric("Worst Trade", f"{stats['worst_trade_pct']:+.2f}%")

st.divider()

# Trade list
st.header("Trade History")

if not trades:
    st.info("No trades found matching the current filters.")
else:
    # Convert to DataFrame for display
    trade_data = []
    for t in trades:
        trade_data.append({
            "ID": t.id,
            "Ticker": t.ticker,
            "Entry Time": t.entry_time.strftime("%Y-%m-%d %H:%M"),
            "Entry $": f"${t.entry_price:.2f}",
            "Exit Time": t.exit_time.strftime("%Y-%m-%d %H:%M"),
            "Exit $": f"${t.exit_price:.2f}",
            "Exit Reason": t.exit_reason,
            "Shares": t.shares,
            "Return %": f"{t.return_pct:+.2f}%",
            "P&L": f"${t.pnl:+.2f}",
            "Mode": "Paper" if t.paper else "LIVE",
        })

    df = pd.DataFrame(trade_data)

    # Style the dataframe
    def color_pnl(val):
        if "+" in str(val):
            return "color: green"
        elif "-" in str(val):
            return "color: red"
        return ""

    styled_df = df.style.applymap(
        color_pnl, subset=["Return %", "P&L"]
    )

    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
    )

    # Expandable trade details
    st.subheader("Trade Details")

    selected_trade_id = st.selectbox(
        "Select a trade to view details",
        options=[t.id for t in trades],
        format_func=lambda x: f"#{x} - {next(t.ticker for t in trades if t.id == x)}",
    )

    if selected_trade_id:
        selected_trade = next(t for t in trades if t.id == selected_trade_id)

        col1, col2 = st.columns(2)

        with col1:
            st.write("**Trade Info**")
            st.write(f"- Ticker: {selected_trade.ticker}")
            st.write(f"- Entry: ${selected_trade.entry_price:.2f} @ {selected_trade.entry_time}")
            st.write(f"- Exit: ${selected_trade.exit_price:.2f} @ {selected_trade.exit_time}")
            st.write(f"- Reason: {selected_trade.exit_reason}")
            st.write(f"- Shares: {selected_trade.shares}")
            st.write(f"- P&L: ${selected_trade.pnl:+.2f} ({selected_trade.return_pct:+.2f}%)")

        with col2:
            st.write("**Strategy Parameters**")
            params = selected_trade.strategy_params
            if params:
                # Filters
                if "filters" in params:
                    f = params["filters"]
                    st.write(f"- Channels: {', '.join(f.get('channels', []))}")
                    st.write(f"- Directions: {', '.join(f.get('directions', []))}")
                    st.write(f"- Price: ${f.get('price_min', 0)}-${f.get('price_max', 100)}")

                # Entry rules
                if "entry" in params:
                    e = params["entry"]
                    st.write(f"- Green candles: {e.get('consec_green_candles', 0)}")
                    st.write(f"- Min volume: {e.get('min_candle_volume', 0):,}")

                # Exit rules
                if "exit" in params:
                    ex = params["exit"]
                    st.write(f"- TP: {ex.get('take_profit_pct', 0)}%")
                    st.write(f"- SL: {ex.get('stop_loss_pct', 0)}% (from open: {ex.get('stop_loss_from_open', False)})")
                    st.write(f"- Trail: {ex.get('trailing_stop_pct', 0)}%")
                    st.write(f"- Timeout: {ex.get('timeout_minutes', 0)}m")

                # Position
                if "position" in params:
                    p = params["position"]
                    st.write(f"- Stake: ${p.get('stake_amount', 0)}")
            else:
                st.write("No strategy parameters recorded")

# Chart of P&L over time
if trades:
    st.divider()
    st.header("P&L Over Time")

    # Build cumulative P&L
    pnl_data = []
    cumulative = 0
    for t in reversed(trades):  # Oldest first
        cumulative += t.pnl
        pnl_data.append({
            "date": t.exit_time,
            "pnl": t.pnl,
            "cumulative": cumulative,
        })

    pnl_df = pd.DataFrame(pnl_data)
    pnl_df.set_index("date", inplace=True)

    st.line_chart(pnl_df["cumulative"])
