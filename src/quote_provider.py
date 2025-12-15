"""Real-time quote provider using InsightSentry WebSocket."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Set

import aiohttp
import requests

logger = logging.getLogger(__name__)

# Cache file for WS key (survives restarts)
WS_KEY_CACHE_FILE = Path(__file__).parent.parent / "data" / ".ws_key_cache.json"


class InsightSentryQuoteProvider:
    """
    Real-time quote provider using InsightSentry WebSocket API.

    Provides second-level bar data for subscribed tickers.
    """

    WS_URL = "wss://realtime.insightsentry.com/live"
    KEY_URL = "https://api.insightsentry.com/v2/websocket-key"

    def __init__(
        self,
        api_key: Optional[str] = None,
        on_quote: Optional[Callable[[str, float, int, datetime], None]] = None,
        on_bar: Optional[Callable[[str, datetime, float, float, float, float, int], None]] = None,
    ):
        """
        Initialize the quote provider.

        Args:
            api_key: InsightSentry API key (Bearer token)
            on_quote: Callback for quote updates (ticker, price, volume, timestamp)
            on_bar: Callback for full bar data (ticker, timestamp, open, high, low, close, volume)
        """
        self.api_key = api_key or os.getenv("INSIGHT_SENTRY_KEY")
        self.on_quote = on_quote
        self.on_bar = on_bar

        self._ws_key: Optional[str] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._subscriptions: Set[str] = set()
        self._running = False
        self._reconnect_delay = 1.0
        self._last_heartbeat = 0.0

    def _load_cached_key(self) -> Optional[str]:
        """Load cached WS key if still valid."""
        try:
            if WS_KEY_CACHE_FILE.exists():
                data = json.loads(WS_KEY_CACHE_FILE.read_text())
                expires = data.get("expires", 0)
                # Key valid if expiration is more than 5 minutes away
                if expires > time.time() + 300:
                    logger.info(f"Using cached WS key (expires in {int((expires - time.time()) / 60)} min)")
                    return data.get("key")
        except Exception as e:
            logger.debug(f"Failed to load cached key: {e}")
        return None

    def _save_key_to_cache(self, key: str, expires: int):
        """Save WS key to cache file."""
        try:
            WS_KEY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            WS_KEY_CACHE_FILE.write_text(json.dumps({
                "key": key,
                "expires": expires,
                "saved_at": time.time(),
            }))
            logger.debug("Saved WS key to cache")
        except Exception as e:
            logger.warning(f"Failed to cache WS key: {e}")

    def get_ws_key(self) -> str:
        """Get WebSocket key - for native subscribers, the API key IS the WS key."""
        if not self.api_key:
            raise ValueError("INSIGHT_SENTRY_KEY not set")

        # For native InsightSentry subscribers, the API key works directly as the WS key
        self._ws_key = self.api_key
        logger.info("Using INSIGHT_SENTRY_KEY as WebSocket key (native subscriber)")
        return self._ws_key

    async def _cleanup_existing_connections(self):
        """Close any existing WebSocket/session before creating new ones."""
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
                logger.info("Closed existing WebSocket connection")
            except Exception as e:
                logger.warning(f"Error closing existing WebSocket: {e}")
            self._ws = None

        if self._session and not self._session.closed:
            try:
                await self._session.close()
                logger.info("Closed existing HTTP session")
            except Exception as e:
                logger.warning(f"Error closing existing session: {e}")
            self._session = None

    async def connect(self):
        """Connect to WebSocket and start receiving data."""
        # Clean up any existing connections first
        await self._cleanup_existing_connections()

        if not self._ws_key:
            self.get_ws_key()

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

            # Send initial subscriptions only if we have any
            # (Empty array is rejected by InsightSentry with "Subscriptions field or value is invalid")
            if self._subscriptions:
                await self._send_subscriptions()
            else:
                logger.info("No subscriptions to send on connect - server starts with clean state")

            # Start heartbeat task
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                logger.info("Starting WebSocket message loop...")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error(f"WebSocket error: {ws.exception()}")
                        break
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.warning(f"WebSocket closed by server (close_code={ws.close_code})")
                        break
                    elif msg.type == aiohttp.WSMsgType.PING:
                        logger.debug("Received ping from server")
                    elif msg.type == aiohttp.WSMsgType.PONG:
                        logger.debug("Received pong from server")
                logger.warning(f"WebSocket message loop ended (running={self._running})")
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
                    self._last_heartbeat = time.time()
                    logger.debug("Sent ping")
                except Exception as e:
                    logger.warning(f"Ping failed: {e}")
            else:
                logger.debug("Heartbeat skipped - no WebSocket connection")

    async def _send_subscriptions(self):
        """Send current subscriptions to server."""
        if not self._ws or self._ws.closed:
            logger.debug("Cannot send subscriptions - WebSocket not connected")
            return

        # InsightSentry rejects empty subscription arrays with "Subscriptions field or value is invalid"
        # When we have no subscriptions, we need to force a reconnect to clear server state
        if not self._subscriptions:
            logger.debug("No subscriptions to send - triggering reconnect to clear server state")
            await self._force_reconnect()
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

        # Log subscription (mask API key)
        log_msg = {**message, "api_key": message["api_key"][:8] + "..."}
        logger.debug(f"Sending subscription: {log_msg}")
        await self._ws.send_json(message)
        logger.info(f"Subscribed to {len(subs)} tickers: {list(self._subscriptions)}")

    async def _force_reconnect(self):
        """Force a WebSocket reconnect to clear server-side subscription state."""
        if self._ws and not self._ws.closed:
            logger.debug("Forcing WebSocket close to clear subscriptions")
            await self._ws.close()
            # The connect() loop will automatically reconnect
            # On reconnect, if _subscriptions is still empty, we won't send anything
            # and the server will have no subscriptions for this connection

    async def _handle_message(self, data: str):
        """Handle incoming WebSocket message."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse message: {data[:100]}")
            return

        # Log non-heartbeat messages (DEBUG to avoid stdout noise)
        if "server_time" not in msg:
            logger.debug(f"WS message: {str(msg)[:300]}")

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

            # Extract full OHLCV
            open_price = bar.get("open", 0)
            high = bar.get("high", 0)
            low = bar.get("low", 0)
            close = bar.get("close", 0)
            volume = int(bar.get("volume", 0))

            # Use close price as the current price for quote callback
            if self.on_quote and close > 0:
                self.on_quote(ticker, close, volume, timestamp)

            # Full bar callback for storage
            if self.on_bar and close > 0:
                self.on_bar(ticker, timestamp, open_price, high, low, close, volume)

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
            logger.debug(f"Already subscribed to {ticker}")
            # Still send subscription in case WS reconnected
            if self._ws and not self._ws.closed:
                await self._send_subscriptions()
            return

        self._subscriptions.add(ticker)
        logger.info(f"Subscribing to {ticker}")

        if self._ws and not self._ws.closed:
            logger.info(f"WebSocket connected, sending subscription for {ticker}")
            await self._send_subscriptions()
        else:
            logger.warning(f"WebSocket not connected, subscription for {ticker} queued")

    async def unsubscribe(self, ticker: str):
        """Unsubscribe from a ticker."""
        ticker = ticker.upper()
        if ticker not in self._subscriptions:
            logger.debug(f"Unsubscribe called for {ticker} but not in subscriptions: {self._subscriptions}")
            return

        self._subscriptions.discard(ticker)
        logger.debug(f"Unsubscribed from {ticker}, remaining: {self._subscriptions}")

        if self._ws and not self._ws.closed:
            await self._send_subscriptions()

    def subscribe_sync(self, ticker: str):
        """Sync wrapper for subscribe (for use from non-async code)."""
        ticker = ticker.upper()
        self._subscriptions.add(ticker)
        logger.debug(f"Queued subscription for {ticker}")

    def unsubscribe_sync(self, ticker: str):
        """Sync wrapper for unsubscribe (for use from non-async code)."""
        ticker = ticker.upper()
        self._subscriptions.discard(ticker)
        logger.debug(f"Queued unsubscription for {ticker}")

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
    def connection_status(self) -> dict:
        """Get detailed connection status."""
        return {
            "connected": self.is_connected,
            "running": self._running,
            "last_heartbeat": self._last_heartbeat,
            "seconds_since_heartbeat": time.time() - self._last_heartbeat if self._last_heartbeat else None,
            "subscriptions": len(self._subscriptions),
        }

    @property
    def subscribed_tickers(self) -> Set[str]:
        """Get set of currently subscribed tickers."""
        return self._subscriptions.copy()
