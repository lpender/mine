"""Traces Dashboard - View alert lifecycle from receipt to completion."""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.trace_store import get_trace_store
from src.strategy_store import get_strategy_store
from src.database import init_db, SessionLocal, TraceDB, TraceEventDB

# Initialize database tables
init_db()

# Timezone helpers
UTC = ZoneInfo("UTC")
EST = ZoneInfo("America/New_York")


def to_est_display(dt: datetime) -> str:
    """Convert UTC datetime to EST and format for display."""
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    est_dt = dt.astimezone(EST)
    return est_dt.strftime("%Y-%m-%d %H:%M:%S")


def to_est_short(dt: datetime) -> str:
    """Convert UTC datetime to EST time only (HH:MM:SS)."""
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    est_dt = dt.astimezone(EST)
    return est_dt.strftime("%H:%M:%S")


def format_status(status: str) -> str:
    """Format status with emoji."""
    status_map = {
        "received": "📥 Received",
        "filtered": "🚫 Filtered",
        "pending_entry": "⏳ Pending Entry",
        "entry_timeout": "⏰ Entry Timeout",
        "buy_submitted": "📤 Buy Submitted",
        "active_trade": "📈 Active Trade",
        "exit_triggered": "📉 Exit Triggered",
        "completed": "✅ Completed",
        "error": "❌ Error",
    }
    return status_map.get(status, status)


def format_pnl(pnl: float, return_pct: float) -> str:
    """Format P&L with color indicator."""
    if pnl is None:
        return "-"
    sign = "+" if pnl >= 0 else ""
    return f"${pnl:,.2f} ({sign}{return_pct:.2f}%)"


def get_traces_with_events(
    limit: int = 100,
    status: str = None,
    ticker: str = None,
    strategy_id: str = None,
    start: datetime = None,
) -> list:
    """Get traces with optional filters."""
    db = SessionLocal()
    try:
        query = db.query(TraceDB)

        if status and status != "All":
            query = query.filter(TraceDB.status == status)
        if ticker:
            query = query.filter(TraceDB.ticker == ticker.upper())
        if strategy_id:
            # Join with events to filter by strategy
            query = query.join(TraceEventDB).filter(
                TraceEventDB.strategy_id == strategy_id
            ).distinct()
        if start:
            query = query.filter(TraceDB.alert_timestamp >= start)

        traces = query.order_by(TraceDB.created_at.desc()).limit(limit).all()

        # Detach from session
        for t in traces:
            db.expunge(t)

        return traces
    finally:
        db.close()


def get_trace_events(trace_id: str) -> list:
    """Get all events for a trace."""
    db = SessionLocal()
    try:
        events = db.query(TraceEventDB).filter(
            TraceEventDB.trace_id == trace_id
        ).order_by(TraceEventDB.event_timestamp.asc()).all()

        for e in events:
            db.expunge(e)

        return events
    finally:
        db.close()


def get_funnel_stats(start: datetime = None) -> dict:
    """Get funnel statistics for traces."""
    db = SessionLocal()
    try:
        query = db.query(TraceDB)
        if start:
            query = query.filter(TraceDB.alert_timestamp >= start)

        traces = query.all()

        stats = {
            "total_received": len(traces),
            "filtered": sum(1 for t in traces if t.status == "filtered"),
            "pending_entry": sum(1 for t in traces if t.status == "pending_entry"),
            "entry_timeout": sum(1 for t in traces if t.status == "entry_timeout"),
            "active_trade": sum(1 for t in traces if t.status == "active_trade"),
            "completed": sum(1 for t in traces if t.status == "completed"),
            "winners": sum(1 for t in traces if t.status == "completed" and t.pnl and t.pnl > 0),
            "losers": sum(1 for t in traces if t.status == "completed" and t.pnl and t.pnl <= 0),
            "total_pnl": sum(t.pnl or 0 for t in traces if t.status == "completed"),
        }

        return stats
    finally:
        db.close()


