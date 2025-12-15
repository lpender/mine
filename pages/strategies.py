"""Strategy management page."""

import streamlit as st
import pandas as pd
import time
from datetime import datetime

from src.database import init_db
from src.strategy import StrategyConfig
from src.strategy_store import get_strategy_store, Strategy
from src.live_trading_service import (
    get_live_trading_status,
    is_live_trading_active,
    enable_strategy,
    disable_strategy,
)
from src.alert_service import start_alert_service, AlertService

# Initialize database tables
init_db()

# Start alert service if not running
if not AlertService.is_running():
    start_alert_service(port=8765)

st.set_page_config(
    page_title="Strategies",
    page_icon=":gear:",
    layout="wide",
)

st.title("Strategy Management")

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar - Trading Engine Controls
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Trading Engine")

    trading_active = is_live_trading_active()
    status = get_live_trading_status()

    if trading_active and status:
        is_paper = status.get("paper", True)
        mode_str = "Paper" if is_paper else "LIVE"
        st.success(f"Engine Running ({mode_str})")

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Strategies", status.get("strategy_count", 0))
        with col2:
            connected = status.get("quote_connected", False)
            st.metric("WebSocket", "Connected" if connected else "Disconnected")

        # Auto-refresh for live price updates
        st.divider()
        auto_refresh = st.checkbox("Auto-refresh (2s)", value=False, key="auto_refresh")
        if auto_refresh:
            time.sleep(2)
            st.rerun()

        st.caption("Stop with Ctrl+C in terminal")
    else:
        st.warning("Engine Not Running")
        st.markdown("""
        **Start the engine from terminal:**
        ```bash
        task trade        # paper mode
        task trade:live   # live mode
        ```
        Or directly:
        ```bash
        python run_trading.py
        ```
        """)

    st.divider()
    st.markdown("[← Back to Backtest](../)")
    st.markdown("[View Trade History →](trade_history)")

# ─────────────────────────────────────────────────────────────────────────────
# Create Strategy Form
# ─────────────────────────────────────────────────────────────────────────────

store = get_strategy_store()

with st.expander("Create New Strategy", expanded=False):
    with st.form("create_strategy"):
        st.subheader("Strategy Details")

        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Strategy Name", placeholder="e.g., Conservative Scalper")
        with col2:
            description = st.text_area("Description (optional)", height=68)

        st.subheader("Filters")
        col1, col2, col3 = st.columns(3)
        with col1:
            channels = st.multiselect(
                "Channels",
                ["select-news", "sweep-news", "option-scanner", "volume-scanner"],
                default=["select-news"],
            )
        with col2:
            directions = st.multiselect(
                "Directions",
                ["up", "up_right", "right", "down"],
                default=["up_right"],
            )
        with col3:
            sessions = st.multiselect(
                "Sessions",
                ["premarket", "market", "afterhours"],
                default=["premarket", "market"],
            )

        col1, col2 = st.columns(2)
        with col1:
            price_min = st.number_input("Min Price ($)", value=1.0, min_value=0.0)
        with col2:
            price_max = st.number_input("Max Price ($)", value=10.0, min_value=0.0)

        st.subheader("Entry Rules")
        col1, col2 = st.columns(2)
        with col1:
            consec_candles = st.number_input("Consecutive Green Candles", value=1, min_value=0)
        with col2:
            min_volume = st.number_input("Min Candle Volume", value=5000, min_value=0)

        st.subheader("Exit Rules")
        col1, col2, col3 = st.columns(3)
        with col1:
            take_profit = st.number_input("Take Profit (%)", value=10.0, min_value=0.0)
        with col2:
            stop_loss = st.number_input("Stop Loss (%)", value=11.0, min_value=0.0)
        with col3:
            trailing_stop = st.number_input("Trailing Stop (%)", value=7.0, min_value=0.0)

        col1, col2 = st.columns(2)
        with col1:
            timeout = st.number_input("Timeout (minutes)", value=15, min_value=1)
        with col2:
            sl_from_open = st.checkbox("Stop Loss from Open", value=True)

        st.subheader("Position Sizing")
        stake = st.number_input("Stake per Trade ($)", value=50.0, min_value=1.0)

        submitted = st.form_submit_button("Create Strategy", type="primary")

        if submitted:
            if not name:
                st.error("Please enter a strategy name")
            elif store.get_strategy_by_name(name):
                st.error(f"Strategy '{name}' already exists")
            else:
                config = StrategyConfig(
                    channels=channels,
                    directions=directions,
                    sessions=sessions,
                    price_min=price_min,
                    price_max=price_max,
                    consec_green_candles=consec_candles,
                    min_candle_volume=min_volume,
                    take_profit_pct=take_profit,
                    stop_loss_pct=stop_loss,
                    trailing_stop_pct=trailing_stop,
                    stop_loss_from_open=sl_from_open,
                    timeout_minutes=timeout,
                    stake_amount=stake,
                )
                strategy_id = store.save_strategy(name, config, description)
                st.success(f"Created strategy '{name}'")
                st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Strategy List
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("Your Strategies")

strategies = store.list_strategies()

if not strategies:
    st.info("No strategies created yet. Use the form above to create one.")
