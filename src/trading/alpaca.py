"""Alpaca trading client implementation."""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo

import requests

from .base import TradingClient, Position, Order, Quote

logger = logging.getLogger("src.trading.alpaca")
ET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

# Slippage for limit orders
# Buy limit = price * (1 + slippage), Sell limit = price * (1 - slippage)
DEFAULT_BUY_SLIPPAGE_PCT = float(os.getenv("TRADE_SLIPPAGE_PCT", "1.0"))
# Sell slippage defaults to 2x buy slippage for more aggressive fills
DEFAULT_SELL_SLIPPAGE_PCT = float(os.getenv("TRADE_SELL_SLIPPAGE_PCT", str(DEFAULT_BUY_SLIPPAGE_PCT * 2)))


def _round_price(price: float) -> float:
    """
    Round price to valid Alpaca tick size.
    - Prices >= $1.00: penny increments (2 decimals)
    - Prices < $1.00: sub-penny allowed (4 decimals)
    """
    if price >= 1.0:
        return round(price, 2)
    else:
        return round(price, 4)


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

    def _request(
        self,
        method: str,
        endpoint: str,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        **kwargs,
    ) -> dict:
        """Make an API request with retry on rate limit (429)."""
        url = f"{base_url or self.base_url}{endpoint}"

        for attempt in range(max_retries):
            response = self._session.request(
                method,
                url,
                headers=self._get_headers(),
                **kwargs,
            )

            if response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = int(response.headers.get("Retry-After", 1))
                wait_time = max(retry_after, 2 ** attempt)  # Exponential backoff, min from header
                logger.warning(f"Rate limited by Alpaca (429), waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                time.sleep(wait_time)
                continue

            # For client errors (4xx), capture response body for better error messages
            if 400 <= response.status_code < 500:
                try:
                    error_body = response.json()
                    error_msg = error_body.get("message", response.text)
                    error_code = error_body.get("code", "")
                    logger.error(f"Alpaca {response.status_code} error: {error_msg} (code={error_code})")
                except Exception:
                    error_msg = response.text
                    logger.error(f"Alpaca {response.status_code} error: {error_msg}")

                # Raise with detailed message
                raise requests.HTTPError(
                    f"{response.status_code} Error: {error_msg}",
                    response=response,
                )

            response.raise_for_status()
            return response.json() if response.text else {}

        # All retries exhausted
        logger.error(f"Alpaca rate limit: all {max_retries} retries exhausted for {method} {endpoint}")
        response.raise_for_status()  # Will raise the 429 error
        return {}

    @property
    def is_paper(self) -> bool:
        return self._paper

    def buy(self, ticker: str, shares: int, limit_price: Optional[float] = None) -> Order:
        """Submit a buy order (limit order with extended hours support)."""
        if limit_price is None:
            raise ValueError("limit_price is required for buy orders")

        # Apply slippage - willing to pay up to X% more
        # Round to valid tick size (2 decimals >= $1, 4 decimals < $1)
        limit_with_slippage = _round_price(limit_price * (1 + DEFAULT_BUY_SLIPPAGE_PCT / 100))

        data = {
            "symbol": ticker,
            "qty": shares,
            "side": "buy",
            "type": "limit",
            "limit_price": str(limit_with_slippage),
            "time_in_force": "day",
            "extended_hours": True,
        }
        logger.info(f"[{ticker}] BUY LIMIT ${limit_with_slippage} (price=${limit_price:.4f} + {DEFAULT_BUY_SLIPPAGE_PCT}% slippage)")
        result = self._request("POST", "/v2/orders", json=data)
        return self._parse_order(result)

    def sell(self, ticker: str, shares: int, limit_price: Optional[float] = None) -> Order:
        """Submit a sell order (limit order with extended hours support)."""
        if limit_price is None:
            raise ValueError("limit_price is required for sell orders")

        # Apply slippage - willing to accept up to X% less (2x buy slippage by default)
        # Round to valid tick size (2 decimals >= $1, 4 decimals < $1)
        limit_with_slippage = _round_price(limit_price * (1 - DEFAULT_SELL_SLIPPAGE_PCT / 100))

        data = {
            "symbol": ticker,
            "qty": shares,
            "side": "sell",
            "type": "limit",
            "limit_price": str(limit_with_slippage),
            "time_in_force": "day",
            "extended_hours": True,
        }
        logger.info(f"[{ticker}] SELL LIMIT ${limit_with_slippage} (price=${limit_price:.4f} - {DEFAULT_SELL_SLIPPAGE_PCT}% slippage)")
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
            # Store as naive UTC
            ts = ts.astimezone(UTC_TZ).replace(tzinfo=None)
        else:
            ts = datetime.utcnow()

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
            # Parse created_at timestamp
            created_at = None
            if "created_at" in data:
                created_at_str = data["created_at"]
                if created_at_str:
                    if created_at_str.endswith("Z"):
                        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    else:
                        created_at = datetime.fromisoformat(created_at_str)
                    # Store as naive UTC
                    created_at = created_at.astimezone(UTC_TZ).replace(tzinfo=None)

            # Parse limit price if present
            limit_price = None
            if "limit_price" in data and data["limit_price"]:
                limit_price = float(data["limit_price"])

            orders.append(Order(
                order_id=data["id"],
                ticker=data["symbol"],
                side=data["side"],
                shares=int(data["qty"]),
                order_type=data["type"],
                status=data["status"],
                created_at=created_at,
                limit_price=limit_price,
            ))
        return orders

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order details by ID."""
        try:
            data = self._request("GET", f"/v2/orders/{order_id}")

            # Parse created_at timestamp
            created_at = None
            created_at_str = data.get("created_at")
            if created_at_str:
                if created_at_str.endswith("Z"):
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                else:
                    created_at = datetime.fromisoformat(created_at_str)
                created_at = created_at.astimezone(UTC_TZ).replace(tzinfo=None)

            limit_price = None
            if "limit_price" in data and data["limit_price"]:
                limit_price = float(data["limit_price"])

            return Order(
                order_id=data["id"],
                ticker=data["symbol"],
                side=data["side"],
                shares=int(data["qty"]),
                order_type=data["type"],
                status=data["status"],
                created_at=created_at,
                limit_price=limit_price,
            )
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order by ID.

        Checks order status first to avoid race conditions where the order
        fills between timeout detection and cancel attempt.
        """
        try:
            # Check order status first to avoid race condition
            order = self.get_order(order_id)
            if order is None:
                logger.warning(f"Order {order_id} not found - may have been canceled already")
                return False
            if order.status == "filled":
                logger.info(f"Order {order_id} already filled - skipping cancel")
                return False
            if order.status in ("canceled", "expired", "replaced"):
                logger.info(f"Order {order_id} already {order.status} - skipping cancel")
                return False

            self._request("DELETE", f"/v2/orders/{order_id}")
            logger.info(f"Canceled order {order_id}")
            return True
        except requests.HTTPError as e:
            # Handle race condition: order filled/canceled between status check and cancel
            if e.response.status_code == 422:
                logger.info(f"Order {order_id} already filled/canceled (422)")
                return False
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

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

    def is_tradeable(self, ticker: str) -> tuple[bool, str]:
        """
        Check if a ticker is tradeable on Alpaca.

        Returns:
            (is_tradeable, reason) - True if tradeable, False with reason if not
        """
        try:
            result = self._request("GET", f"/v2/assets/{ticker}")

            if not result.get("tradable", False):
                return False, "asset not tradable on Alpaca"

            if result.get("status") != "active":
                return False, f"asset status is '{result.get('status')}'"

            # Check asset class (we only trade US equities)
            asset_class = result.get("class", "")
            if asset_class not in ("us_equity",):
                return False, f"asset class '{asset_class}' not supported"

            return True, "tradeable"

        except requests.HTTPError as e:
            if e.response and e.response.status_code == 404:
                return False, "ticker not found"
            return False, f"API error: {e}"
        except Exception as e:
            return False, f"check failed: {e}"

    def _parse_order(self, data: dict) -> Order:
        """Parse API response into Order object."""
        filled_at = None
        if data.get("filled_at"):
            ts_str = data["filled_at"]
            if ts_str.endswith("Z"):
                filled_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                filled_at = datetime.fromisoformat(ts_str)
            # Store as naive UTC
            filled_at = filled_at.astimezone(UTC_TZ).replace(tzinfo=None)

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