def get_filter_rejection_summary(strategy_id: str = None, start: datetime = None) -> pd.DataFrame:
    """Get summary of filter rejections by reason."""
    db = SessionLocal()
    try:
        query = db.query(TraceEventDB).filter(
            TraceEventDB.event_type == "filter_rejected"
        )

        if strategy_id:
            query = query.filter(TraceEventDB.strategy_id == strategy_id)
        if start:
            query = query.filter(TraceEventDB.event_timestamp >= start)

        events = query.all()

        # Group by reason
        reason_counts = {}
        for e in events:
            reason = e.reason or "Unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if not reason_counts:
            return pd.DataFrame()

        df = pd.DataFrame([
            {"Rejection Reason": reason, "Count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])
        ])

        return df
    finally:
        db.close()


# Page config
st.set_page_config(
    page_title="Traces",
    page_icon="🔍",
    layout="wide",
)

st.title("Alert Traces")
st.caption("Track alert lifecycle from receipt through completion")

# Sidebar filters
st.sidebar.header("Filters")

# Status filter
status_options = ["All", "received", "filtered", "pending_entry", "entry_timeout", "active_trade", "completed", "error"]
params = st.query_params
status_default = params.get("status", "All")
status_index = status_options.index(status_default) if status_default in status_options else 0

status_filter = st.sidebar.selectbox(
    "Status",
    options=status_options,
    index=status_index,
    format_func=lambda x: format_status(x) if x != "All" else "All Statuses",
)

# Strategy filter
store = get_strategy_store()
strategies = store.list_strategies()
strategy_options = ["All Strategies"] + [s.name for s in strategies]
strategy_default = params.get("strategy", "All Strategies")
strategy_index = strategy_options.index(strategy_default) if strategy_default in strategy_options else 0

selected_strategy = st.sidebar.selectbox(
    "Strategy",
    options=strategy_options,
    index=strategy_index,
)
strategy_id_filter = None
if selected_strategy != "All Strategies":
    matching = [s for s in strategies if s.name == selected_strategy]
    if matching:
        strategy_id_filter = matching[0].strategy_id

# Date range filter
date_range_options = ["Today", "Last 7 days", "Last 30 days", "All time"]
date_range_default = params.get("range", "Today")
date_range_index = date_range_options.index(date_range_default) if date_range_default in date_range_options else 0

date_range = st.sidebar.selectbox(
    "Date Range",
    options=date_range_options,
    index=date_range_index,
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
ticker_filter = st.sidebar.text_input("Ticker (optional)", value=ticker_default)
ticker_filter = ticker_filter.upper() if ticker_filter else None

# Update query params
st.query_params["status"] = status_filter
st.query_params["strategy"] = selected_strategy
st.query_params["range"] = date_range
if ticker_filter:
    st.query_params["ticker"] = ticker_filter
elif "ticker" in st.query_params:
    del st.query_params["ticker"]

# Links
st.sidebar.divider()
st.sidebar.markdown("[<- Back to Backtest](../)")
st.sidebar.markdown("[View Trades ->](trades)")
st.sidebar.markdown("[View Orders ->](orders)")

# Funnel stats
st.header("Alert Funnel")
stats = get_funnel_stats(start_date)

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Received", stats["total_received"])

with col2:
    filtered_pct = (stats["filtered"] / stats["total_received"] * 100) if stats["total_received"] > 0 else 0
    st.metric("Filtered Out", stats["filtered"], delta=f"{filtered_pct:.0f}%", delta_color="off")

with col3:
    timeout_count = stats["entry_timeout"]
    st.metric("Entry Timeout", timeout_count)

with col4:
    st.metric("Completed", stats["completed"])

with col5:
    if stats["completed"] > 0:
        win_rate = stats["winners"] / stats["completed"] * 100
        st.metric("Win Rate", f"{win_rate:.0f}%", delta=f"${stats['total_pnl']:+,.2f}")
    else:
        st.metric("Win Rate", "-")

st.divider()

# Filter rejection analysis
if status_filter in ["All", "filtered"]:
    with st.expander("Filter Rejection Analysis", expanded=False):
        rejection_df = get_filter_rejection_summary(strategy_id_filter, start_date)
        if not rejection_df.empty:
            st.dataframe(rejection_df, use_container_width=True, hide_index=True)
        else:
            st.info("No filter rejections found for the selected filters.")

# Traces list
st.header("Traces")

traces = get_traces_with_events(
    limit=200,
    status=status_filter if status_filter != "All" else None,
    ticker=ticker_filter,
    strategy_id=strategy_id_filter,
    start=start_date,
)

if not traces:
    st.info("No traces found matching the current filters.")
else:
    # Convert to DataFrame
    trace_data = []
    for t in traces:
        trace_data.append({
            "ID": t.trace_id[:8],
            "Ticker": t.ticker,
            "Time (EST)": to_est_display(t.alert_timestamp)[:-3],  # Remove seconds
            "Channel": t.channel or "-",
            "Status": format_status(t.status),
            "Exit Reason": t.exit_reason or "-",
            "P&L": format_pnl(t.pnl, t.return_pct) if t.pnl is not None else "-",
            "_trace_id": t.trace_id,  # Hidden for lookup
        })

    df = pd.DataFrame(trace_data)

    # Style P&L column
    def color_pnl(val):
        if "$" in str(val):
            if val.startswith("$-") or val.startswith("-"):
                return "color: red"
            elif val.startswith("$"):
                return "color: green"
        return ""

    display_df = df.drop(columns=["_trace_id"])
    styled_df = display_df.style.applymap(color_pnl, subset=["P&L"])

    st.dataframe(styled_df, use_container_width=True, hide_index=True)

    # Trace detail view
    st.subheader("Trace Details")

    trace_options = [(t.trace_id, f"{t.ticker} @ {to_est_short(t.alert_timestamp)} - {format_status(t.status)}") for t in traces]
    selected_trace_id = st.selectbox(
        "Select a trace to view events",
        options=[t[0] for t in trace_options],
        format_func=lambda x: next(t[1] for t in trace_options if t[0] == x),
    )

    if selected_trace_id:
        selected_trace = next(t for t in traces if t.trace_id == selected_trace_id)
        events = get_trace_events(selected_trace_id)

        col1, col2 = st.columns(2)

        with col1:
            st.write("**Alert Info**")
            st.write(f"- **Ticker**: {selected_trace.ticker}")
            st.write(f"- **Time**: {to_est_display(selected_trace.alert_timestamp)} EST")
            st.write(f"- **Channel**: {selected_trace.channel or 'N/A'}")
            st.write(f"- **Author**: {selected_trace.author or 'N/A'}")
            if selected_trace.price_threshold:
                st.write(f"- **Price**: ${selected_trace.price_threshold:.2f}")
            if selected_trace.headline:
                st.write(f"- **Headline**: {selected_trace.headline[:100]}...")

        with col2:
            st.write("**Outcome**")
            st.write(f"- **Status**: {format_status(selected_trace.status)}")
            if selected_trace.exit_reason:
                st.write(f"- **Exit Reason**: {selected_trace.exit_reason}")
            if selected_trace.pnl is not None:
                pnl_color = "green" if selected_trace.pnl >= 0 else "red"
                st.markdown(f"- **P&L**: :{pnl_color}[${selected_trace.pnl:+,.2f} ({selected_trace.return_pct:+.2f}%)]")
            if selected_trace.completed_at:
                st.write(f"- **Completed**: {to_est_display(selected_trace.completed_at)} EST")

        # Events timeline
        st.subheader("Event Timeline")

        if events:
            event_data = []
            for e in events:
                event_data.append({
                    "Time (EST)": to_est_display(e.event_timestamp),
                    "Event": e.event_type,
                    "Strategy": e.strategy_name or "-",
                    "Reason": e.reason or "-",
                    "Details": e.details[:100] + "..." if e.details and len(e.details) > 100 else (e.details or "-"),
                })

            events_df = pd.DataFrame(event_data)

            # Color code events
            def color_event(val):
                event_colors = {
                    "alert_received": "color: blue",
                    "filter_rejected": "color: orange",
                    "filter_accepted": "color: green",
                    "pending_entry_created": "color: blue",
                    "entry_timeout": "color: orange",
                    "entry_condition_met": "color: green",
                    "buy_order_submitted": "color: blue",
                    "buy_order_filled": "color: green",
                    "active_trade_created": "color: green",
                    "exit_condition_triggered": "color: orange",
                    "sell_order_submitted": "color: blue",
                    "sell_order_filled": "color: green",
                    "trade_completed": "color: green",
                }
                return event_colors.get(val, "")

            styled_events = events_df.style.applymap(color_event, subset=["Event"])
            st.dataframe(styled_events, use_container_width=True, hide_index=True)
        else:
            st.info("No events recorded for this trace.")

        # Raw content (expandable)
        if selected_trace.raw_content:
            with st.expander("Raw Alert Content"):
                st.code(selected_trace.raw_content, language=None)
