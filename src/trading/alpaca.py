"""Alpaca trading client implementation."""

import os
from datetime import datetime, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo

import requests

from .base import TradingClient, Position, Order, Quote

ET_TZ = ZoneInfo("America/New_York")


class AlpacaTradingClient(TradingClient):
    """Trading client using Alpaca Markets API."""

    PAPER_URL = "https://paper-api.alpaca.markets"
    LIVE_URL = "https://api.alpaca.markets"
    DATA_URL = "https://data.alpaca.markets"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self._paper = paper
        self.base_url = self.PAPER_URL if paper else self.LIVE_URL
        self._session = requests.Session()

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca API credentials not set. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env"
            )

    def _get_headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _request(self, method: str, endpoint: str, base_url: Optional[str] = None, **kwargs) -> dict:
        """Make an API request."""
        url = f"{base_url or self.base_url}{endpoint}"
        response = self._session.request(
            method,
            url,
            headers=self._get_headers(),
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.text else {}

    @property
    def is_paper(self) -> bool:
        return self._paper

    def buy(self, ticker: str, shares: int) -> Order:
        """Submit a market buy order."""
        data = {
            "symbol": ticker,
            "qty": shares,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }
        result = self._request("POST", "/v2/orders", json=data)
        return self._parse_order(result)

    def sell(self, ticker: str, shares: int) -> Order:
        """Submit a market sell order."""
        data = {
            "symbol": ticker,
            "qty": shares,
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
        }
        result = self._request("POST", "/v2/orders", json=data)
        return self._parse_order(result)

    def get_position(self, ticker: str) -> Optional[Position]:
        """Get current position for a ticker."""
        try:
            result = self._request("GET", f"/v2/positions/{ticker}")
            return self._parse_position(result)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        results = self._request("GET", "/v2/positions")
        return [self._parse_position(p) for p in results]

    def get_quote(self, ticker: str) -> Quote:
        """Get current quote for a ticker."""
        result = self._request(
            "GET",
            f"/v2/stocks/{ticker}/quotes/latest",
            base_url=self.DATA_URL,
        )
        quote_data = result.get("quote", {})

        # Parse timestamp
        ts_str = quote_data.get("t", "")
        if ts_str:
            if ts_str.endswith("Z"):
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                ts = datetime.fromisoformat(ts_str)
            ts = ts.astimezone(ET_TZ).replace(tzinfo=None)
        else:
            ts = datetime.now()

        return Quote(
            ticker=ticker,
            bid=float(quote_data.get("bp", 0)),
            ask=float(quote_data.get("ap", 0)),
            last=float(quote_data.get("bp", 0)),  # Use bid as proxy for last
            volume=int(quote_data.get("s", 0)),  # bid size
            timestamp=ts,
        )

    def get_open_orders(self) -> List[Order]:
        """Get all open orders."""
        results = self._request("GET", "/v2/orders", params={"status": "open"})
        orders = []
        for data in results:
            orders.append(Order(
                order_id=data["id"],
                ticker=data["symbol"],
                side=data["side"],
                shares=int(data["qty"]),
                order_type=data["type"],
                status=data["status"],
            ))
        return orders

    def cancel_all_orders(self, ticker: Optional[str] = None) -> int:
        """Cancel all open orders."""
        if ticker:
            # Get orders for this ticker and cancel them
            orders = self._request("GET", "/v2/orders", params={"symbols": ticker})
            count = 0
            for order in orders:
                try:
                    self._request("DELETE", f"/v2/orders/{order['id']}")
                    count += 1
                except requests.HTTPError:
                    pass
            return count
        else:
            # Cancel all orders
            result = self._request("DELETE", "/v2/orders")
            return len(result) if isinstance(result, list) else 0

    def get_account_info(self) -> dict:
        """Get account information."""
        result = self._request("GET", "/v2/account")
        return {
            "equity": float(result.get("equity", 0)),
            "cash": float(result.get("cash", 0)),
            "buying_power": float(result.get("buying_power", 0)),
            "portfolio_value": float(result.get("portfolio_value", 0)),
            "status": result.get("status", ""),
            "trading_blocked": result.get("trading_blocked", False),
            "pattern_day_trader": result.get("pattern_day_trader", False),
        }

    def _parse_order(self, data: dict) -> Order:
        """Parse API response into Order object."""
        filled_at = None
        if data.get("filled_at"):
            ts_str = data["filled_at"]
            if ts_str.endswith("Z"):
                filled_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                filled_at = datetime.fromisoformat(ts_str)
            filled_at = filled_at.astimezone(ET_TZ).replace(tzinfo=None)

        return Order(
            order_id=data.get("id", ""),
            ticker=data.get("symbol", ""),
            side=data.get("side", ""),
            shares=int(data.get("qty", 0)),
            order_type=data.get("type", ""),
            status=data.get("status", ""),
            filled_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
            filled_at=filled_at,
        )

    def _parse_position(self, data: dict) -> Position:
        """Parse API response into Position object."""
        return Position(
            ticker=data.get("symbol", ""),
            shares=int(data.get("qty", 0)),
            avg_entry_price=float(data.get("avg_entry_price", 0)),
            market_value=float(data.get("market_value", 0)),
            unrealized_pl=float(data.get("unrealized_pl", 0)),
            unrealized_pl_pct=float(data.get("unrealized_plpc", 0)) * 100,
        )
