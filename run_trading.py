#!/usr/bin/env python3
"""
Standalone trading engine runner.

Run this from the command line to start the trading engine independently
of the Streamlit UI. The engine will run continuously, processing alerts
and managing positions.

Usage:
    python run_trading.py [--live]

Options:
    --live    Run in live trading mode (default is paper trading)

The engine loads enabled strategies from the database and processes
alerts from the alert service. Status is written to a JSON file that
the Streamlit UI can read.

To stop: Press Ctrl+C
"""

import argparse
import logging
import signal
import sys
import time

from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

from src.database import init_db
from src.alert_service import start_alert_service
from src.live_trading_service import (
    start_live_trading,
    stop_live_trading,
    is_live_trading_active,
    is_trading_locked,
    force_release_trading_lock,
    get_live_trading_status,
)

# Create logs directory if needed
import os
os.makedirs('logs', exist_ok=True)

# Configure logging - both stdout and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# Add file handler for all trading logs (tail -f logs/dev.log)
trading_handler = logging.FileHandler('logs/dev.log')
trading_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logging.getLogger().addHandler(trading_handler)

# Reduce noise from some loggers
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)

# Set up separate file handler for volume/candle logs
# These go to logs/volume.log instead of stdout (tail -f logs/volume.log)
quotes_logger = logging.getLogger('src.strategy.quotes')
quotes_logger.setLevel(logging.INFO)
quotes_logger.propagate = False  # Don't send to root logger (stdout)

# File handler for volume logs
quotes_handler = logging.FileHandler('logs/volume.log')
quotes_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M:%S'))
quotes_logger.addHandler(quotes_handler)

# Set up separate file handler for subscription limit errors
# These go to logs/limits.log (tail -f logs/limits.log)
limits_logger = logging.getLogger('src.quote_provider.limits')
limits_logger.setLevel(logging.INFO)
limits_logger.propagate = False  # Don't send to root logger (stdout)

# File handler for limits logs
limits_handler = logging.FileHandler('logs/limits.log')
limits_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
limits_logger.addHandler(limits_handler)

# Set up separate file handler for strategy status updates (price monitoring)
# These go to logs/prices.log instead of stdout (tail -f logs/prices.log)
status_logger = logging.getLogger('src.strategy.status')
status_logger.setLevel(logging.INFO)
status_logger.propagate = False  # Don't send to root logger (stdout)

# File handler for price monitoring logs
status_handler = logging.FileHandler('logs/prices.log')
status_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(name)s] %(message)s',
    datefmt='%H:%M:%S'
))
status_logger.addHandler(status_handler)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info("\nShutdown signal received...")
    stop_live_trading()
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description='Run the trading engine')
    parser.add_argument('--live', action='store_true', help='Run in live trading mode')
    parser.add_argument('--force', action='store_true', help='Force start even if locked')
    args = parser.parse_args()

    paper_mode = not args.live
    mode_str = "PAPER" if paper_mode else "LIVE"

    print(f"\n{'='*60}")
    print(f"  Trading Engine - {mode_str} MODE")
    print(f"{'='*60}\n")

    # Check if already running
    if is_trading_locked():
        if args.force:
            logger.warning("Forcing release of existing lock...")
            force_release_trading_lock()
        else:
            logger.error("Trading engine is already running!")
            logger.error("Use --force to override, or stop the other instance first.")
            sys.exit(1)

    # Initialize database
    logger.info("Initializing database...")
    init_db()

    # Start alert service (this process must own it for callbacks to work)
    logger.info("Starting alert service...")
    try:
        start_alert_service(port=8765)
    except OSError as e:
        if "Address already in use" in str(e):
            logger.error("Port 8765 already in use! Stop any other processes using it (e.g., Streamlit).")
            logger.error("The trading engine must own the alert service for callbacks to work.")
            sys.exit(1)
        raise

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start trading engine
    logger.info(f"Starting trading engine ({mode_str} mode)...")
    engine = start_live_trading(paper=paper_mode)

    if not engine:
        logger.error("Failed to start trading engine")
        sys.exit(1)

    logger.info("Trading engine started successfully!")
    logger.info("Press Ctrl+C to stop\n")

    # Main loop - just keep running and show status
    try:
        while is_live_trading_active():
            status = get_live_trading_status()
            if status:
                strategies = status.get('strategy_count', 0)
                pending = len(status.get('pending_entries', []))
                active = len(status.get('active_trades', {}))
                completed = status.get('completed_trades', 0)

                # Only log periodically (every 30 seconds)
                # The actual status is already being written to file
                pass

            time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Stopping trading engine...")
        stop_live_trading()
        logger.info("Trading engine stopped.")


if __name__ == '__main__':
    main()
