"""Orders Dashboard - View all order events."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.database import init_db, SessionLocal, OrderDB, OrderEventDB

# Initialize database tables
init_db()


def to_est_display(dt: datetime) -> str:
    """Convert UTC datetime to EST and format for display."""
    if dt is None:
        return "N/A"
    # All DB timestamps are naive UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    # Convert to EST
    est_dt = dt.astimezone(ZoneInfo("America/New_York"))
    return est_dt.strftime("%Y-%m-%d %H:%M:%S")


def format_price(price: float) -> str:
    """Format price with appropriate decimals for penny stocks."""
    if price is None:
        return "-"
    if price >= 1.0:
        return f"${price:.2f}"
    elif price >= 0.01:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


st.set_page_config(
    page_title="Orders",
    page_icon="📋",
    layout="wide",
)

st.title("Orders")

# Sidebar filters
st.sidebar.header("Filters")

# Read initial values from query params
params = st.query_params

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

# Event type filter
event_type_options = ["All", "fill", "partial_fill", "new", "cancelled", "rejected", "expired"]
event_type_default = params.get("event_type", "All")
event_type_index = event_type_options.index(event_type_default) if event_type_default in event_type_options else 0

event_type_filter = st.sidebar.selectbox(
    "Event Type",
    options=event_type_options,
    index=event_type_index,
    key="event_type",
)

# Side filter
side_options = ["All", "buy", "sell"]
side_default = params.get("side", "All")
side_index = side_options.index(side_default) if side_default in side_options else 0

side_filter = st.sidebar.selectbox(
    "Side",
    options=side_options,
    index=side_index,
    key="side",
)

# Update query params to reflect current filter state
st.query_params["range"] = date_range
st.query_params["event_type"] = event_type_filter
st.query_params["side"] = side_filter
if ticker_filter:
    st.query_params["ticker"] = ticker_filter
elif "ticker" in st.query_params:
    del st.query_params["ticker"]

# Links
st.sidebar.divider()
st.sidebar.markdown("[<- Back to Backtest](../)")
st.sidebar.markdown("[Trades ->](trades)")
st.sidebar.markdown("[Manage Strategies ->](strategies)")

# Load order events
db = SessionLocal()
try:
    # Build query joining orders and events
    query = db.query(OrderEventDB, OrderDB).outerjoin(
        OrderDB, OrderEventDB.order_id == OrderDB.id
    )

    # Apply filters
    if start_date:
        query = query.filter(OrderEventDB.event_timestamp >= start_date)

    if ticker_filter:
        query = query.filter(OrderDB.ticker == ticker_filter)

    if event_type_filter != "All":
        query = query.filter(OrderEventDB.event_type == event_type_filter)

    if side_filter != "All":
        query = query.filter(OrderDB.side == side_filter)

    # Order by event timestamp descending (newest first)
    query = query.order_by(OrderEventDB.event_timestamp.desc())

    # Limit results
    results = query.limit(500).all()

    # Build display data
    events_data = []
    for event, order in results:
        events_data.append({
            "Event Time (EST)": to_est_display(event.event_timestamp),
            "Event Type": event.event_type.upper() if event.event_type else "-",
            "Ticker": order.ticker if order else "-",
            "Side": order.side.upper() if order and order.side else "-",
            "Order Type": order.order_type if order else "-",
            "Requested": order.requested_shares if order else "-",
            "Filled": str(event.filled_shares) if event.filled_shares else "-",
            "Cumulative": f"{event.cumulative_filled}/{order.requested_shares}" if event.cumulative_filled is not None and order else "-",
            "Fill Price": format_price(event.fill_price) if event.fill_price else "-",
            "Limit Price": format_price(order.limit_price) if order and order.limit_price else "-",
            "Status": order.status if order else "-",
            "Strategy": order.strategy_name if order else "-",
            "Broker Order ID": event.broker_order_id or (order.broker_order_id if order else "-"),
            "Order ID": event.order_id or "-",
        })
finally:
    db.close()

# Stats section
st.header("Order Events Summary")

if events_data:
    df = pd.DataFrame(events_data)

    # Count by event type
    event_counts = df["Event Type"].value_counts()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Events", len(events_data))
    with col2:
        fills = event_counts.get("FILL", 0) + event_counts.get("PARTIAL_FILL", 0)
        st.metric("Fills", fills)
    with col3:
        st.metric("Cancelled", event_counts.get("CANCELLED", 0))
    with col4:
        st.metric("Rejected", event_counts.get("REJECTED", 0))

    st.divider()

    # Events table
    st.header("Order Events")

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

    # Expandable raw data viewer
    st.subheader("Event Details")

    if events_data:
        selected_idx = st.selectbox(
            "Select an event to view raw data",
            options=range(len(results)),
            format_func=lambda x: f"{events_data[x]['Event Time (EST)']} - {events_data[x]['Ticker']} {events_data[x]['Side']} {events_data[x]['Event Type']}",
        )

        if selected_idx is not None:
            event, order = results[selected_idx]

            col1, col2 = st.columns(2)

            with col1:
                st.write("**Event Details**")
                st.write(f"- Event ID: {event.id}")
                st.write(f"- Event Type: {event.event_type}")
                st.write(f"- Event Timestamp: {to_est_display(event.event_timestamp)} EST")
                st.write(f"- Recorded At: {to_est_display(event.created_at)} EST")
                st.write(f"- Filled Shares: {event.filled_shares}")
                st.write(f"- Fill Price: {format_price(event.fill_price)}")
                st.write(f"- Cumulative Filled: {event.cumulative_filled}")

            with col2:
                if order:
                    st.write("**Order Details**")
                    st.write(f"- Order ID: {order.id}")
                    st.write(f"- Broker Order ID: {order.broker_order_id}")
                    st.write(f"- Ticker: {order.ticker}")
                    st.write(f"- Side: {order.side}")
                    st.write(f"- Order Type: {order.order_type}")
                    st.write(f"- Limit Price: {format_price(order.limit_price)}")
                    st.write(f"- Requested Shares: {order.requested_shares}")
                    st.write(f"- Filled Shares: {order.filled_shares}")
                    st.write(f"- Avg Fill Price: {format_price(order.avg_fill_price)}")
                    st.write(f"- Status: {order.status}")
                    st.write(f"- Strategy: {order.strategy_name}")
                    st.write(f"- Paper: {order.paper}")
                else:
                    st.write("**Order Details**")
                    st.write("No associated order found")

            # Raw data expander
            if event.raw_data:
                with st.expander("Raw Event Data (JSON)"):
                    st.code(event.raw_data, language="json")
else:
    st.info("No order events found matching the current filters.")
