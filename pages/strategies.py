"""Strategy management page."""

import streamlit as st
import pandas as pd
import time
from datetime import datetime

from src.database import init_db, SessionLocal, TradeDB
from src.strategy import StrategyConfig
from src.strategy_store import get_strategy_store, Strategy
from src.active_trade_store import get_active_trade_store
from src.pending_entry_store import get_pending_entry_store
from src.trading import get_trading_client
from datetime import date
from src.live_trading_service import (
    get_live_trading_status,
    is_live_trading_active,
    enable_strategy,
    disable_strategy,
    exit_all_positions,
    exit_orphaned_positions,
    get_orphaned_positions,
    _exit_strategy_positions,
)
# Initialize database tables
init_db()

# Initialize stores for database access
pending_store = get_pending_entry_store()
active_store = get_active_trade_store()

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

        # Orphaned positions warning
        orphaned = status.get("orphaned_tickers", [])
        if orphaned:
            st.divider()
            st.error(f"**Orphaned Positions!**\n\n{', '.join(orphaned)}\n\nThese positions exist but their strategy is DISABLED. Stop losses will NOT be enforced!")

        # Exit positions buttons
        st.divider()
        col_exit_all, col_exit_orphaned = st.columns(2)
        with col_exit_all:
            if st.button("🔴 Exit All", type="secondary", key="exit_all"):
                with st.spinner("Exiting positions..."):
                    results = exit_all_positions(paper=is_paper)
                    if results:
                        for ticker, result in results.items():
                            if "failed" in result.lower():
                                st.error(f"{ticker}: {result}")
                            else:
                                st.success(f"{ticker}: {result}")
                    else:
                        st.info("No positions to exit")

        with col_exit_orphaned:
            if st.button("🧹 Exit Orphans", type="secondary", key="exit_orphaned"):
                with st.spinner("Checking for orphaned positions..."):
                    try:
                        orphaned = get_orphaned_positions(paper=is_paper)
                        if orphaned:
                            st.write(f"Found {len(orphaned)} orphaned position(s):")
                            for ticker, shares, price in orphaned:
                                st.write(f"  • {ticker}: {shares} shares @ ${price:.2f}")
                            results = exit_orphaned_positions(paper=is_paper)
                            for ticker, result in results.items():
                                if "failed" in result.lower():
                                    st.error(f"{ticker}: {result}")
                                else:
                                    st.success(f"{ticker}: {result}")
                        else:
                            st.info("No orphaned positions found")
                    except Exception as e:
                        st.error(f"Error checking orphans: {e}")

        # See open positions button
        if st.button("📊 See Open Positions", key="see_positions"):
            try:
                trader = get_trading_client(paper=is_paper)
                positions = trader.get_positions()

                # Build strategy lookup from database (not in-memory status)
                strategy_lookup = {}  # ticker -> strategy name
                all_active_trades = active_store.get_all_trades()
                for trade in all_active_trades:
                    strategy_lookup[trade.ticker] = trade.strategy_name or "Unknown"

                if positions:
                    st.markdown("**Broker Positions:**")
                    for pos in positions:
                        strategy_name = strategy_lookup.get(pos.ticker, "Unknown")
                        pnl_color = "green" if pos.unrealized_pl >= 0 else "red"
                        st.markdown(
                            f"• **{pos.ticker}**: {pos.shares} shares @ ${pos.avg_entry_price:.2f} "
                            f"| P/L: :{pnl_color}[${pos.unrealized_pl:.2f} ({pos.unrealized_pl_pct:.1f}%)] "
                            f"| Strategy: *{strategy_name}*"
                        )
                else:
                    st.info("No open positions at broker")
            except Exception as e:
                st.error(f"Error fetching positions: {e}")
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
    st.markdown("[View Trades →](trades)")

store = get_strategy_store()


def get_completed_today_count(strategy_id: str) -> int:
    """Get count of trades completed today for a strategy from the database."""
    session = SessionLocal()
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        count = session.query(TradeDB).filter(
            TradeDB.strategy_id == strategy_id,
            TradeDB.exit_time >= today_start,
        ).count()
        return count
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Strategy List
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("Your Strategies")

try:
    strategies = store.list_strategies()
except Exception as e:
    st.error(f"Error loading strategies: {e}")
    st.stop()

if not strategies:
    st.info("No strategies created yet. Use the form above to create one.")
