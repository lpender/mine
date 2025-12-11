"""
Tradier trading client for executing bracket orders with extended hours support.

Usage:
    from src.tradier_trader import TradierTrader

    trader = TradierTrader()  # Uses TRADIER_API_KEY env var
    trader.buy_with_bracket("AAPL", dollars=100, take_profit_pct=10, stop_loss_pct=7)

Requires TRADIER_API_KEY and TRADIER_ACCOUNT_ID environment variables.
Set TRADIER_SANDBOX=true for paper trading (default).
"""

import os
import requests
from typing import Optional
from zoneinfo import ZoneInfo


class TradierTrader:
    """Client for executing trades via Tradier API."""

    LIVE_BASE_URL = "https://api.tradier.com/v1"
    SANDBOX_BASE_URL = "https://sandbox.tradier.com/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        paper: bool = True,
    ):
        """
        Initialize the Tradier trading client.

        Args:
            api_key: Tradier API key (defaults to TRADIER_API_KEY env var)
            account_id: Tradier account ID (defaults to TRADIER_ACCOUNT_ID env var)
            paper: If True, use sandbox/paper trading (default)
        """
        self.api_key = api_key or os.getenv("TRADIER_API_KEY")
        self.account_id = account_id or os.getenv("TRADIER_ACCOUNT_ID")
        self.paper = paper
        self.et_tz = ZoneInfo("America/New_York")

        if not self.api_key:
            raise ValueError(
                "Tradier API key required. Set TRADIER_API_KEY env var or pass api_key parameter."
            )

        # Use sandbox for paper trading
        sandbox_env = os.getenv("TRADIER_SANDBOX", "true").lower()
        use_sandbox = paper or sandbox_env == "true"
        self.base_url = self.SANDBOX_BASE_URL if use_sandbox else self.LIVE_BASE_URL

        self._connected = False
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an API request."""
        url = f"{self.base_url}{endpoint}"
        resp = self._session.request(method, url, **kwargs)

        if resp.status_code >= 400:
            error_msg = f"Tradier API error {resp.status_code}: {resp.text}"
            raise RuntimeError(error_msg)

        return resp.json()

    def connect(self) -> bool:
        """Verify connection by fetching profile. Auto-discovers account_id if not set."""
        try:
            data = self._request("GET", "/user/profile")
            profile = data.get("profile", {})

            # Auto-discover account_id if not set
            if not self.account_id:
                account = profile.get("account")
                if isinstance(account, dict):
                    self.account_id = account.get("account_number")
                elif isinstance(account, list) and len(account) > 0:
                    # Use first account if multiple
                    self.account_id = account[0].get("account_number")

            if not self.account_id:
                raise ValueError(
                    "Could not determine account ID. Set TRADIER_ACCOUNT_ID env var."
                )

            self._connected = True
            return True
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Tradier API: {e}")

    def disconnect(self):
        """Close the session."""
        self._session.close()
        self._connected = False

    def _ensure_connected(self):
        """Ensure we're connected before making API calls."""
        if not self._connected:
            self.connect()

    def get_account(self) -> dict:
        """Get account information."""
        self._ensure_connected()

        data = self._request("GET", f"/accounts/{self.account_id}/balances")
        balances = data.get("balances", {})

        return {
            "equity": balances.get("equity", 0),
            "buying_power": balances.get("buying_power", 0),
            "cash": balances.get("cash", {}).get("cash_available", 0)
            if isinstance(balances.get("cash"), dict)
            else balances.get("total_cash", 0),
            "status": "active",
        }

    def get_quote(self, ticker: str) -> dict:
        """Get the latest quote for a ticker."""
        self._ensure_connected()

        data = self._request("GET", "/markets/quotes", params={"symbols": ticker})
        quotes = data.get("quotes", {})
        quote = quotes.get("quote", {})

        # Handle case where quote is a list
        if isinstance(quote, list):
            quote = quote[0] if quote else {}

        bid = quote.get("bid", 0) or 0
        ask = quote.get("ask", 0) or 0
        last = quote.get("last", 0) or 0

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
            "bid_size": quote.get("bidsize", 0) or 0,
            "ask_size": quote.get("asksize", 0) or 0,
            "mid": (bid + ask) / 2 if bid > 0 and ask > 0 else last,
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
        Supports extended hours trading.

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

        print(f"Placing bracket order for {ticker}:")
        print(f"  Shares: {shares}")
        print(f"  Est. entry: ${current_price:.2f} (from {quote.get('source', 'quote')})")
        print(f"  Take profit: ${take_profit_price:.2f} (+{take_profit_pct}%)")
        print(f"  Stop loss: ${stop_loss_price:.2f} (-{stop_loss_pct}%)")
        print(f"  Total cost: ~${shares * current_price:.2f}")

        # Place OTOCO bracket order (One-Triggers-OCO)
        # Primary order triggers two linked exit orders (OCO = one-cancels-other)
        order_data = {
            "class": "otoco",
            "duration": "gtc",
            "symbol": ticker,
            # Primary leg: Market buy
            "side": "buy",
            "quantity": shares,
            "type": "market",
            # Take profit leg
            "side[0]": "sell",
            "quantity[0]": shares,
            "type[0]": "limit",
            "price[0]": take_profit_price,
            "duration[0]": "gtc",
            # Stop loss leg
            "side[1]": "sell",
            "quantity[1]": shares,
            "type[1]": "stop",
            "stop[1]": stop_loss_price,
            "duration[1]": "gtc",
        }

        data = self._request(
            "POST",
            f"/accounts/{self.account_id}/orders",
            data=order_data,
        )

        order = data.get("order", {})
        order_id = order.get("id")
        status = order.get("status", "unknown")

        return {
            "parent_order_id": order_id,
            "take_profit_order_id": None,  # Tradier bundles these
            "stop_loss_order_id": None,
            "status": status,
            "ticker": ticker,
            "shares": shares,
            "side": "buy",
            "estimated_entry": current_price,
            "take_profit": take_profit_price,
            "stop_loss": stop_loss_price,
            "order_class": "otoco",
        }

    def get_positions(self) -> list:
        """Get all open positions."""
        self._ensure_connected()

        data = self._request("GET", f"/accounts/{self.account_id}/positions")
        positions_data = data.get("positions", {})

        # Handle "null" positions (no positions)
        if positions_data == "null" or not positions_data:
            return []

        position_list = positions_data.get("position", [])
        # Handle single position (not a list)
        if isinstance(position_list, dict):
            position_list = [position_list]

        return [
            {
                "ticker": p.get("symbol"),
                "shares": int(p.get("quantity", 0)),
                "avg_entry": float(p.get("cost_basis", 0)) / max(int(p.get("quantity", 1)), 1),
                "market_value": float(p.get("cost_basis", 0)),
            }
            for p in position_list
            if p.get("quantity", 0) != 0
        ]

    def get_open_orders(self) -> list:
        """Get all open orders."""
        self._ensure_connected()

        data = self._request("GET", f"/accounts/{self.account_id}/orders")
        orders_data = data.get("orders", {})

        # Handle "null" orders (no orders)
        if orders_data == "null" or not orders_data:
            return []

        order_list = orders_data.get("order", [])
        # Handle single order (not a list)
        if isinstance(order_list, dict):
            order_list = [order_list]

        results = []
        for o in order_list:
            status = o.get("status", "").lower()
            # Only include open orders
            if status not in ("open", "pending", "partially_filled"):
                continue

            results.append({
                "order_id": o.get("id"),
                "ticker": o.get("symbol"),
                "side": o.get("side", "").lower(),
                "qty": int(o.get("quantity", 0)),
                "type": o.get("type", "").lower(),
                "status": status,
                "limit_price": float(o.get("price")) if o.get("price") else None,
                "stop_price": float(o.get("stop_price")) if o.get("stop_price") else None,
            })

        return results

    def cancel_order(self, order_id: int) -> dict:
        """Cancel a specific order."""
        self._ensure_connected()

        data = self._request(
            "DELETE",
            f"/accounts/{self.account_id}/orders/{order_id}",
        )

        return {
            "order_id": order_id,
            "status": data.get("order", {}).get("status", "cancelled"),
        }

    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns number of orders cancelled."""
        self._ensure_connected()

        orders = self.get_open_orders()
        cancelled = 0

        for order in orders:
            try:
                self.cancel_order(order["order_id"])
                cancelled += 1
            except Exception:
                pass

        return cancelled

    def close_position(self, ticker: str) -> dict:
        """Close a position by selling all shares."""
        self._ensure_connected()

        # Find position
        positions = self.get_positions()
        position = None
        for p in positions:
            if p["ticker"] == ticker and p["shares"] != 0:
                position = p
                break

        if not position:
            raise ValueError(f"No open position for {ticker}")

        shares = abs(position["shares"])
        action = "sell" if position["shares"] > 0 else "buy"

        # Place market order
        order_data = {
            "class": "equity",
            "symbol": ticker,
            "side": action,
            "quantity": shares,
            "type": "market",
            "duration": "day",
        }

        data = self._request(
            "POST",
            f"/accounts/{self.account_id}/orders",
            data=order_data,
        )

        order = data.get("order", {})
        return {
            "order_id": order.get("id"),
            "ticker": ticker,
            "status": order.get("status", "unknown"),
        }

    def close_all_positions(self) -> list:
        """Close all positions."""
        self._ensure_connected()

        results = []
        positions = self.get_positions()

        for p in positions:
            if p["shares"] != 0:
                try:
                    result = self.close_position(p["ticker"])
                    results.append(result)
                except Exception as e:
                    results.append({"ticker": p["ticker"], "status": f"error: {e}"})

        return results

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()


def create_trader(paper: bool = True) -> TradierTrader:
    """Factory function to create a TradierTrader instance."""
    return TradierTrader(paper=paper)
