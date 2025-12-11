"""
Alpaca trading client for executing bracket orders.

Usage:
    from src.alpaca_trader import AlpacaTrader

    trader = AlpacaTrader()  # Uses env vars for API keys
    trader.buy_with_bracket("AAPL", dollars=100, take_profit_pct=10, stop_loss_pct=7)
"""

import os
from datetime import datetime
from typing import Optional
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest


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
        self.et_tz = ZoneInfo("America/New_York")

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

    def is_market_open(self) -> tuple[bool, str]:
        """Check if the market is currently open.

        Returns:
            Tuple of (is_open, status_message)
        """
        clock = self.trading_client.get_clock()
        if clock.is_open:
            return True, "Market is open"
        else:
            next_open = clock.next_open.astimezone(self.et_tz)
            next_close = clock.next_close.astimezone(self.et_tz)
            return False, f"Market closed. Next open: {next_open.strftime('%Y-%m-%d %H:%M ET')}"

    def get_last_trade(self, ticker: str) -> dict:
        """Get the last trade for a ticker (useful when market is closed)."""
        request = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trades = self.data_client.get_stock_latest_trade(request)
        trade = trades[ticker]
        return {
            "price": float(trade.price),
            "size": int(trade.size),
            "timestamp": trade.timestamp.isoformat(),
        }

    def get_quote(self, ticker: str) -> dict:
        """Get the latest quote for a ticker.

        If bid/ask are zero (after hours), falls back to last trade price.
        """
        request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote = quotes[ticker]

        bid = float(quote.bid_price)
        ask = float(quote.ask_price)

        # If bid/ask are zero, use last trade price
        if bid == 0 or ask == 0:
            last_trade = self.get_last_trade(ticker)
            return {
                "bid": last_trade["price"],
                "ask": last_trade["price"],
                "bid_size": 0,
                "ask_size": 0,
                "mid": last_trade["price"],
                "source": "last_trade",
                "last_trade_time": last_trade["timestamp"],
            }

        return {
            "bid": bid,
            "ask": ask,
            "bid_size": quote.bid_size,
            "ask_size": quote.ask_size,
            "mid": (bid + ask) / 2,
            "source": "quote",
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
        # Check if market is open
        is_open, market_status = self.is_market_open()
        if not is_open:
            print(f"WARNING: {market_status}")
            print("Order will be queued and execute at market open.\n")

        # Get current price to calculate shares and bracket prices
        quote = self.get_quote(ticker)
        current_price = quote["ask"]  # Use ask for buying

        if current_price <= 0:
            raise ValueError(f"Invalid price for {ticker}: {current_price}")

        # Warn if using stale price data
        if quote.get("source") == "last_trade":
            print(f"NOTE: Using last trade price (no live quote available)")
            print(f"      Last trade: {quote.get('last_trade_time', 'unknown')}\n")

        # Calculate number of shares
        if shares is None:
            shares = int(dollars / current_price)

        if shares <= 0:
            min_cost = current_price
            raise ValueError(
                f"Cannot buy {shares} shares. Price ${current_price:.2f} "
                f"is too high for ${dollars:.2f}. Minimum order: ${min_cost:.2f}"
            )

        # Calculate bracket prices
        take_profit_price = round(current_price * (1 + take_profit_pct / 100), 2)
        stop_loss_price = round(current_price * (1 - stop_loss_pct / 100), 2)

        print(f"Placing bracket order for {ticker}:")
        print(f"  Shares: {shares}")
        print(f"  Est. entry: ${current_price:.2f} (from {quote.get('source', 'quote')})")
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
            extended_hours=True,  # Allow premarket/afterhours execution
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
