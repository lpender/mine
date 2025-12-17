#!/usr/bin/env python3
"""
Cleanup script for orphaned positions.

Orphaned positions are those that exist in the database but their strategy is disabled.
These positions won't have stop-loss monitoring, so they should be cleaned up.

Usage:
    python cleanup_orphaned_positions.py              # Dry run (show what would be cleaned)
    python cleanup_orphaned_positions.py --execute    # Actually clean up
    python cleanup_orphaned_positions.py --sell       # Sell positions at broker and clean DB
"""

import argparse
import logging
import sys
from datetime import datetime

from src.database import init_db
from src.active_trade_store import get_active_trade_store
from src.strategy_store import get_strategy_store
from src.trading import get_trading_client
from src.trade_history import get_trade_history_client
from src.trade_logger import log_sell_fill

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def find_orphaned_positions():
    """Find positions in DB whose strategy is disabled."""
    trade_store = get_active_trade_store()
    strategy_store = get_strategy_store()

    all_trades = trade_store.get_all_trades()
    if not all_trades:
        logger.info("No active trades in database")
        return []

    # Get enabled strategy IDs
    enabled_strategies = strategy_store.list_strategies(enabled_only=True)
    enabled_ids = {s.id for s in enabled_strategies}

    # Find orphaned trades
    orphaned = []
    for trade in all_trades:
        if trade.strategy_id not in enabled_ids:
            orphaned.append(trade)

    return orphaned


def check_broker_positions(orphaned_trades, paper=True):
    """Check which orphaned trades still exist at the broker.

    Note: If a ticker has both orphaned AND active positions, we need to be careful.
    The orphaned position's shares might not match what's at the broker if another
    strategy is actively trading it.
    """
    if not orphaned_trades:
        return {}, {}

    trader = get_trading_client(paper=paper)
    trade_store = get_active_trade_store()

    try:
        broker_positions = trader.get_positions()
        broker_tickers = {p.ticker: p for p in broker_positions}
    except Exception as e:
        logger.error(f"Failed to fetch broker positions: {e}")
        return {}, {}

    # Split into positions that exist vs don't exist at broker
    at_broker = {}
    not_at_broker = {}

    for trade in orphaned_trades:
        if trade.ticker in broker_tickers:
            broker_pos = broker_tickers[trade.ticker]

            # Check if this ticker is ALSO being tracked by an active strategy
            all_trades_for_ticker = [t for t in trade_store.get_all_trades() if t.ticker == trade.ticker]

            # Find non-orphaned (active) trades for this ticker
            from src.strategy_store import get_strategy_store
            strategy_store = get_strategy_store()
            enabled_strategy_ids = {s.id for s in strategy_store.list_strategies(enabled_only=True)}

            active_trades_for_ticker = [t for t in all_trades_for_ticker if t.strategy_id in enabled_strategy_ids]

            if active_trades_for_ticker:
                # Ticker is actively being traded by another strategy
                # The orphaned position's shares probably don't exist at broker
                logger.debug(f"[{trade.ticker}] Has {len(active_trades_for_ticker)} active position(s), orphaned shares likely already closed")
                not_at_broker[f"{trade.ticker} (strategy: {trade.strategy_name})"] = trade
            else:
                # No active strategies trading this ticker, broker position is likely this orphaned one
                at_broker[trade.ticker] = (trade, broker_pos)
        else:
            not_at_broker[trade.ticker] = trade

    return at_broker, not_at_broker


def cleanup_database_only(trades, paper=True):
    """Remove orphaned trades from database (don't touch broker)."""
    trade_store = get_active_trade_store()
    trade_history = get_trade_history_client()

    cleaned = 0
    for trade in trades:
        logger.info(f"[{trade.ticker}] Removing from database: {trade.shares} shares @ ${trade.entry_price:.2f} (strategy: {trade.strategy_name})")

        # Record as orphaned in trade history
        try:
            trade_record = {
                "ticker": trade.ticker,
                "entry_price": trade.entry_price,
                "exit_price": trade.entry_price,  # No actual exit
                "entry_time": trade.entry_time,
                "exit_time": datetime.now(),
                "shares": trade.shares,
                "exit_reason": "orphaned_cleanup",
                "return_pct": 0,
                "pnl": 0,
                "strategy_params": {},
            }
            trade_history.save_trade(
                trade=trade_record,
                paper=paper,
                strategy_id=trade.strategy_id,
                strategy_name=trade.strategy_name or "Unknown",
            )
        except Exception as e:
            logger.warning(f"[{trade.ticker}] Failed to record to history: {e}")

        # Delete from active trades DB
        if trade_store.delete_trade(trade.ticker, trade.strategy_id):
            cleaned += 1

    return cleaned


