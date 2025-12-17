#!/usr/bin/env python3
"""
Cleanup script for truly orphaned broker positions.

This script finds positions at the broker that DON'T have database records
(opposite of cleanup_orphaned_positions.py which finds DB records without active strategies).

These can occur when:
1. You ran cleanup_orphaned_positions.py --execute (without --sell)
2. Database records were deleted but broker positions remain

Usage:
    python cleanup_broker_orphans.py              # Dry run (show what would be sold)
    python cleanup_broker_orphans.py --execute    # Actually sell the positions
    python cleanup_broker_orphans.py --live       # Use live account instead of paper
"""

import argparse
import logging
import sys
from datetime import datetime

from src.database import init_db
from src.active_trade_store import get_active_trade_store
from src.trading import get_trading_client
from src.trade_store import get_trade_store
from src.trade_logger import log_sell_fill

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def find_untracked_broker_positions(paper=True):
    """Find positions at broker that don't have database records."""
    trader = get_trading_client(paper=paper)
    trade_store = get_active_trade_store()

    try:
        broker_positions = trader.get_positions()
    except Exception as e:
        logger.error(f"Failed to fetch broker positions: {e}")
        return []

    if not broker_positions:
        logger.info("No positions at broker")
        return []

    # Get all tracked tickers from database
    all_trades = trade_store.get_all_trades()
    tracked_tickers = {trade.ticker for trade in all_trades}

    # Find positions at broker that aren't tracked
    untracked = []
    for pos in broker_positions:
        if pos.ticker not in tracked_tickers:
            untracked.append(pos)

    return untracked


def sell_positions(positions, paper=True):
    """Sell positions at broker and record to trade history."""
    if not positions:
        return 0, []

    trader = get_trading_client(paper=paper)
    trade_history = get_trade_store()

    sold = 0
    failed = []

    for pos in positions:
        # Calculate current price from market value
        current_price = pos.market_value / pos.shares if pos.shares > 0 else 0
        logger.info(f"[{pos.ticker}] Selling {pos.shares} shares @ ${current_price:.2f}")

        try:
            # Submit sell order with current price as limit
            order = trader.sell(pos.ticker, pos.shares, limit_price=current_price)
            logger.info(f"[{pos.ticker}] Sell order submitted: {order.status}")

            # Calculate P&L from entry price
            return_pct = ((current_price - pos.avg_entry_price) / pos.avg_entry_price) * 100
            pnl = pos.unrealized_pl

            # Log to trades.log
            log_sell_fill(
                ticker=pos.ticker,
                shares=pos.shares,
                price=current_price,
                strategy_name="Orphaned (no DB record)",
                entry_price=pos.avg_entry_price,
                pnl=pnl,
                pnl_pct=return_pct,
                exit_reason="broker_orphan_cleanup",
            )

            # Record to trade history (with unknown strategy)
            try:
                trade_record = {
                    "ticker": pos.ticker,
                    "entry_price": pos.avg_entry_price,
                    "exit_price": current_price,
                    "entry_time": datetime.now(),  # We don't know actual entry time
                    "exit_time": datetime.now(),
                    "shares": pos.shares,
                    "exit_reason": "broker_orphan_cleanup",
                    "return_pct": return_pct,
                    "pnl": pnl,
                    "strategy_params": {},
                }
                trade_history.save_trade(
                    trade=trade_record,
                    paper=paper,
                    strategy_id="unknown",
                    strategy_name="Orphaned (no DB record)",
                )
                logger.info(f"[{pos.ticker}] Recorded to trade history: {return_pct:+.2f}% (${pnl:+.2f})")
            except Exception as e:
                logger.warning(f"[{pos.ticker}] Failed to record to history: {e}")

            sold += 1

        except Exception as e:
            logger.error(f"[{pos.ticker}] Failed to sell: {e}")
            failed.append((pos.ticker, str(e)))

    return sold, failed


def main():
    parser = argparse.ArgumentParser(description="Clean up truly orphaned broker positions (no DB records)")
    parser.add_argument("--execute", action="store_true", help="Actually sell positions (dry run by default)")
    parser.add_argument("--live", action="store_true", help="Use live trading account (paper by default)")
    args = parser.parse_args()

    paper = not args.live
    mode = "LIVE" if args.live else "PAPER"

    # Initialize database
    init_db()

    logger.info("=" * 60)
    logger.info(f"Broker Orphan Cleanup ({mode} mode)")
    logger.info("=" * 60)

    # Find untracked positions at broker
    untracked = find_untracked_broker_positions(paper=paper)

    if not untracked:
        logger.info("✅ No untracked positions at broker!")
        return 0

    logger.info(f"\nFound {len(untracked)} untracked position(s) at broker:")
    for pos in untracked:
        current_price = pos.market_value / pos.shares if pos.shares > 0 else 0
        pnl_pct = ((current_price - pos.avg_entry_price) / pos.avg_entry_price) * 100
        logger.info(
            f"  • {pos.ticker}: {pos.shares} shares @ ${pos.avg_entry_price:.2f} "
            f"→ ${current_price:.2f} (P&L: ${pos.unrealized_pl:+.2f} / {pnl_pct:+.1f}%)"
        )

    # Dry run?
    if not args.execute:
        logger.info("\n" + "=" * 60)
        logger.info("DRY RUN - No changes made")
        logger.info("=" * 60)
        logger.info("\nTo sell these positions:")
        logger.info("  python cleanup_broker_orphans.py --execute")
        if paper:
            logger.info("  python cleanup_broker_orphans.py --execute --live  # Use live account")
        return 0

    # Execute cleanup
    logger.info("\n" + "=" * 60)
    logger.info("EXECUTING CLEANUP")
    logger.info("=" * 60)

    logger.info(f"\nSelling {len(untracked)} position(s)...")
    sold, failed = sell_positions(untracked, paper=paper)

    if failed:
        logger.error(f"\n❌ Failed to sell {len(failed)} position(s):")
        for ticker, error in failed:
            logger.error(f"  • {ticker}: {error}")

    logger.info("\n" + "=" * 60)
    logger.info(f"✅ Cleanup complete: {sold} position(s) sold")
    logger.info("=" * 60)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

