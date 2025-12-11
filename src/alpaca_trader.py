"""
Alpaca trading client for executing bracket orders.

Usage:
    from src.alpaca_trader import AlpacaTrader

    trader = AlpacaTrader()  # Uses env vars for API keys
    trader.buy_with_bracket("AAPL", dollars=100, take_profit_pct=10, stop_loss_pct=7)
"""

import os
from typing import Optional
from decimal import Decimal, ROUND_DOWN

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest


class AlpacaTrader:
    """Client for executing trades via Alpaca API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
    ):
        """
        Initialize the Alpaca trading client.

        Args:
            api_key: Alpaca API key (defaults to ALPACA_API_KEY env var)
            secret_key: Alpaca secret key (defaults to ALPACA_SECRET_KEY env var)
            paper: If True, use paper trading (default). Set False for live trading.
        """
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca API credentials not found. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "environment variables or pass them to the constructor."
            )

        self.paper = paper
        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=paper,
        )
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
        )

    def get_account(self) -> dict:
        """Get account information."""
        account = self.trading_client.get_account()
        return {
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "equity": float(account.equity),
            "portfolio_value": float(account.portfolio_value),
            "status": account.status,
        }

    def get_quote(self, ticker: str) -> dict:
        """Get the latest quote for a ticker."""
        request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote = quotes[ticker]
        return {
            "bid": float(quote.bid_price),
            "ask": float(quote.ask_price),
            "bid_size": quote.bid_size,
            "ask_size": quote.ask_size,
            "mid": (float(quote.bid_price) + float(quote.ask_price)) / 2,
        }

    def buy_with_bracket(
        self,
        ticker: str,
        dollars: float = 100.0,
        shares: Optional[int] = None,
        take_profit_pct: float = 10.0,
        stop_loss_pct: float = 7.0,
    ) -> dict:
        """
        Buy a stock with automatic take-profit and stop-loss orders.

        Args:
            ticker: Stock ticker symbol
            dollars: Amount to invest in dollars (ignored if shares specified)
            shares: Number of shares to buy (overrides dollars)
            take_profit_pct: Take profit percentage (e.g., 10 = sell at +10%)
            stop_loss_pct: Stop loss percentage (e.g., 7 = sell at -7%)

        Returns:
            Order details including order ID and status
        """
        # Get current price to calculate shares and bracket prices
        quote = self.get_quote(ticker)
        current_price = quote["ask"]  # Use ask for buying

        if current_price <= 0:
            raise ValueError(f"Invalid price for {ticker}: {current_price}")

        # Calculate number of shares
        if shares is None:
            shares = int(dollars / current_price)

        if shares <= 0:
            raise ValueError(
                f"Cannot buy {shares} shares. Price ${current_price:.2f} "
                f"is too high for ${dollars:.2f}"
            )

        # Calculate bracket prices
        take_profit_price = round(current_price * (1 + take_profit_pct / 100), 2)
        stop_loss_price = round(current_price * (1 - stop_loss_pct / 100), 2)

        print(f"Placing bracket order for {ticker}:")
        print(f"  Shares: {shares}")
        print(f"  Est. entry: ${current_price:.2f}")
        print(f"  Take profit: ${take_profit_price:.2f} (+{take_profit_pct}%)")
        print(f"  Stop loss: ${stop_loss_price:.2f} (-{stop_loss_pct}%)")
        print(f"  Total cost: ~${shares * current_price:.2f}")

        # Create bracket order
        order_request = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,  # Good til cancelled
            order_class=OrderClass.BRACKET,
            take_profit={"limit_price": take_profit_price},
            stop_loss={"stop_price": stop_loss_price},
        )

        order = self.trading_client.submit_order(order_request)

        return {
            "order_id": str(order.id),
            "status": order.status.value,
            "ticker": ticker,
            "shares": shares,
            "side": "buy",
            "estimated_entry": current_price,
            "take_profit": take_profit_price,
            "stop_loss": stop_loss_price,
            "order_class": "bracket",
        }

    def get_positions(self) -> list:
        """Get all open positions."""
        positions = self.trading_client.get_all_positions()
        return [
            {
                "ticker": p.symbol,
                "shares": int(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_pl_pct": float(p.unrealized_plpc) * 100,
            }
            for p in positions
        ]

    def get_open_orders(self) -> list:
        """Get all open orders."""
        request = GetOrdersRequest(status="open")
        orders = self.trading_client.get_orders(request)
        return [
            {
                "order_id": str(o.id),
                "ticker": o.symbol,
                "side": o.side.value,
                "qty": int(o.qty) if o.qty else None,
                "type": o.type.value,
                "status": o.status.value,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "stop_price": float(o.stop_price) if o.stop_price else None,
            }
            for o in orders
        ]

    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns number of orders cancelled."""
        cancelled = self.trading_client.cancel_orders()
        return len(cancelled)

    def close_position(self, ticker: str) -> dict:
        """Close a position by selling all shares."""
        order = self.trading_client.close_position(ticker)
        return {
            "order_id": str(order.id),
            "ticker": order.symbol,
            "status": order.status.value,
        }

    def close_all_positions(self) -> list:
        """Close all positions."""
        closed = self.trading_client.close_all_positions()
        return [{"ticker": c.symbol, "status": "closing"} for c in closed]


def create_trader(paper: bool = True) -> AlpacaTrader:
    """Factory function to create an AlpacaTrader instance."""
    return AlpacaTrader(paper=paper)
