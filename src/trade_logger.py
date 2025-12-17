"""Dedicated logger for trade executions (buys and sells)."""

import logging
from pathlib import Path
from datetime import datetime

# Create logs directory if it doesn't exist
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Create a dedicated logger for trade executions
trade_logger = logging.getLogger("trade_executions")
trade_logger.setLevel(logging.INFO)
trade_logger.propagate = False  # Don't propagate to root logger

# File handler for trade executions
trade_file_handler = logging.FileHandler(LOGS_DIR / "trades.log")
trade_file_handler.setLevel(logging.INFO)

# Format: timestamp [BUY/SELL] ticker shares @ price | details
trade_formatter = logging.Formatter(
    '%(asctime)s [%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
trade_file_handler.setFormatter(trade_formatter)

trade_logger.addHandler(trade_file_handler)


def log_buy_fill(ticker: str, shares: int, price: float, strategy_name: str,
                 trigger: str = "", sizing_info: str = ""):
    """Log a buy fill."""
    details = f"strategy={strategy_name}"
    if trigger:
        details += f" trigger={trigger}"
    if sizing_info:
        details += f" {sizing_info}"

    trade_logger.info(f"BUY] {ticker}: {shares} shares @ ${price:.4f} | {details}")


def log_sell_fill(ticker: str, shares: int, price: float, strategy_name: str,
                  entry_price: float = None, pnl: float = None, pnl_pct: float = None,
                  exit_reason: str = ""):
    """Log a sell fill."""
    details = f"strategy={strategy_name}"

    if entry_price is not None:
        details += f" entry=${entry_price:.4f}"

    if pnl is not None and pnl_pct is not None:
        details += f" P&L=${pnl:+.2f} ({pnl_pct:+.2f}%)"

    if exit_reason:
        details += f" reason={exit_reason}"

    trade_logger.info(f"SELL] {ticker}: {shares} shares @ ${price:.4f} | {details}")


def log_order_submission(ticker: str, side: str, shares: int, order_type: str,
                        strategy_name: str, limit_price: float = None):
    """Log order submission."""
    price_str = f"${limit_price:.4f}" if limit_price else "market"
    trade_logger.info(f"{side.upper()}_ORDER] {ticker}: {shares} shares @ {price_str} ({order_type}) | strategy={strategy_name}")

