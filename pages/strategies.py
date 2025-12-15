"""Strategy management page."""

import streamlit as st
import pandas as pd
import time
from datetime import datetime

from src.database import init_db
from src.strategy import StrategyConfig
from src.strategy_store import get_strategy_store, Strategy
from src.active_trade_store import get_active_trade_store
from src.trading import get_trading_client
from src.live_trading_service import (
    get_live_trading_status,
    is_live_trading_active,
    enable_strategy,
    disable_strategy,
)
# Initialize database tables
init_db()

# Note: Alert service is now started by run_trading.py only

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
        stake_mode = st.radio(
            "Sizing Mode",
            ["fixed", "volume_pct"],
            format_func=lambda x: "Fixed Dollar Amount" if x == "fixed" else "% of Previous Candle Volume",
            horizontal=True,
        )

        if stake_mode == "fixed":
            stake = st.number_input("Stake per Trade ($)", value=50.0, min_value=1.0)
            volume_pct = 1.0  # Default
            max_stake = 10000.0  # Default
        else:
            col1, col2 = st.columns(2)
            with col1:
                volume_pct = st.number_input("Volume %", value=1.0, min_value=0.1, max_value=100.0, help="Buy this % of the previous candle's volume")
            with col2:
                max_stake = st.number_input("Max Cost ($)", value=10000.0, min_value=1.0, help="Maximum position cost cap")
            stake = 50.0  # Default for fixed mode

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
                    stake_mode=stake_mode,
                    stake_amount=stake,
                    volume_pct=volume_pct,
                    max_stake=max_stake,
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

        # Format position sizing display
        if s.config.stake_mode == "volume_pct":
            sizing_str = f"{s.config.volume_pct}% vol (max ${s.config.max_stake:.0f})"
        else:
            sizing_str = f"${s.config.stake_amount:.0f}"

        rows.append({
            "id": s.id,
            "priority": s.priority,
            "#": s.priority + 1,  # 1-indexed for display
            "Name": s.name,
            "Enabled": s.enabled,
            "Pending": pending,
            "Active": active,
            "Completed": completed,
            "Sizing": sizing_str,
            "TP/SL": f"{s.config.take_profit_pct:.0f}% / {s.config.stop_loss_pct:.0f}%",
        })

    df = pd.DataFrame(rows)

    # Display with selection (hide internal columns)
    event = st.dataframe(
        df.drop(columns=["id", "priority"]),
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

            # Strategy header with priority controls
            header_cols = st.columns([3, 1, 1, 1])
            with header_cols[0]:
                st.subheader(f"Strategy: {strategy.name}")
            with header_cols[1]:
                st.caption(f"Priority: #{strategy.priority + 1}")
            with header_cols[2]:
                if st.button("⬆️ Move Up", key=f"up_{strategy_id}", disabled=strategy.priority == 0):
                    store.move_strategy_up(strategy_id)
                    st.rerun()
            with header_cols[3]:
                # Check if at bottom (highest priority number)
                max_priority = max(s.priority for s in strategies)
                if st.button("⬇️ Move Down", key=f"down_{strategy_id}", disabled=strategy.priority >= max_priority):
                    store.move_strategy_down(strategy_id)
                    st.rerun()

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
                # Load in Backtest - direct navigation with strategy params
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
                    "stake_mode": cfg.stake_mode,
                    "stake": str(cfg.stake_amount),
                    "vol_pct": str(cfg.volume_pct),
                    "max_stake": str(cfg.max_stake),
                }
                query_string = "&".join(f"{k}={v}" for k, v in params.items())
                # Use markdown link styled as button - opens in same tab
                st.markdown(
                    f'<a href="../?{query_string}" target="_self" style="'
                    'display: inline-block; padding: 0.25rem 0.75rem; '
                    'background-color: rgb(49, 51, 63); color: white; '
                    'border-radius: 0.25rem; text-decoration: none; '
                    'font-size: 14px; font-weight: 400; text-align: center;">'
                    'Load in Backtest</a>',
                    unsafe_allow_html=True
                )

            with col3:
                if st.button("Edit Sizing"):
                    st.session_state[f"edit_sizing_{strategy_id}"] = True

            with col4:
                if st.button("Delete", type="secondary"):
                    if strategy.enabled:
                        st.error("Disable strategy before deleting")
                    else:
                        # Check for active trades in DB
                        active_store = get_active_trade_store()
                        active_trades = active_store.get_trades_for_strategy(strategy_id)
                        if active_trades:
                            st.session_state[f"confirm_delete_{strategy_id}"] = True
                            st.warning(f"Found {len(active_trades)} active trade(s) in DB for this strategy")
                        else:
                            store.delete_strategy(strategy_id)
                            st.success(f"Deleted strategy '{strategy.name}'")
                            st.rerun()

            # Delete confirmation dialog
            if st.session_state.get(f"confirm_delete_{strategy_id}", False):
                st.divider()
                st.markdown("**⚠️ Delete Confirmation**")
                active_store = get_active_trade_store()
                active_trades = active_store.get_trades_for_strategy(strategy_id)
                st.warning(f"This strategy has {len(active_trades)} active trade record(s) in the database:")
                for trade in active_trades:
                    st.write(f"- **{trade.ticker}**: {trade.shares} shares @ ${trade.entry_price:.2f}")

                st.info("If these positions are still open at the broker, they should be sold first.")

                col_sell, col_force, col_cancel = st.columns(3)
                with col_sell:
                    if st.button("🔴 Sell Positions & Delete", type="primary", key=f"sell_delete_{strategy_id}"):
                        try:
                            trader = get_trading_client()
                            for trade in active_trades:
                                try:
                                    order = trader.sell(trade.ticker, trade.shares)
                                    st.info(f"Sold {trade.ticker}: {order.status}")
                                except Exception as e:
                                    st.warning(f"Failed to sell {trade.ticker}: {e}")
                        except Exception as e:
                            st.error(f"Failed to connect to broker: {e}")
                        # Delete the strategy (will clean up trade records)
                        store.delete_strategy(strategy_id)
                        st.session_state[f"confirm_delete_{strategy_id}"] = False
                        st.success(f"Deleted strategy '{strategy.name}'")
                        st.rerun()

                with col_force:
                    if st.button("⚠️ Force Delete (no sell)", type="secondary", key=f"force_delete_{strategy_id}"):
                        store.delete_strategy(strategy_id)
                        st.session_state[f"confirm_delete_{strategy_id}"] = False
                        st.success(f"Force deleted strategy '{strategy.name}' (positions NOT sold)")
                        st.rerun()

                with col_cancel:
                    if st.button("Cancel", key=f"cancel_delete_{strategy_id}"):
                        st.session_state[f"confirm_delete_{strategy_id}"] = False
                        st.rerun()

            # Configuration details
            st.markdown("**Configuration**")
            cfg = strategy.config
            col1, col2, col3, col4 = st.columns(4)

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

            with col4:
                st.markdown("**Position Sizing**")
                if cfg.stake_mode == "volume_pct":
                    st.write(f"Mode: Volume %")
                    st.write(f"Volume: {cfg.volume_pct}%")
                    st.write(f"Max Cost: ${cfg.max_stake:,.0f}")
                else:
                    st.write(f"Mode: Fixed")
                    st.write(f"Stake: ${cfg.stake_amount:.0f}")

            # Edit Position Sizing form
            if st.session_state.get(f"edit_sizing_{strategy_id}", False):
                st.divider()
                st.markdown("**Edit Position Sizing**")

                edit_mode = st.radio(
                    "Sizing Mode",
                    ["fixed", "volume_pct"],
                    index=0 if cfg.stake_mode == "fixed" else 1,
                    format_func=lambda x: "Fixed Dollar Amount" if x == "fixed" else "% of Previous Candle Volume",
                    horizontal=True,
                    key=f"edit_mode_{strategy_id}",
                )

                if edit_mode == "fixed":
                    edit_stake = st.number_input(
                        "Stake per Trade ($)",
                        value=cfg.stake_amount,
                        min_value=1.0,
                        key=f"edit_stake_{strategy_id}",
                    )
                    edit_vol_pct = cfg.volume_pct
                    edit_max_stake = cfg.max_stake
                else:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        edit_vol_pct = st.number_input(
                            "Volume %",
                            value=cfg.volume_pct,
                            min_value=0.1,
                            max_value=100.0,
                            key=f"edit_vol_pct_{strategy_id}",
                        )
                    with col_b:
                        edit_max_stake = st.number_input(
                            "Max Cost ($)",
                            value=cfg.max_stake,
                            min_value=1.0,
                            key=f"edit_max_stake_{strategy_id}",
                        )
                    edit_stake = cfg.stake_amount

                col_save, col_cancel = st.columns(2)
                with col_save:
                    if st.button("Save Changes", type="primary", key=f"save_{strategy_id}"):
                        # Update config
                        new_config = StrategyConfig(
                            channels=cfg.channels,
                            directions=cfg.directions,
                            sessions=cfg.sessions,
                            price_min=cfg.price_min,
                            price_max=cfg.price_max,
                            consec_green_candles=cfg.consec_green_candles,
                            min_candle_volume=cfg.min_candle_volume,
                            take_profit_pct=cfg.take_profit_pct,
                            stop_loss_pct=cfg.stop_loss_pct,
                            trailing_stop_pct=cfg.trailing_stop_pct,
                            stop_loss_from_open=cfg.stop_loss_from_open,
                            timeout_minutes=cfg.timeout_minutes,
                            stake_mode=edit_mode,
                            stake_amount=edit_stake,
                            volume_pct=edit_vol_pct,
                            max_stake=edit_max_stake,
                        )
                        store.update_strategy(strategy_id, config=new_config)
                        st.session_state[f"edit_sizing_{strategy_id}"] = False
                        st.success("Position sizing updated!")
                        st.rerun()
                with col_cancel:
                    if st.button("Cancel", key=f"cancel_{strategy_id}"):
                        st.session_state[f"edit_sizing_{strategy_id}"] = False
                        st.rerun()

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
                        timeout_at = trade.get("timeout_at")

                        # Color P&L
                        pnl_color = "green" if pnl_pct >= 0 else "red"
                        pnl_str = f":{pnl_color}[{pnl_pct:+.2f}% (${pnl_dollars:+.2f})]"

                        # Format timeout time
                        timeout_str = ""
                        if timeout_at:
                            timeout_dt = datetime.fromisoformat(timeout_at)
                            timeout_str = f" | Timeout: {timeout_dt.strftime('%H:%M:%S')}"

                        # Show stale data warning
                        last_quote = trade.get("last_quote_time")
                        stale_str = ""
                        if last_quote:
                            last_dt = datetime.fromisoformat(last_quote)
                            age_secs = (datetime.now() - last_dt).total_seconds()
                            if age_secs > 30:
                                stale_str = f" :orange[(stale: {int(age_secs)}s ago)]"

                        # Show manual exit warning
                        manual_exit_str = ""
                        if trade.get("needs_manual_exit"):
                            manual_exit_str = " :red[⚠️ NEEDS MANUAL EXIT]"
                        elif trade.get("sell_attempts", 0) > 0:
                            attempts = trade.get("sell_attempts", 0)
                            manual_exit_str = f" :orange[(sell attempts: {attempts}/3)]"

                        st.write(f"**{ticker}**: {shares} @ ${entry:.2f} → ${current:.2f} {pnl_str}{stale_str} | SL: ${sl:.2f} | TP: ${tp:.2f}{timeout_str}{manual_exit_str}")
