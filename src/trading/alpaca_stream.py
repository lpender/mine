"""Alpaca WebSocket streaming for trade updates."""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import websockets

logger = logging.getLogger("src.trading.alpaca_stream")
ET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


class AlpacaTradeStream:
    """WebSocket client for Alpaca trade updates (order fills, cancellations, etc.)."""

    PAPER_URL = "wss://paper-api.alpaca.markets/stream"
    LIVE_URL = "wss://api.alpaca.markets/stream"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
        on_fill: Optional[Callable] = None,
        on_partial_fill: Optional[Callable] = None,
        on_canceled: Optional[Callable] = None,
        on_rejected: Optional[Callable] = None,
        on_new: Optional[Callable] = None,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.paper = paper
        self.ws_url = self.PAPER_URL if paper else self.LIVE_URL

        # Callbacks
        self.on_fill = on_fill
        self.on_partial_fill = on_partial_fill
        self.on_canceled = on_canceled
        self.on_rejected = on_rejected
        self.on_new = on_new

        # State
        self._ws = None
        self._loop = None
        self._thread = None
        self._running = False
        self._authenticated = False

    def start(self):
        """Start the WebSocket connection in a background thread."""
        if self._running:
            logger.warning("Alpaca stream already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()
        logger.info(f"Alpaca trade stream started (paper={self.paper})")

    def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Alpaca trade stream stopped")

    def _run_async_loop(self):
        """Run the async event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_listen())
        except Exception as e:
            logger.error(f"Alpaca stream error: {e}")
        finally:
            self._loop.close()

    async def _connect_and_listen(self):
        """Connect to WebSocket and listen for trade updates."""
        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    logger.info(f"Connected to Alpaca stream: {self.ws_url}")

                    # Authenticate
                    await self._authenticate(ws)

                    # Subscribe to trade updates
                    await self._subscribe(ws)

                    # Listen for messages
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Alpaca stream connection closed: {e}")
                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Alpaca stream error: {e}")
                if self._running:
                    await asyncio.sleep(5)

    async def _authenticate(self, ws):
        """Send authentication message."""
        auth_msg = {
            "action": "auth",
            "key": self.api_key,
            "secret": self.secret_key,
        }
        await ws.send(json.dumps(auth_msg))
        logger.debug("Sent auth message")

        # Wait for auth response
        response = await ws.recv()
        data = json.loads(response)
        logger.debug(f"Auth response: {data}")

        if data.get("stream") == "authorization":
            if data.get("data", {}).get("status") == "authorized":
                self._authenticated = True
                logger.info("Alpaca stream authenticated")
            else:
                raise Exception(f"Auth failed: {data}")

    async def _subscribe(self, ws):
        """Subscribe to trade updates stream."""
        sub_msg = {
            "action": "listen",
            "data": {"streams": ["trade_updates"]},
        }
        await ws.send(json.dumps(sub_msg))
        logger.debug("Subscribed to trade_updates")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            stream = data.get("stream")

            if stream == "trade_updates":
                await self._handle_trade_update(data.get("data", {}))
            elif stream == "listening":
                logger.info(f"Now listening to: {data.get('data', {}).get('streams', [])}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")

    async def _handle_trade_update(self, data: dict):
        """Handle a trade update event."""
        event = data.get("event")
        order = data.get("order", {})

        order_id = order.get("id")
        ticker = order.get("symbol")
        side = order.get("side")
        qty = int(order.get("qty", 0))
        filled_qty = int(order.get("filled_qty", 0))
        filled_avg_price = float(order.get("filled_avg_price")) if order.get("filled_avg_price") else None

        # Parse timestamp - store as naive UTC
        timestamp = None
        if order.get("filled_at"):
            ts_str = order["filled_at"]
            if ts_str.endswith("Z"):
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.fromisoformat(ts_str)
            timestamp = timestamp.astimezone(UTC_TZ).replace(tzinfo=None)

        logger.info(f"[{ticker}] Trade update: {event} - {side} {filled_qty}/{qty} @ ${filled_avg_price or 'N/A'}")

        # Dispatch to callbacks
        if event == "fill" and self.on_fill:
            # Use qty (order quantity) instead of filled_qty if they differ
            # A "fill" event means the order is complete, so qty is the true amount
            shares = filled_qty
            if filled_qty != qty:
                logger.warning(
                    f"[{ticker}] Fill event has filled_qty={filled_qty} != qty={qty}, "
                    f"using qty={qty} as the fill amount"
                )
                shares = qty
            self.on_fill(
                order_id=order_id,
                ticker=ticker,
                side=side,
                shares=shares,
                filled_price=filled_avg_price,
                timestamp=timestamp,
            )
        elif event == "partial_fill" and self.on_partial_fill:
            self.on_partial_fill(
                order_id=order_id,
                ticker=ticker,
                side=side,
                shares=filled_qty,
                filled_price=filled_avg_price,
                timestamp=timestamp,
            )
        elif event == "canceled" and self.on_canceled:
            self.on_canceled(order_id=order_id, ticker=ticker, side=side)
        elif event == "rejected" and self.on_rejected:
            reason = order.get("reject_reason", "unknown")
            self.on_rejected(order_id=order_id, ticker=ticker, side=side, reason=reason)
        elif event == "new" and self.on_new:
            self.on_new(order_id=order_id, ticker=ticker, side=side, shares=qty)
