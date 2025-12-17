"""Trades Dashboard - View live/paper trading performance."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

from src.trade_store import get_trade_store
from src.strategy_store import get_strategy_store
from src.live_bar_store import get_live_bar_store
from src.database import init_db, SessionLocal, OrderDB, OrderEventDB
from src.order_store import get_order_store


def format_price(price: float) -> str:
    """Format price with appropriate decimals for penny stocks."""
    if price >= 1.0:
        return f"${price:.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


def to_est_display(dt: datetime) -> str:
    """Convert UTC datetime to EST and format for display."""
    from zoneinfo import ZoneInfo
    if dt is None:
        return "N/A"
    # All DB timestamps are naive UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    # Convert to EST
    est_dt = dt.astimezone(ZoneInfo("America/New_York"))
    return est_dt.strftime("%Y-%m-%d %H:%M:%S")


def get_order_events_for_trade(trade_id: int, ticker: str, entry_time: datetime, exit_time: datetime, strategy_id: str = None) -> pd.DataFrame:
    """Get all orders and their events for a completed trade, displayed in EST."""
    db = SessionLocal()
    try:
        # Find the specific BUY and SELL orders for this trade
        # BUY order: created shortly before entry_time (order submitted, then filled = entry_time)
        # SELL order: created shortly before exit_time (order submitted, then filled = exit_time)
        orders = []

        # Find BUY order (created within 30 seconds before entry fill time)
        buy_query = db.query(OrderDB).filter(
            OrderDB.ticker == ticker,
            OrderDB.side == "buy",
            OrderDB.created_at >= entry_time - timedelta(seconds=30),
            OrderDB.created_at <= entry_time + timedelta(seconds=5),
        )
        if strategy_id:
            buy_query = buy_query.filter(OrderDB.strategy_id == strategy_id)
        buy_order = buy_query.first()
        if buy_order:
            orders.append(buy_order)

        # Find SELL order (created within 30 seconds before exit fill time)
        sell_query = db.query(OrderDB).filter(
            OrderDB.ticker == ticker,
            OrderDB.side == "sell",
            OrderDB.created_at >= exit_time - timedelta(seconds=30),
            OrderDB.created_at <= exit_time + timedelta(seconds=5),
        )
        if strategy_id:
            sell_query = sell_query.filter(OrderDB.strategy_id == strategy_id)
        sell_order = sell_query.first()
        if sell_order:
            orders.append(sell_order)

        # Sort by created_at
        orders.sort(key=lambda o: o.created_at)

        if not orders:
            return pd.DataFrame()

        # Get all events for these orders
        events_data = []
        for order in orders:
            # Get events for this order
            events = db.query(OrderEventDB).filter(
                OrderEventDB.order_id == order.id
            ).order_by(OrderEventDB.event_timestamp.asc()).all()

            for event in events:
                # For SUBMITTED events, show the order details
                # For FILL events, show the fill details
                if event.event_type.lower() == "submitted":
                    shares_display = order.requested_shares
                    price_display = f"${order.limit_price:.2f}" if order.limit_price else "-"
                elif event.event_type.lower() in ("fill", "partial_fill"):
                    shares_display = event.filled_shares if event.filled_shares else "-"
                    price_display = f"${event.fill_price:.2f}" if event.fill_price else "-"
                else:
                    shares_display = event.filled_shares if event.filled_shares else order.requested_shares
                    price_display = f"${event.fill_price:.2f}" if event.fill_price else (f"${order.limit_price:.2f}" if order.limit_price else "-")

                events_data.append({
                    "Time (EST)": to_est_display(event.event_timestamp),
                    "Event": event.event_type.upper(),
                    "Side": order.side.upper(),
                    "Type": order.order_type,
                    "Shares": shares_display,
                    "Limit Price": f"${order.limit_price:.2f}" if order.limit_price else "-",
                    "Fill Price": price_display if event.event_type.lower() in ("fill", "partial_fill") else "-",
                    "Filled": f"{event.cumulative_filled}/{order.requested_shares}" if event.cumulative_filled is not None else "-",
                    "Status": order.status,
                })

        if not events_data:
            return pd.DataFrame()

        return pd.DataFrame(events_data)
    finally:
        db.close()



# Initialize database tables
init_db()

st.set_page_config(
    page_title="Trades",
    page_icon="📊",
    layout="wide",
)

st.title("Trades")

# Get clients
client = get_trade_store()
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
st.header("Trades")

if not trades:
    st.info("No trades found matching the current filters.")
else:
    # Convert to DataFrame for display
    trade_data = []
    for t in trades:
        # Format times as EST without seconds (YYYY-MM-DD HH:MM)
        entry_est = to_est_display(t.entry_time)[:-3]  # Remove seconds
        exit_est = to_est_display(t.exit_time)[:-3]  # Remove seconds

        # Calculate hold time
        hold_time_seconds = (t.exit_time - t.entry_time).total_seconds()
        hold_time_minutes = hold_time_seconds / 60

        # Format hold time (show hours:minutes if >= 60 min, else just minutes)
        if hold_time_minutes >= 60:
            hours = int(hold_time_minutes // 60)
            minutes = int(hold_time_minutes % 60)
            hold_time_str = f"{hours}h {minutes}m"
        else:
            hold_time_str = f"{hold_time_minutes:.1f}m"

        trade_data.append({
            "ID": t.id,
            "Strategy": t.strategy_name or "-",
            "Ticker": t.ticker,
            "Entry Time (EST)": entry_est,
            "Entry $": format_price(t.entry_price),
            "Exit Time (EST)": exit_est,
            "Exit $": format_price(t.exit_price),
            "Hold Time": hold_time_str,
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

        # Order Events Table (only for trades with order tracking)
        st.subheader("Order Events (Chronological)")
        order_events_df = get_order_events_for_trade(
            trade_id=selected_trade.id,
            ticker=selected_trade.ticker,
            entry_time=selected_trade.entry_time,
            exit_time=selected_trade.exit_time,
            strategy_id=selected_trade.strategy_id,
        )

        if not order_events_df.empty:
            st.dataframe(
                order_events_df,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No order events found (order tracking was not enabled for this trade, or orders have been cleared).")

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            st.write("**Trade Summary**")
            st.write(f"- Ticker: **{selected_trade.ticker}**")
            st.write(f"- Strategy: {selected_trade.strategy_name or 'N/A'}")
            entry_est = to_est_display(selected_trade.entry_time).split()[1]  # Get time part only
            exit_est = to_est_display(selected_trade.exit_time).split()[1]  # Get time part only
            st.write(f"- Entry: {format_price(selected_trade.entry_price)} @ {entry_est} EST")
            st.write(f"- Exit: {format_price(selected_trade.exit_price)} @ {exit_est} EST")
            duration = (selected_trade.exit_time - selected_trade.entry_time).total_seconds() / 60
            st.write(f"- Duration: {duration:.1f} min")
            st.write(f"- Exit Reason: {selected_trade.exit_reason}")
            st.write(f"- Shares: {selected_trade.shares:,}")
            st.write(f"- **P&L: ${selected_trade.pnl:+.2f} ({selected_trade.return_pct:+.2f}%)**")

        with col2:
            st.write("**Strategy Parameters**")
            strategy_params = selected_trade.strategy_params
            if strategy_params:
                # Filters
                if "filters" in strategy_params:
                    f = strategy_params["filters"]
                    if f.get('channels'):
                        st.write(f"- Channels: {', '.join(f.get('channels', []))}")
                    if f.get('directions'):
                        st.write(f"- Directions: {', '.join(f.get('directions', []))}")
                    st.write(f"- Price Range: ${f.get('price_min', 0)}-${f.get('price_max', 100)}")

                # Entry rules
                if "entry" in strategy_params:
                    e = strategy_params["entry"]
                    st.write(f"- Entry Window: {e.get('entry_window_minutes', 5)} min")
                    if e.get('consec_green_candles', 0) > 0:
                        st.write(f"- Green Candles: {e.get('consec_green_candles', 0)}")
                        st.write(f"- Min Volume/Candle: {e.get('min_candle_volume', 0):,}")

                # Exit rules
                if "exit" in strategy_params:
                    ex = strategy_params["exit"]
                    st.write(f"- Take Profit: {ex.get('take_profit_pct', 0)}%")
                    st.write(f"- Stop Loss: {ex.get('stop_loss_pct', 0)}%" +
                            (" (from open)" if ex.get('stop_loss_from_open', False) else ""))
                    if ex.get('trailing_stop_pct', 0) > 0:
                        st.write(f"- Trailing Stop: {ex.get('trailing_stop_pct', 0)}%")
                    st.write(f"- Timeout: {ex.get('timeout_minutes', 0)} min")

                # Position
                if "position" in strategy_params:
                    p = strategy_params["position"]
                    stake_mode = p.get('stake_mode', 'fixed')
                    if stake_mode == 'volume_pct':
                        st.write(f"- Position Sizing: {p.get('volume_pct', 1.0)}% of prev candle volume")
                        st.write(f"- Max Stake: ${p.get('max_stake', 10000):,.2f}")
                    else:
                        st.write(f"- Position Sizing: Fixed ${p.get('stake_amount', 50):,.2f}")
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

                # Create custom hover text with volume
                hover_text = [
                    f"Time: {t.strftime('%H:%M')}<br>"
                    f"Open: ${o:.2f}<br>"
                    f"High: ${h:.2f}<br>"
                    f"Low: ${l:.2f}<br>"
                    f"Close: ${c:.2f}<br>"
                    f"Volume: {v:,}"
                    for t, o, h, l, c, v in zip(
                        ohlcv.index, ohlcv["open"], ohlcv["high"],
                        ohlcv["low"], ohlcv["close"], ohlcv["volume"]
                    )
                ]

                # Add candlestick
                fig.add_trace(go.Candlestick(
                    x=ohlcv.index,
                    open=ohlcv["open"],
                    high=ohlcv["high"],
                    low=ohlcv["low"],
                    close=ohlcv["close"],
                    name="Price",
                    text=hover_text,
                    hoverinfo="text",
                ))

                # Add entry marker - round to minute to align with resampled bars
                entry_time_rounded = pd.Timestamp(selected_trade.entry_time).floor('1min')
                fig.add_trace(go.Scatter(
                    x=[entry_time_rounded],
                    y=[selected_trade.entry_price],
                    mode="markers",
                    marker=dict(symbol="circle", size=12, color="blue", line=dict(width=2, color="white")),
                    name=f"Entry @ {format_price(selected_trade.entry_price)}",
                    hoverinfo="name",
                ))

                # Add exit marker - round to minute to align with resampled bars
                exit_time_rounded = pd.Timestamp(selected_trade.exit_time).floor('1min')
                exit_color = "green" if selected_trade.pnl > 0 else "red"
                fig.add_trace(go.Scatter(
                    x=[exit_time_rounded],
                    y=[selected_trade.exit_price],
                    mode="markers",
                    marker=dict(symbol="x", size=12, color=exit_color, line=dict(width=3)),
                    name=f"Exit @ {format_price(selected_trade.exit_price)} ({selected_trade.exit_reason})",
                    hoverinfo="name",
                ))

                # Calculate price range for Y-axis scaling
                price_min = min(ohlcv["low"].min(), selected_trade.entry_price, selected_trade.exit_price)
                price_max = max(ohlcv["high"].max(), selected_trade.entry_price, selected_trade.exit_price)
                price_range = price_max - price_min
                # Add 10% padding
                y_min = price_min - price_range * 0.1
                y_max = price_max + price_range * 0.1

                # Add stop loss line if available in params (only if within visible range)
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

                        # Only show line if within reasonable range (extends y-axis by at most 30%)
                        if sl_price >= y_min - price_range * 0.3:
                            fig.add_hline(
                                y=sl_price,
                                line_dash="dash",
                                line_color="red",
                                annotation_text=f"SL @ {format_price(sl_price)}",
                                annotation_position="right",
                            )
                            y_min = min(y_min, sl_price - price_range * 0.05)

                    # Add take profit line (only if within visible range)
                    take_profit_pct = params["exit"].get("take_profit_pct", 0)
                    if take_profit_pct > 0:
                        tp_price = selected_trade.entry_price * (1 + take_profit_pct / 100)
                        # Only show line if within reasonable range
                        if tp_price <= y_max + price_range * 0.3:
                            fig.add_hline(
                                y=tp_price,
                                line_dash="dash",
                                line_color="green",
                                annotation_text=f"TP @ {format_price(tp_price)}",
                                annotation_position="right",
                            )
                            y_max = max(y_max, tp_price + price_range * 0.05)

                fig.update_layout(
                    title=f"{selected_trade.ticker} - Trade #{selected_trade.id}",
                    xaxis_title="Time",
                    yaxis_title="Price",
                    yaxis_range=[y_min, y_max],
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
