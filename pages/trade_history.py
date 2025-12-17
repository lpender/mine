"""Trade History Dashboard - View live/paper trading performance."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

from src.trade_history import get_trade_history_client
from src.strategy_store import get_strategy_store
from src.live_bar_store import get_live_bar_store
from src.database import init_db


def format_price(price: float) -> str:
    """Format price with appropriate decimals for penny stocks."""
    if price >= 1.0:
        return f"${price:.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"

# Initialize database tables
init_db()

st.set_page_config(
    page_title="Trade History",
    page_icon="📊",
    layout="wide",
)

st.title("Trade History")

# Get clients
client = get_trade_history_client()
store = get_strategy_store()
bar_store = get_live_bar_store()

# Sidebar filters
st.sidebar.header("Filters")

# Read initial values from query params
params = st.query_params

# Paper/Live filter
trade_type_options = ["All", "Paper", "Live"]
trade_type_default = params.get("type", "Paper")
trade_type_index = trade_type_options.index(trade_type_default) if trade_type_default in trade_type_options else 1

trade_type = st.sidebar.radio(
    "Trade Type",
    options=trade_type_options,
    index=trade_type_index,
    key="trade_type",
)
paper_filter = None if trade_type == "All" else (trade_type == "Paper")

# Strategy filter
strategies = store.list_strategies()
strategy_options = ["All Strategies"] + [s.name for s in strategies]
strategy_default = params.get("strategy", "All Strategies")
strategy_index = strategy_options.index(strategy_default) if strategy_default in strategy_options else 0

selected_strategy = st.sidebar.selectbox(
    "Strategy",
    options=strategy_options,
    index=strategy_index,
    key="strategy",
)
strategy_name_filter = None if selected_strategy == "All Strategies" else selected_strategy

# Date range filter
date_range_options = ["Today", "Last 7 days", "Last 30 days", "All time"]
date_range_default = params.get("range", "Last 7 days")
date_range_index = date_range_options.index(date_range_default) if date_range_default in date_range_options else 1

date_range = st.sidebar.selectbox(
    "Date Range",
    options=date_range_options,
    index=date_range_index,
    key="date_range",
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
ticker_default = params.get("ticker", "")
ticker_filter = st.sidebar.text_input("Ticker (optional)", value=ticker_default, key="ticker")
ticker_filter = ticker_filter.upper() if ticker_filter else None

# Update query params to reflect current filter state
st.query_params["type"] = trade_type
st.query_params["strategy"] = selected_strategy
st.query_params["range"] = date_range
if ticker_filter:
    st.query_params["ticker"] = ticker_filter
elif "ticker" in st.query_params:
    del st.query_params["ticker"]

# Links
st.sidebar.divider()
st.sidebar.markdown("[← Back to Backtest](../)")
st.sidebar.markdown("[Manage Strategies →](strategies)")

# Load trades
trades = client.get_trades(
    paper=paper_filter,
    ticker=ticker_filter,
    start=start_date,
    limit=500,
)

# Filter by strategy name (client-side since DB query doesn't support it yet)
if strategy_name_filter:
    trades = [t for t in trades if t.strategy_name == strategy_name_filter]

# Stats section
st.header("Performance Summary")

# Compute stats from filtered trades (not a separate DB call that ignores filters)
if trades:
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl <= 0)
    total_pnl = sum(t.pnl for t in trades)
    returns = [t.return_pct for t in trades]
    stats = {
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(trades) * 100,
        "total_pnl": total_pnl,
        "avg_return_pct": sum(returns) / len(returns),
        "best_trade_pct": max(returns),
        "worst_trade_pct": min(returns),
    }
else:
    stats = {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0,
        "total_pnl": 0,
        "avg_return_pct": 0,
        "best_trade_pct": 0,
        "worst_trade_pct": 0,
    }

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
            "Strategy": t.strategy_name or "-",
            "Ticker": t.ticker,
            "Entry Time": t.entry_time.strftime("%Y-%m-%d %H:%M"),
            "Entry $": format_price(t.entry_price),
            "Exit Time": t.exit_time.strftime("%Y-%m-%d %H:%M"),
            "Exit $": format_price(t.exit_price),
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
        width="stretch",
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
            st.write(f"- Entry: {format_price(selected_trade.entry_price)} @ {selected_trade.entry_time}")
            st.write(f"- Exit: {format_price(selected_trade.exit_price)} @ {selected_trade.exit_time}")
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

        # Price chart for selected trade
        st.subheader("Price Chart")

        # Query bars for this trade (with some buffer before/after)
        buffer = timedelta(minutes=2)
        bars = bar_store.get_bars(
            ticker=selected_trade.ticker,
            start_time=selected_trade.entry_time - buffer,
            end_time=selected_trade.exit_time + buffer,
            strategy_id=selected_trade.strategy_id,
        )

        # Also check for legacy bars stored in local time (ET = UTC-5)
        # TODO: Remove this fallback after migrating old bars to UTC
        if not bars:
            et_offset = timedelta(hours=5)
            bars = bar_store.get_bars(
                ticker=selected_trade.ticker,
                start_time=selected_trade.entry_time - buffer - et_offset,
                end_time=selected_trade.exit_time + buffer - et_offset,
                strategy_id=selected_trade.strategy_id,
            )
            # Adjust timestamps to UTC for display consistency
            if bars:
                for bar in bars:
                    bar.timestamp = bar.timestamp + et_offset

        if not bars:
            st.info("No bar data available for this trade. Bar data is only captured during live monitoring.")
        else:
            # Resample 1-second bars to 1-minute for cleaner display
            bar_df = pd.DataFrame([
                {
                    "timestamp": b.timestamp,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ])
            bar_df["timestamp"] = pd.to_datetime(bar_df["timestamp"])
            bar_df.set_index("timestamp", inplace=True)

            # Resample to 1-minute OHLCV
            ohlcv = bar_df.resample("1min").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()

            if ohlcv.empty:
                st.info("No complete minute bars available.")
            else:
                # Create candlestick chart
                fig = go.Figure()

                # Add candlestick
                fig.add_trace(go.Candlestick(
                    x=ohlcv.index,
                    open=ohlcv["open"],
                    high=ohlcv["high"],
                    low=ohlcv["low"],
                    close=ohlcv["close"],
                    name="Price",
                ))

                # Add entry marker
                fig.add_trace(go.Scatter(
                    x=[selected_trade.entry_time],
                    y=[selected_trade.entry_price],
                    mode="markers",
                    marker=dict(symbol="circle", size=12, color="blue", line=dict(width=2, color="white")),
                    name=f"Entry @ {format_price(selected_trade.entry_price)}",
                    hoverinfo="name",
                ))

                # Add exit marker
                exit_color = "green" if selected_trade.pnl > 0 else "red"
                fig.add_trace(go.Scatter(
                    x=[selected_trade.exit_time],
                    y=[selected_trade.exit_price],
                    mode="markers",
                    marker=dict(symbol="x", size=12, color=exit_color, line=dict(width=3)),
                    name=f"Exit @ {format_price(selected_trade.exit_price)} ({selected_trade.exit_reason})",
                    hoverinfo="name",
                ))

                # Add stop loss line if available in params
                params = selected_trade.strategy_params
                if params and "exit" in params:
                    stop_loss_pct = params["exit"].get("stop_loss_pct", 0)
                    stop_loss_from_open = params["exit"].get("stop_loss_from_open", False)
                    if stop_loss_pct > 0:
                        # Calculate stop loss price
                        if stop_loss_from_open:
                            # Would need first candle open - approximate with entry for now
                            sl_price = selected_trade.entry_price * (1 - stop_loss_pct / 100)
                        else:
                            sl_price = selected_trade.entry_price * (1 - stop_loss_pct / 100)

                        fig.add_hline(
                            y=sl_price,
                            line_dash="dash",
                            line_color="red",
                            annotation_text=f"SL @ {format_price(sl_price)}",
                            annotation_position="right",
                        )

                    # Add take profit line
                    take_profit_pct = params["exit"].get("take_profit_pct", 0)
                    if take_profit_pct > 0:
                        tp_price = selected_trade.entry_price * (1 + take_profit_pct / 100)
                        fig.add_hline(
                            y=tp_price,
                            line_dash="dash",
                            line_color="green",
                            annotation_text=f"TP @ {format_price(tp_price)}",
                            annotation_position="right",
                        )

                fig.update_layout(
                    title=f"{selected_trade.ticker} - Trade #{selected_trade.id}",
                    xaxis_title="Time",
                    yaxis_title="Price",
                    xaxis_rangeslider_visible=False,
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=400,
                )

                st.plotly_chart(fig, use_container_width=True)

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