def sell_and_cleanup(at_broker_dict, paper=True):
    """Sell positions at broker and clean up database."""
    trader = get_trading_client(paper=paper)
    trade_store = get_active_trade_store()
    trade_history = get_trade_history_client()

    sold = 0
    failed = []

    for ticker, (trade, broker_pos) in at_broker_dict.items():
        # Calculate current price from market value
        current_price = broker_pos.market_value / broker_pos.shares if broker_pos.shares > 0 else trade.entry_price
        logger.info(f"[{ticker}] Selling {broker_pos.shares} shares @ current price ${current_price:.2f}")

        try:
            # Submit sell order with current price as limit
            order = trader.sell(ticker, broker_pos.shares, limit_price=current_price)
            logger.info(f"[{ticker}] Sell order submitted: {order.status}")

            # Calculate P&L
            return_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            pnl = (current_price - trade.entry_price) * trade.shares

            # Log to trades.log
            log_sell_fill(
                ticker=ticker,
                shares=trade.shares,
                price=current_price,
                strategy_name=trade.strategy_name or "Unknown",
                entry_price=trade.entry_price,
                pnl=pnl,
                pnl_pct=return_pct,
                exit_reason="orphaned_cleanup_sell",
            )

            # Record to trade history
            try:
                trade_record = {
                    "ticker": ticker,
                    "entry_price": trade.entry_price,
                    "exit_price": current_price,
                    "entry_time": trade.entry_time,
                    "exit_time": datetime.now(),
                    "shares": trade.shares,
                    "exit_reason": "orphaned_cleanup_sell",
                    "return_pct": return_pct,
                    "pnl": pnl,
                    "strategy_params": {},
                }
                trade_history.save_trade(
                    trade=trade_record,
                    paper=paper,
                    strategy_id=trade.strategy_id,
                    strategy_name=trade.strategy_name or "Unknown",
                )
                logger.info(f"[{ticker}] Recorded to trade history: {return_pct:+.2f}% (${pnl:+.2f})")
            except Exception as e:
                logger.warning(f"[{ticker}] Failed to record to history: {e}")

            # Delete from active trades DB
            trade_store.delete_trade(ticker, trade.strategy_id)
            sold += 1

        except Exception as e:
            logger.error(f"[{ticker}] Failed to sell: {e}")
            failed.append((ticker, str(e)))

    return sold, failed


def main():
    parser = argparse.ArgumentParser(description="Clean up orphaned trading positions")
    parser.add_argument("--execute", action="store_true", help="Actually clean up (dry run by default)")
    parser.add_argument("--sell", action="store_true", help="Sell positions at broker before cleaning")
    parser.add_argument("--live", action="store_true", help="Use live trading account (paper by default)")
    args = parser.parse_args()

    paper = not args.live
    mode = "LIVE" if args.live else "PAPER"

    # Initialize database
    init_db()

    logger.info("=" * 60)
    logger.info(f"Orphaned Position Cleanup ({mode} mode)")
    logger.info("=" * 60)

    # Find orphaned positions
    orphaned = find_orphaned_positions()

    if not orphaned:
        logger.info("✅ No orphaned positions found!")
        return 0

    logger.info(f"\nFound {len(orphaned)} orphaned position(s):")
    for trade in orphaned:
        logger.info(f"  • {trade.ticker}: {trade.shares} shares @ ${trade.entry_price:.2f} (strategy: {trade.strategy_name})")

    # Check which exist at broker
    at_broker, not_at_broker = check_broker_positions(orphaned, paper=paper)

    if at_broker:
        logger.info(f"\n{len(at_broker)} position(s) still exist at broker:")
        for ticker, (trade, broker_pos) in at_broker.items():
            # Calculate current price from market value
            current_price = broker_pos.market_value / broker_pos.shares if broker_pos.shares > 0 else 0
            current_pnl = (current_price - trade.entry_price) * trade.shares
            current_pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            logger.info(f"  • {ticker}: {broker_pos.shares} shares @ ${current_price:.2f} (P&L: ${current_pnl:+.2f} / {current_pnl_pct:+.1f}%)")

    if not_at_broker:
        logger.info(f"\n{len(not_at_broker)} position(s) NOT at broker (manually closed or error):")
        for ticker, trade in not_at_broker.items():
            logger.info(f"  • {ticker}: {trade.shares} shares @ ${trade.entry_price:.2f}")

    # Dry run?
    if not args.execute:
        logger.info("\n" + "=" * 60)
        logger.info("DRY RUN - No changes made")
        logger.info("=" * 60)
        logger.info("\nTo clean up positions:")
        logger.info("  --execute              # Remove from DB only (don't sell)")
        logger.info("  --execute --sell       # Sell at broker AND remove from DB")
        if paper:
            logger.info("  --execute --live       # Use live account instead of paper")
        return 0

    # Execute cleanup
    logger.info("\n" + "=" * 60)
    logger.info("EXECUTING CLEANUP")
    logger.info("=" * 60)

    total_cleaned = 0

    # Sell positions at broker if requested
    if args.sell and at_broker:
        logger.info(f"\nSelling {len(at_broker)} position(s) at broker...")
        sold, failed = sell_and_cleanup(at_broker, paper=paper)
        total_cleaned += sold

        if failed:
            logger.error(f"\n❌ Failed to sell {len(failed)} position(s):")
            for ticker, error in failed:
                logger.error(f"  • {ticker}: {error}")

        logger.info(f"\n✅ Sold {sold} position(s)")

    # Clean up positions not at broker
    if not_at_broker:
        logger.info(f"\nCleaning up {len(not_at_broker)} position(s) from database...")
        cleaned = cleanup_database_only(list(not_at_broker.values()), paper=paper)
        total_cleaned += cleaned
        logger.info(f"✅ Cleaned up {cleaned} database record(s)")

    # If --sell wasn't used but positions exist at broker, warn
    if not args.sell and at_broker:
        logger.warning(f"\n⚠️  {len(at_broker)} position(s) still at broker - not sold (use --sell to sell them)")
        logger.info("Cleaning up database records only...")
        cleaned = cleanup_database_only([trade for trade, _ in at_broker.values()], paper=paper)
        total_cleaned += cleaned
        logger.info(f"✅ Cleaned up {cleaned} database record(s)")

    logger.info("\n" + "=" * 60)
    logger.info(f"✅ Cleanup complete: {total_cleaned} position(s) processed")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())

