"""
Interactive Brokers trading client for executing bracket orders with premarket support.

Usage:
    from src.ib_trader import IBTrader

    trader = IBTrader()  # Connects to TWS/Gateway on localhost:7497
    trader.buy_with_bracket("AAPL", dollars=100, take_profit_pct=10, stop_loss_pct=7)

Requires TWS or IB Gateway running locally.
"""

import logging
import os
import random
import requests
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder, Contract

logger = logging.getLogger(__name__)


def get_yahoo_price(ticker: str) -> Optional[float]:
    """Fetch current price from Yahoo Finance as fallback."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        data = resp.json()
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return float(price) if price else None
    except Exception:
        return None


class IBTrader:
    """Client for executing trades via Interactive Brokers API."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        paper: bool = True,
        docker: bool = False,
    ):
        """
        Initialize the IB trading client.

        Args:
            host: TWS/Gateway host (default: localhost)
            port: TWS/Gateway port. If None, auto-selects based on paper/docker
            client_id: Unique client ID for this connection
            paper: If True, use paper trading port (default)
            docker: If True, use Docker IB Gateway ports (4001/4002)
        """
        self.paper = paper
        self.docker = docker
        if port is None:
            if docker:
                # Docker IB Gateway uses socat proxy: 4004->4002 (paper), 4003->4001 (live)
                port = 4004 if paper else 4003
            else:
                # Local IB Gateway typically uses 4002 (paper) / 4001 (live)
                # TWS uses 7497 (paper) / 7496 (live)
                port = 4002 if paper else 4001

        self.host = host
        self.port = port
        # Generate random client_id if not provided to avoid collisions
        self.client_id = client_id if client_id is not None else random.randint(1, 9999)
        self.et_tz = ZoneInfo("America/New_York")

        self.ib = IB()
        self._connected = False

    def connect(self) -> bool:
        """Connect to TWS/Gateway."""
        if self._connected:
            return True

        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=10)
            self._connected = True
            return True
        except Exception as e:
            raise ConnectionError(
                f"Failed to connect to IB Gateway/TWS at {self.host}:{self.port}. "
                f"Make sure TWS or IB Gateway is running. Error: {e}"
            )

    def disconnect(self):
        """Disconnect from TWS/Gateway."""
        if self._connected:
            self.ib.disconnect()
            self._connected = False

    def _ensure_connected(self):
        """Ensure we're connected before making API calls."""
        if not self._connected:
            self.connect()

    def get_account(self) -> dict:
        """Get account information."""
        self._ensure_connected()

        account_values = self.ib.accountSummary()
        result = {}
        for av in account_values:
            if av.tag == "NetLiquidation":
                result["equity"] = float(av.value)
            elif av.tag == "AvailableFunds":
                result["buying_power"] = float(av.value)
            elif av.tag == "TotalCashValue":
                result["cash"] = float(av.value)

        result["status"] = "active"
        return result

    def get_quote(self, ticker: str) -> dict:
        """Get the latest quote for a ticker."""
        self._ensure_connected()

        contract = Stock(ticker, "SMART", "USD")
        self.ib.qualifyContracts(contract)

        # Request market data (try real-time first)
        ticker_data = self.ib.reqMktData(contract, "", False, False)
        self.ib.sleep(1)  # Wait for data

        bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else 0
        ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else 0
        last = ticker_data.last if ticker_data.last and ticker_data.last > 0 else 0

        # If no data, try delayed market data (type 3)
        if bid <= 0 and ask <= 0 and last <= 0:
            self.ib.cancelMktData(contract)
            self.ib.reqMarketDataType(3)  # Request delayed data
            ticker_data = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(2)  # Wait longer for delayed data

            bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else 0
            ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else 0
            last = ticker_data.last if ticker_data.last and ticker_data.last > 0 else 0

            # Check delayed fields
            if bid <= 0:
                bid = ticker_data.delayedBid if hasattr(ticker_data, 'delayedBid') and ticker_data.delayedBid and ticker_data.delayedBid > 0 else 0
            if ask <= 0:
                ask = ticker_data.delayedAsk if hasattr(ticker_data, 'delayedAsk') and ticker_data.delayedAsk and ticker_data.delayedAsk > 0 else 0
            if last <= 0:
                last = ticker_data.delayedLast if hasattr(ticker_data, 'delayedLast') and ticker_data.delayedLast and ticker_data.delayedLast > 0 else 0

            # Reset to live data for future requests
            self.ib.reqMarketDataType(1)

        # Cancel market data subscription
        self.ib.cancelMktData(contract)

        # If still no data, try Yahoo Finance as fallback
        if bid <= 0 and ask <= 0 and last <= 0:
            yahoo_price = get_yahoo_price(ticker)
            if yahoo_price and yahoo_price > 0:
                return {
                    "bid": yahoo_price,
                    "ask": yahoo_price,
                    "bid_size": 0,
                    "ask_size": 0,
                    "mid": yahoo_price,
                    "source": "yahoo",
                }

        # Use last price if bid/ask unavailable
        if bid <= 0 and ask <= 0 and last > 0:
            return {
                "bid": last,
                "ask": last,
                "bid_size": 0,
                "ask_size": 0,
                "mid": last,
                "source": "last_trade",
            }

        if bid <= 0:
            bid = last
        if ask <= 0:
            ask = last

        return {
            "bid": bid,
            "ask": ask,
            "bid_size": ticker_data.bidSize if ticker_data.bidSize else 0,
            "ask_size": ticker_data.askSize if ticker_data.askSize else 0,
            "mid": (bid + ask) / 2 if bid > 0 and ask > 0 else last,
            "source": "delayed" if bid == last or ask == last else "quote",
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
        Supports premarket/extended hours trading.

        Args:
            ticker: Stock ticker symbol
            dollars: Amount to invest in dollars (ignored if shares specified)
            shares: Number of shares to buy (overrides dollars)
            take_profit_pct: Take profit percentage (e.g., 10 = sell at +10%)
            stop_loss_pct: Stop loss percentage (e.g., 7 = sell at -7%)

        Returns:
            Order details including order IDs and status
        """
        self._ensure_connected()

        # Get current price
        quote = self.get_quote(ticker)
        current_price = quote["ask"]

        if current_price <= 0:
            raise ValueError(f"Invalid price for {ticker}: {current_price}")

        # Calculate number of shares
        if shares is None:
            shares = int(dollars / current_price)

        if shares <= 0:
            raise ValueError(
                f"Cannot buy {shares} shares. Price ${current_price:.2f} "
                f"is too high for ${dollars:.2f}. Minimum order: ${current_price:.2f}"
            )

        # Calculate bracket prices
        take_profit_price = round(current_price * (1 + take_profit_pct / 100), 2)
        stop_loss_price = round(current_price * (1 - stop_loss_pct / 100), 2)

        logger.info(f"Placing bracket order for {ticker}: "
                    f"{shares} shares @ ~${current_price:.2f}, "
                    f"TP=${take_profit_price:.2f} (+{take_profit_pct}%), "
                    f"SL=${stop_loss_price:.2f} (-{stop_loss_pct}%), "
                    f"Total ~${shares * current_price:.2f}")

        # Create contract
        contract = Stock(ticker, "SMART", "USD")
        self.ib.qualifyContracts(contract)

        # Create bracket order manually for better control
        # Parent order: Market buy
        parent = MarketOrder("BUY", shares)
        parent.outsideRth = True  # Allow premarket/afterhours
        parent.tif = "GTC"
        parent.transmit = False  # Don't transmit until all orders ready

        # Take profit: Limit sell
        take_profit_order = LimitOrder("SELL", shares, take_profit_price)
        take_profit_order.outsideRth = True
        take_profit_order.tif = "GTC"
        take_profit_order.transmit = False

        # Stop loss: Stop sell
        stop_loss_order = StopOrder("SELL", shares, stop_loss_price)
        stop_loss_order.outsideRth = True
        stop_loss_order.tif = "GTC"
        stop_loss_order.transmit = True  # Transmit all orders when this is placed

        # Place parent order first
        parent_trade = self.ib.placeOrder(contract, parent)
        self.ib.sleep(0.5)

        # Set parent ID for child orders
        take_profit_order.parentId = parent_trade.order.orderId
        stop_loss_order.parentId = parent_trade.order.orderId

        # Place child orders
        tp_trade = self.ib.placeOrder(contract, take_profit_order)
        sl_trade = self.ib.placeOrder(contract, stop_loss_order)

        trades = [parent_trade, tp_trade, sl_trade]

        self.ib.sleep(1)  # Wait for order acknowledgment

        return {
            "parent_order_id": trades[0].order.orderId if trades else None,
            "take_profit_order_id": trades[1].order.orderId if len(trades) > 1 else None,
            "stop_loss_order_id": trades[2].order.orderId if len(trades) > 2 else None,
            "status": trades[0].orderStatus.status if trades else "unknown",
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
        self._ensure_connected()

        positions = self.ib.positions()
        return [
            {
                "ticker": p.contract.symbol,
                "shares": int(p.position),
                "avg_entry": float(p.avgCost),
                "market_value": float(p.position * p.avgCost),
            }
            for p in positions
            if p.position != 0
        ]

    def get_open_orders(self) -> list:
        """Get all open orders."""
        self._ensure_connected()

        # Request all open orders including those from other clients
        self.ib.reqAllOpenOrders()
        self.ib.sleep(0.5)
        trades = self.ib.openTrades()
        return [
            {
                "order_id": t.order.orderId,
                "ticker": t.contract.symbol,
                "side": t.order.action.lower(),
                "qty": int(t.order.totalQuantity),
                "type": t.order.orderType.lower(),
                "status": t.orderStatus.status.lower(),
                "limit_price": float(t.order.lmtPrice) if t.order.lmtPrice else None,
                "stop_price": float(t.order.auxPrice) if t.order.auxPrice else None,
            }
            for t in trades
        ]

    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns number of orders cancelled."""
        self._ensure_connected()

        orders = self.ib.openOrders()
        for order in orders:
            self.ib.cancelOrder(order)

        return len(orders)

    def close_position(self, ticker: str) -> dict:
        """Close a position by selling all shares."""
        self._ensure_connected()

        # Find position
        positions = self.ib.positions()
        position = None
        for p in positions:
            if p.contract.symbol == ticker and p.position != 0:
                position = p
                break

        if not position:
            raise ValueError(f"No open position for {ticker}")

        # Create market sell order
        contract = Stock(ticker, "SMART", "USD")
        self.ib.qualifyContracts(contract)

        shares = abs(int(position.position))
        action = "SELL" if position.position > 0 else "BUY"

        order = MarketOrder(action, shares)
        order.outsideRth = True

        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)

        return {
            "order_id": trade.order.orderId,
            "ticker": ticker,
            "status": trade.orderStatus.status,
        }

    def close_all_positions(self) -> list:
        """Close all positions."""
        self._ensure_connected()

        results = []
        positions = self.ib.positions()

        for p in positions:
            if p.position != 0:
                try:
                    result = self.close_position(p.contract.symbol)
                    results.append(result)
                except Exception as e:
                    results.append({"ticker": p.contract.symbol, "status": f"error: {e}"})

        return results

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()


def create_trader(paper: bool = True, docker: bool = False) -> IBTrader:
    """Factory function to create an IBTrader instance."""
    return IBTrader(paper=paper, docker=docker)
