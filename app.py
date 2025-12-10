import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from dotenv import load_dotenv

from src.parser import parse_discord_messages
from src.massive_client import MassiveClient
from src.backtest import run_backtest, calculate_summary_stats
from src.models import BacktestConfig, Announcement, OHLCVBar

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
if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None


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

    if st.button("Parse Messages", type="primary"):
        if messages_input.strip():
            ref_datetime = datetime.combine(reference_date, datetime.min.time())
            announcements = parse_discord_messages(messages_input, ref_datetime)
            st.session_state.announcements = announcements
            st.session_state.bars_by_announcement = {}  # Clear old data
            st.session_state.results = []
            st.success(f"Parsed {len(announcements)} announcements")
        else:
            st.warning("Please paste some messages first")

    st.divider()
    st.header("Trigger Configuration")

    entry_trigger = st.slider(
        "Entry Trigger (%)",
        min_value=0.0,
        max_value=20.0,
        value=5.0,
        step=0.5,
        help="Buy when price moves up by this percentage from open",
    )

    take_profit = st.slider(
        "Take Profit (%)",
        min_value=1.0,
        max_value=50.0,
        value=10.0,
        step=0.5,
        help="Sell when price moves up by this percentage from entry",
    )

    stop_loss = st.slider(
        "Stop Loss (%)",
        min_value=1.0,
        max_value=20.0,
        value=3.0,
        step=0.5,
        help="Sell when price moves down by this percentage from entry",
    )

    volume_threshold = st.number_input(
        "Min Volume Threshold",
        min_value=0,
        value=0,
        step=1000,
        help="Minimum volume required to trigger entry",
    )

    window_minutes = st.slider(
        "Window (minutes)",
        min_value=5,
        max_value=120,
        value=30,
        step=5,
        help="How long to track after announcement",
    )


# Load cached data button (always visible)
col_load1, col_load2 = st.columns([1, 4])
with col_load1:
    if st.button("Load All Cached Data"):
        client = MassiveClient()
        cached_announcements, cached_bars = client.load_all_cached_data()
        if cached_announcements:
            st.session_state.announcements = cached_announcements
            st.session_state.bars_by_announcement = cached_bars
            st.session_state.results = []
            st.success(f"Loaded {len(cached_announcements)} announcements from cache")
            st.rerun()
        else:
            st.warning("No cached data found")

# Main area
if st.session_state.announcements:
    announcements = st.session_state.announcements

    # Fetch OHLCV data button
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Fetch OHLCV Data"):
            client = MassiveClient()
            progress_bar = st.progress(0)

            for i, ann in enumerate(announcements):
                key = (ann.ticker, ann.timestamp)
                if key not in st.session_state.bars_by_announcement:
                    bars = client.fetch_after_announcement(
                        ann.ticker,
                        ann.timestamp,
                        window_minutes,
                    )
                    st.session_state.bars_by_announcement[key] = bars
                progress_bar.progress((i + 1) / len(announcements))

            # Save announcements to cache for future use
            client.save_announcements(announcements)
            st.success("OHLCV data fetched and saved!")

    # Run backtest
    config = BacktestConfig(
        entry_trigger_pct=entry_trigger,
        take_profit_pct=take_profit,
        stop_loss_pct=stop_loss,
        volume_threshold=volume_threshold,
        window_minutes=window_minutes,
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

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("Total Announcements", stats["total_announcements"])
        with col2:
            st.metric("Trades Entered", stats["total_trades"])
        with col3:
            st.metric("Win Rate", f"{stats['win_rate']:.1f}%")
        with col4:
            st.metric("Avg Return", f"{stats['avg_return']:.2f}%")
        with col5:
            st.metric("Best Trade", f"{stats['best_trade']:.2f}%")
        with col6:
            st.metric("Worst Trade", f"{stats['worst_trade']:.2f}%")

        st.divider()

    # Announcements table
    st.header("Announcements")

    # Build table data
    table_data = []
    for i, ann in enumerate(announcements):
        result = st.session_state.results[i] if i < len(st.session_state.results) else None

        row = {
            "Ticker": ann.ticker,
            "Time": ann.timestamp.strftime("%Y-%m-%d %H:%M"),
            "Price": f"${ann.price_threshold:.2f}" if ann.price_threshold else "N/A",
            "Float": f"{ann.float_shares/1e6:.1f}M" if ann.float_shares else "N/A",
            "IO%": f"{ann.io_percent:.1f}%" if ann.io_percent else "N/A",
            "MC": f"${ann.market_cap/1e6:.1f}M" if ann.market_cap else "N/A",
            "Country": ann.country,
            "Return": f"{result.return_pct:.2f}%" if result and result.return_pct else "N/A",
            "Status": result.trigger_type if result else "pending",
        }
        table_data.append(row)

    df = pd.DataFrame(table_data)

    # Display as interactive table
    selected_idx = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    # Chart for selected announcement
    if selected_idx and selected_idx.selection.rows:
        idx = selected_idx.selection.rows[0]
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

                # Add trigger lines
                ref_price = bars[0].open
                entry_line = ref_price * (1 + entry_trigger / 100)
                fig.add_hline(
                    y=entry_line,
                    line_dash="dash",
                    line_color="blue",
                    annotation_text=f"Entry Trigger ({entry_trigger}%)",
                    row=1, col=1,
                )

                if result.entry_price:
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
    st.info("Paste Discord messages in the sidebar and click 'Parse Messages' to get started.")
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

    1. **Paste messages** from Discord into the sidebar
    2. **Set reference date** (for "Yesterday" and "Today" timestamps)
    3. **Click Parse Messages** to extract announcements
    4. **Fetch OHLCV Data** from Massive.com (requires API key in .env)
    5. **Adjust triggers** using the sliders to see different strategy results
    6. **Click on a row** to see the detailed price chart
    """)
