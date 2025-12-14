"""Trading client abstraction layer."""

import os
from typing import Optional

from .base import TradingClient, Position, Order, Quote
from .alpaca import AlpacaTradingClient


def get_trading_client(
    backend: Optional[str] = None,
    paper: bool = True,
) -> TradingClient:
    """
    Factory to get a trading client.

    Args:
        backend: Trading backend to use ("alpaca", "tradier", "ib")
                 Defaults to TRADING_BACKEND env var or "alpaca"
        paper: Whether to use paper trading (default True)

    Returns:
        TradingClient instance
    """
    backend = backend or os.getenv("TRADING_BACKEND", "alpaca")

    if backend == "alpaca":
        return AlpacaTradingClient(paper=paper)
    else:
        raise ValueError(f"Unknown trading backend: {backend}")


__all__ = [
    "TradingClient",
    "Position",
    "Order",
    "Quote",
    "AlpacaTradingClient",
    "get_trading_client",
]