else:
    # Get live status if engine is running
    engine = get_trading_engine()
    live_status = get_live_trading_status() or {}
    strategies_status = live_status.get("strategies", {})

    # Build dataframe
    rows = []
    for s in strategies:
        strategy_live = strategies_status.get(s.id, {})

        # Use strings for all columns to avoid Arrow mixed-type errors
        pending = str(len(strategy_live.get("pending_entries", []))) if s.enabled else "-"
        active = str(len(strategy_live.get("active_trades", {}))) if s.enabled else "-"
        completed = str(strategy_live.get("completed_trades", 0)) if s.enabled else "-"

        rows.append({
            "id": s.id,
            "Name": s.name,
            "Enabled": s.enabled,
            "Pending": pending,
            "Active": active,
            "Completed": completed,
            "Stake": f"${s.config.stake_amount:.0f}",
            "TP/SL": f"{s.config.take_profit_pct:.0f}% / {s.config.stop_loss_pct:.0f}%",
            "Created": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "",
        })

    df = pd.DataFrame(rows)

    # Display with selection
    event = st.dataframe(
        df.drop(columns=["id"]),
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key="strategy_table",
    )

    selected_rows = event.selection.rows if hasattr(event, 'selection') else []

    # ─────────────────────────────────────────────────────────────────────────
    # Selected Strategy Detail
    # ─────────────────────────────────────────────────────────────────────────

    if selected_rows:
        idx = selected_rows[0]
        strategy_id = df.iloc[idx]["id"]
        strategy = store.get_strategy(strategy_id)

        if strategy:
            st.divider()
            st.subheader(f"Strategy: {strategy.name}")

            # Action buttons
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                if strategy.enabled:
                    if st.button("Disable", type="secondary"):
                        disable_strategy(strategy_id)
                        st.rerun()
                else:
                    if st.button("Enable", type="primary"):
                        enable_strategy(strategy_id)
                        st.rerun()

            with col2:
                # Load in Backtest button - sets URL params
                if st.button("Load in Backtest"):
                    cfg = strategy.config
                    params = {
                        "channel": ",".join(cfg.channels),
                        "direction": ",".join(cfg.directions),
                        "sess": ",".join(cfg.sessions),
                        "price_min": str(cfg.price_min),
                        "price_max": str(cfg.price_max),
                        "consec": str(cfg.consec_green_candles),
                        "min_vol": str(cfg.min_candle_volume),
                        "tp": str(cfg.take_profit_pct),
                        "sl": str(cfg.stop_loss_pct),
                        "trail": str(cfg.trailing_stop_pct),
                        "sl_open": "1" if cfg.stop_loss_from_open else "0",
                        "hold": str(cfg.timeout_minutes),
                        "stake": str(cfg.stake_amount),
                    }
                    query_string = "&".join(f"{k}={v}" for k, v in params.items())
                    st.markdown(f"[Open Backtest with these settings](../?{query_string})")

            with col3:
                pass  # Placeholder for future actions

            with col4:
                if st.button("Delete", type="secondary"):
                    if strategy.enabled:
                        st.error("Disable strategy before deleting")
                    else:
                        store.delete_strategy(strategy_id)
                        st.success(f"Deleted strategy '{strategy.name}'")
                        st.rerun()

            # Configuration details
            st.markdown("**Configuration**")
            cfg = strategy.config
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("**Filters**")
                st.write(f"Channels: {', '.join(cfg.channels)}")
                st.write(f"Directions: {', '.join(cfg.directions)}")
                st.write(f"Sessions: {', '.join(cfg.sessions)}")
                st.write(f"Price: ${cfg.price_min} - ${cfg.price_max}")

            with col2:
                st.markdown("**Entry**")
                st.write(f"Green Candles: {cfg.consec_green_candles}")
                st.write(f"Min Volume: {cfg.min_candle_volume:,}")

            with col3:
                st.markdown("**Exit**")
                st.write(f"Take Profit: {cfg.take_profit_pct}%")
                st.write(f"Stop Loss: {cfg.stop_loss_pct}%")
                st.write(f"Trailing Stop: {cfg.trailing_stop_pct}%")
                st.write(f"Timeout: {cfg.timeout_minutes} min")
                st.write(f"SL from Open: {'Yes' if cfg.stop_loss_from_open else 'No'}")

            # Live status if enabled
            if strategy.enabled and strategy_id in strategies_status:
                st.divider()
                st.markdown("**Live Status**")

                strat_status = strategies_status[strategy_id]

                col1, col2, col3 = st.columns(3)
                with col1:
                    pending = strat_status.get("pending_entries", [])
                    st.metric("Pending Entries", len(pending))
                    if pending:
                        st.write("Tickers: " + ", ".join(pending))

                with col2:
                    active = strat_status.get("active_trades", {})
                    st.metric("Active Trades", len(active))

                with col3:
                    st.metric("Completed Today", strat_status.get("completed_trades", 0))

                # Active trades details
                if active:
                    st.markdown("**Active Positions**")
                    for ticker, trade in active.items():
                        entry = trade.get("entry_price", 0)
                        current = trade.get("current_price", entry)
                        pnl_pct = trade.get("pnl_pct", 0)
                        pnl_dollars = trade.get("pnl_dollars", 0)
                        sl = trade.get("stop_loss", 0)
                        tp = trade.get("take_profit", 0)
                        shares = trade.get("shares", 0)

                        # Color P&L
                        pnl_color = "green" if pnl_pct >= 0 else "red"
                        pnl_str = f":{pnl_color}[{pnl_pct:+.2f}% (${pnl_dollars:+.2f})]"

                        st.write(f"**{ticker}**: {shares} @ ${entry:.2f} → ${current:.2f} {pnl_str} | SL: ${sl:.2f} | TP: ${tp:.2f}")
