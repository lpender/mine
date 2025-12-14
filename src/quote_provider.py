"""Real-time quote provider using InsightSentry WebSocket."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Callable, Dict, Optional, Set

import aiohttp
import requests

logger = logging.getLogger(__name__)


class InsightSentryQuoteProvider:
    """
    Real-time quote provider using InsightSentry WebSocket API.

    Provides second-level bar data for subscribed tickers.
    """

    WS_URL = "wss://realtime.insightsentry.com/live"
    KEY_URL = "https://insightsentry.p.rapidapi.com/v2/websocket-key"

    def __init__(
        self,
        rapidapi_key: Optional[str] = None,
        on_quote: Optional[Callable[[str, float, int, datetime], None]] = None,
    ):
        """
        Initialize the quote provider.

        Args:
            rapidapi_key: RapidAPI key for InsightSentry
            on_quote: Callback for quote updates (ticker, price, volume, timestamp)
        """
        self.rapidapi_key = rapidapi_key or os.getenv("RAPIDAPI_KEY")
        self.on_quote = on_quote

        self._ws_key: Optional[str] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._subscriptions: Set[str] = set()
        self._running = False
        self._reconnect_delay = 1.0
        self._last_heartbeat = 0.0

    async def get_ws_key(self) -> str:
        """Get WebSocket key from InsightSentry API."""
        if not self.rapidapi_key:
            raise ValueError("RAPIDAPI_KEY not set")

        # Use sync request for simplicity (only called once)
        response = requests.get(
            self.KEY_URL,
            headers={
                "x-rapidapi-host": "insightsentry.p.rapidapi.com",
                "x-rapidapi-key": self.rapidapi_key,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        # Response format: {"key": "xxx", "expires": "xxx"}
        self._ws_key = data.get("key")
        if not self._ws_key:
            raise ValueError(f"Failed to get WebSocket key: {data}")

        logger.info(f"Got WebSocket key (expires: {data.get('expires', 'unknown')})")
        return self._ws_key

    async def connect(self):
        """Connect to WebSocket and start receiving data."""
        if not self._ws_key:
            await self.get_ws_key()

        self._session = aiohttp.ClientSession()
        self._running = True

        while self._running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if self._running:
                    logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _connect_and_run(self):
        """Connect and run the WebSocket message loop."""
        logger.info(f"Connecting to {self.WS_URL}")

        async with self._session.ws_connect(
            self.WS_URL,
            heartbeat=30,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # Reset on successful connect
            logger.info("WebSocket connected")

            # Send initial authentication and subscriptions
            await self._send_subscriptions()

            # Start heartbeat task
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"WebSocket error: {ws.exception()}")
                        break
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.info("WebSocket closed")
                        break
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_loop(self):
        """Send periodic pings to keep connection alive."""
        while self._running:
            await asyncio.sleep(25)
            if self._ws and not self._ws.closed:
                try:
                    await self._ws.ping()
                    logger.debug("Sent ping")
                except Exception as e:
                    logger.warning(f"Ping failed: {e}")

    async def _send_subscriptions(self):
        """Send current subscriptions to server."""
        if not self._ws or self._ws.closed:
            return

        if not self._subscriptions:
            logger.debug("No subscriptions to send")
            return

        # Build subscription message
        # InsightSentry expects: {"api_key": "xxx", "subscriptions": [...]}
        subs = []
        for ticker in self._subscriptions:
            # Convert ticker to InsightSentry format (e.g., "NASDAQ:AAPL")
            # For now, assume all are NASDAQ - could add exchange detection
            code = f"NASDAQ:{ticker}"
            subs.append({
                "code": code,
                "type": "series",
                "bar_type": "second",
                "bar_interval": 1,
                "extended": True,
                "recent_bars": False,
            })

        message = {
            "api_key": self._ws_key,
            "subscriptions": subs,
        }

        await self._ws.send_json(message)
        logger.info(f"Sent subscriptions for {len(subs)} tickers: {list(self._subscriptions)}")

    async def _handle_message(self, data: str):
        """Handle incoming WebSocket message."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {data[:100]}")
            return

        # Server heartbeat
        if "server_time" in msg:
            self._last_heartbeat = time.time()
            return

        # Error message
        if "message" in msg and "error" in msg.get("message", "").lower():
            logger.error(f"Server error: {msg}")
            return

        # Series data (OHLCV bars)
        if "code" in msg and "series" in msg:
            await self._handle_series(msg)
            return

        # Quote data
        if "data" in msg:
            for quote in msg["data"]:
                if "code" in quote:
                    await self._handle_quote(quote)
            return

        logger.debug(f"Unknown message: {msg}")

    async def _handle_series(self, msg: dict):
        """Handle series (OHLCV) data."""
        code = msg.get("code", "")
        # Extract ticker from code (e.g., "NASDAQ:AAPL" -> "AAPL")
        ticker = code.split(":")[-1] if ":" in code else code

        series = msg.get("series", [])
        for bar in series:
            ts = bar.get("time", 0)
            timestamp = datetime.fromtimestamp(ts) if ts else datetime.now()

            # Use close price as the current price
            price = bar.get("close", 0)
            volume = int(bar.get("volume", 0))

            if self.on_quote and price > 0:
                self.on_quote(ticker, price, volume, timestamp)

    async def _handle_quote(self, quote: dict):
        """Handle quote data."""
        code = quote.get("code", "")
        ticker = code.split(":")[-1] if ":" in code else code

        price = quote.get("last_price", 0)
        volume = int(quote.get("volume", 0))
        timestamp = datetime.now()

        if self.on_quote and price > 0:
            self.on_quote(ticker, price, volume, timestamp)

    async def subscribe(self, ticker: str):
        """Subscribe to real-time data for a ticker."""
        ticker = ticker.upper()
        if ticker in self._subscriptions:
            return

        self._subscriptions.add(ticker)
        logger.info(f"Subscribing to {ticker}")

        if self._ws and not self._ws.closed:
            await self._send_subscriptions()

    async def unsubscribe(self, ticker: str):
        """Unsubscribe from a ticker."""
        ticker = ticker.upper()
        if ticker not in self._subscriptions:
            return

        self._subscriptions.discard(ticker)
        logger.info(f"Unsubscribing from {ticker}")

        if self._ws and not self._ws.closed:
            await self._send_subscriptions()

    def subscribe_sync(self, ticker: str):
        """Sync wrapper for subscribe (for use from non-async code)."""
        ticker = ticker.upper()
        self._subscriptions.add(ticker)
        logger.info(f"Queued subscription for {ticker}")

    def unsubscribe_sync(self, ticker: str):
        """Sync wrapper for unsubscribe (for use from non-async code)."""
        ticker = ticker.upper()
        self._subscriptions.discard(ticker)
        logger.info(f"Queued unsubscription for {ticker}")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("Disconnected from WebSocket")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws is not None and not self._ws.closed

    @property
    def subscribed_tickers(self) -> Set[str]:
        """Get set of currently subscribed tickers."""
        return self._subscriptions.copy()
