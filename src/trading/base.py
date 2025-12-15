"""Abstract base class for trading clients."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class Position:
    """Represents an open position."""
    ticker: str
    shares: int
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float


@dataclass
class Order:
    """Represents an order."""
    order_id: str
    ticker: str
    side: str  # "buy" or "sell"
    shares: int
    order_type: str  # "market", "limit", etc.
    status: str  # "new", "filled", "canceled", etc.
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None


@dataclass
class Quote:
    """Represents a price quote."""
    ticker: str
    bid: float
    ask: float
    last: float
    volume: int
    timestamp: datetime


class TradingClient(ABC):
    """Abstract base class for trading clients."""

    @abstractmethod
    def buy(self, ticker: str, shares: int) -> Order:
        """
        Submit a market buy order.

        Args:
            ticker: Stock ticker symbol
            shares: Number of shares to buy

        Returns:
            Order object with order details
        """
        pass

    @abstractmethod
    def sell(self, ticker: str, shares: int) -> Order:
        """
        Submit a market sell order.

        Args:
            ticker: Stock ticker symbol
            shares: Number of shares to sell

        Returns:
            Order object with order details
        """
        pass

    @abstractmethod
    def get_position(self, ticker: str) -> Optional[Position]:
        """
        Get current position for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Position if exists, None otherwise
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """
        Get all open positions.

        Returns:
            List of Position objects
        """
        pass

    @abstractmethod
    def get_open_orders(self) -> List[Order]:
        """
        Get all open/pending orders.

        Returns:
            List of Order objects
        """
        pass

    @abstractmethod
    def get_quote(self, ticker: str) -> Quote:
        """
        Get current quote for a ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Quote object with bid/ask/last
        """
        pass

    @abstractmethod
    def cancel_all_orders(self, ticker: Optional[str] = None) -> int:
        """
        Cancel all open orders, optionally filtered by ticker.

        Args:
            ticker: If provided, only cancel orders for this ticker

        Returns:
            Number of orders canceled
        """
        pass

    @abstractmethod
    def get_account_info(self) -> dict:
        """
        Get account information.

        Returns:
            Dict with equity, cash, buying_power, etc.
        """
        pass

    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """Whether this client is using paper trading."""
        pass

    @property
    def name(self) -> str:
        """Human-readable name of the trading client."""
        return self.__class__.__name__