else:
    # Get live status if engine is running (for WebSocket status only)
    live_status = get_live_trading_status() or {}
    strategies_status = live_status.get("strategies", {})

    # Build dataframe - always read counts from database for persistence
    rows = []
    try:
        for s in strategies:
            # Always read from database for accurate counts
            db_pending = pending_store.get_entries_for_strategy(s.id)
            db_active = active_store.get_trades_for_strategy(s.id)
            db_completed = get_completed_today_count(s.id)

            # Use strings for all columns to avoid Arrow mixed-type errors
            pending = str(len(db_pending)) if s.enabled else "-"
            active = str(len(db_active)) if s.enabled else "-"
            completed = str(db_completed) if s.enabled else "-"

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
    except Exception as e:
        st.error(f"Error building strategy table: {e}")
        import traceback
        st.code(traceback.format_exc())
        st.stop()

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
                        # Check for active trades before disabling
                        has_active_trades = False

                        # First check live status (if engine is running)
                        if trading_active:
                            strategy_status = strategies_status.get(strategy_id, {})
                            active_trades_dict = strategy_status.get("active_trades", {})
                            has_active_trades = len(active_trades_dict) > 0
                        else:
                            # Engine not running - check database
                            active_store = get_active_trade_store()
                            db_trades = active_store.get_trades_for_strategy(strategy_id)
                            has_active_trades = len(db_trades) > 0

                        if has_active_trades:
                            # Has active trades - show confirmation
                            st.session_state[f"confirm_disable_{strategy_id}"] = True
                            st.rerun()
                        else:
                            # No active trades - disable immediately
                            if disable_strategy(strategy_id):
                                st.success(f"Disabled strategy '{strategy.name}'")
                            else:
                                st.error(f"Failed to disable strategy '{strategy.name}' - check logs")
                            st.rerun()
                else:
                    if st.button("Enable", type="primary"):
                        enable_strategy(strategy_id)
                        st.rerun()

            # Disable confirmation dialog
            if st.session_state.get(f"confirm_disable_{strategy_id}", False):
                st.divider()
                st.markdown("**⚠️ Disable Strategy Confirmation**")

                # Get active trades (from live status or database)
                if trading_active:
                    # Engine running - use live status
                    strategy_status = strategies_status.get(strategy_id, {})
                    active_trades_dict = strategy_status.get("active_trades", {})

                    st.warning(f"This strategy has {len(active_trades_dict)} active position(s):")
                    for ticker, trade_info in active_trades_dict.items():
                        shares = trade_info.get("shares", 0)
                        entry = trade_info.get("entry_price", 0)
                        current = trade_info.get("current_price", entry)
                        pnl_pct = trade_info.get("pnl_pct", 0)
                        st.write(f"- **{ticker}**: {shares} shares @ ${entry:.2f} (current: ${current:.2f}, {pnl_pct:+.2f}%)")
                else:
                    # Engine not running - check database
                    active_store = get_active_trade_store()
                    db_trades = active_store.get_trades_for_strategy(strategy_id)

                    st.warning(f"This strategy has {len(db_trades)} active position(s) in database:")
                    for trade in db_trades:
                        st.write(f"- **{trade.ticker}**: {trade.shares} shares @ ${trade.entry_price:.2f}")
                    st.info("Note: Trading engine is not running. Positions cannot be sold automatically.")

                st.info("Disabling will sell all positions at market price (if engine is running).")

                col_disable, col_cancel = st.columns(2)
                with col_disable:
                    if st.button("🔴 Disable & Sell Positions", type="primary", key=f"confirm_disable_yes_{strategy_id}"):
                        with st.spinner("Disabling strategy and selling positions..."):
                            success = disable_strategy(strategy_id)
                        st.session_state[f"confirm_disable_{strategy_id}"] = False

                        if success:
                            if trading_active:
                                st.success(f"Disabled strategy '{strategy.name}' and sold positions")
                            else:
                                st.warning(f"Disabled strategy '{strategy.name}' - positions NOT sold (engine not running)")
                        else:
                            st.error(f"Failed to disable strategy '{strategy.name}' - could not sell all positions. Check logs and manually close positions if needed.")
                        st.rerun()

                with col_cancel:
                    if st.button("Cancel", key=f"cancel_disable_{strategy_id}"):
                        st.session_state[f"confirm_disable_{strategy_id}"] = False
                        st.rerun()

            with col2:
                # Load in Backtest - show link button with query params
                cfg = strategy.config

                params = {
                    # Filters
                    "channel": ",".join(cfg.channels) if cfg.channels else "",
                    "direction": ",".join(cfg.directions) if cfg.directions else "",
                    "sess": ",".join(cfg.sessions) if cfg.sessions else "premarket,market",
                    "country_blacklist": ",".join(cfg.country_blacklist) if cfg.country_blacklist else "",
                    "price_min": str(cfg.price_min),
                    "price_max": str(cfg.price_max),
                    "max_mentions": str(cfg.max_intraday_mentions) if cfg.max_intraday_mentions else "",
                    "no_fin": "1" if cfg.exclude_financing_headlines else "0",
                    "exclude_biotech": "1" if cfg.exclude_biotech else "0",
                    "max_prior_move": str(cfg.max_prior_move_pct) if cfg.max_prior_move_pct else "",
                    "max_mcap": str(cfg.max_market_cap_millions) if cfg.max_market_cap_millions else "",
                    # Entry rules
                    "consec": str(cfg.consec_green_candles),
                    "min_vol": str(cfg.min_candle_volume),
                    "entry_window": str(cfg.entry_window_minutes),
                    # Exit rules
                    "tp": str(cfg.take_profit_pct),
                    "sl": str(cfg.stop_loss_pct),
                    "trail": str(cfg.trailing_stop_pct),
                    "sl_open": "1" if cfg.stop_loss_from_open else "0",
                    "hold": str(cfg.timeout_minutes),
                    # Position sizing
                    "stake_mode": cfg.stake_mode,
                    "stake": str(cfg.stake_amount),
                    "vol_pct": str(cfg.volume_pct),
                    "max_stake": str(cfg.max_stake),
                }
                # Note: The following dashboard filters are NOT saved in strategy:
                # - author, country (whitelist), has_hl, no_hl
                # - float_min, float_max, mc_min, mc_max (separate from max_mcap)
                # - nhod, nsh, rvol_min, rvol_max, prior_move_min
                # - exclude_financing (types list, only boolean no_fin is saved)
                # - sort, asc, sample_pct, sample_seed

                # Filter out empty parameters
                filtered_params = {k: v for k, v in params.items() if v != ""}

                # Construct URL with query params
                from urllib.parse import urlencode
                query_string = urlencode(filtered_params)

                st.link_button("Load in Backtest", f"/?{query_string}", use_container_width=True)

            with col3:
                if st.button("Edit Sizing"):
                    st.session_state[f"edit_sizing_{strategy_id}"] = True

            with col4:
                if st.button("Delete", type="secondary"):
                    # If strategy is enabled, disable it first (which exits positions)
                    if strategy.enabled:
                        with st.spinner("Disabling strategy and exiting positions..."):
                            success = disable_strategy(strategy_id)
                        if success:
                            st.success(f"Disabled strategy '{strategy.name}' and exited positions")
                        else:
                            st.error(f"Failed to exit all positions for '{strategy.name}'. Cannot delete until positions are closed.")
                        st.rerun()
                    else:
                        # Check for active trades in DB (should be none after disabling)
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
                            results = _exit_strategy_positions(active_trades, trader, context="ui")
                            for ticker, success_msg, error_msg in results:
                                if success_msg:
                                    st.info(success_msg)
                                if error_msg:
                                    st.warning(error_msg)
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
                if cfg.country_blacklist:
                    st.write(f"Blacklist: {', '.join(cfg.country_blacklist)}")
                if cfg.max_intraday_mentions:
                    st.write(f"Max Mentions: {cfg.max_intraday_mentions}")
                if cfg.exclude_financing_headlines:
                    st.write("Exclude Financing: Yes")
                if cfg.exclude_biotech:
                    st.write("Exclude Biotech: Yes")
                if cfg.max_prior_move_pct:
                    st.write(f"Max Prior Move: {cfg.max_prior_move_pct}%")
                if cfg.max_market_cap_millions:
                    st.write(f"Max Market Cap: ${cfg.max_market_cap_millions}M")

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
                            country_blacklist=cfg.country_blacklist,
                            max_intraday_mentions=cfg.max_intraday_mentions,
                            exclude_financing_headlines=cfg.exclude_financing_headlines,
                            exclude_biotech=cfg.exclude_biotech,
                            max_prior_move_pct=cfg.max_prior_move_pct,
                            max_market_cap_millions=cfg.max_market_cap_millions,
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

            # Live status if enabled - always read from database
            if strategy.enabled:
                st.divider()
                st.markdown("**Live Status**")

                # Read from database for persistence across restarts
                db_pending = pending_store.get_entries_for_strategy(strategy_id)
                db_active = active_store.get_trades_for_strategy(strategy_id)
                db_completed = get_completed_today_count(strategy_id)

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Pending Entries", len(db_pending))
                    if db_pending:
                        tickers = [e.ticker for e in db_pending]
                        st.write("Tickers: " + ", ".join(tickers))

                with col2:
                    st.metric("Active Trades", len(db_active))

                with col3:
                    st.metric("Completed Today", db_completed)

                # Active trades details from database
                if db_active:
                    st.markdown("**Active Positions**")
                    for trade in db_active:
                        ticker = trade.ticker
                        entry = trade.entry_price
                        current = trade.last_price if trade.last_price else entry
                        pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
                        pnl_dollars = (current - entry) * trade.shares
                        sl = trade.stop_loss_price
                        tp = trade.take_profit_price
                        shares = trade.shares

                        # Color P&L
                        pnl_color = "green" if pnl_pct >= 0 else "red"
                        pnl_str = f":{pnl_color}[{pnl_pct:+.2f}% (${pnl_dollars:+.2f})]"

                        # Show stale data warning
                        stale_str = ""
                        if trade.last_quote_time:
                            age_secs = (datetime.utcnow() - trade.last_quote_time).total_seconds()
                            if age_secs > 30:
                                stale_str = f" :orange[(stale: {int(age_secs)}s ago)]"

                        st.write(f"**{ticker}**: {shares} @ ${entry:.2f} → ${current:.2f} {pnl_str}{stale_str} | SL: ${sl:.2f} | TP: ${tp:.2f}")
