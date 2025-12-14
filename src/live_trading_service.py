"""Live trading service that coordinates alerts, quotes, and trading."""

import asyncio
import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Callable
from queue import Queue

from .strategy import StrategyConfig, StrategyEngine
from .trading import get_trading_client, TradingClient
from .quote_provider import InsightSentryQuoteProvider
from .parser import parse_message_line
from .models import Announcement

logger = logging.getLogger(__name__)


class AlertHandler(BaseHTTPRequestHandler):
    """HTTP handler for incoming Discord alerts."""

    # Class-level references set by LiveTradingService
    alert_queue: Queue = None
    service: "LiveTradingService" = None

    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        """Handle incoming alert."""
        if self.path != "/alert":
            self.send_response(404)
            self.end_headers()
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)

            # Queue the alert for processing
            if self.alert_queue:
                self.alert_queue.put(data)
                logger.info(f"Alert queued: {data.get('ticker', 'unknown')}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())

        except Exception as e:
            logger.error(f"Error handling alert: {e}")
            self.send_response(500)
            self.end_headers()


class LiveTradingService:
    """
    Background service for live trading.

    Coordinates:
    - HTTP server for receiving Discord alerts
    - WebSocket connection for real-time quotes
    - Strategy engine for entry/exit decisions
    - Trading client for order execution
    """

    def __init__(
        self,
        config: StrategyConfig,
        paper: bool = True,
        alert_port: int = 8765,
    ):
        self.config = config
        self.paper = paper
        self.alert_port = alert_port

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._alert_queue: Queue = Queue()

        # Components initialized in start()
        self.trader: Optional[TradingClient] = None
        self.quote_provider: Optional[InsightSentryQuoteProvider] = None
        self.engine: Optional[StrategyEngine] = None
        self.http_server: Optional[HTTPServer] = None

        # Callbacks for external status updates
        self.on_status_change: Optional[Callable[[dict], None]] = None

    def start(self):
        """Start the live trading service in a background thread."""
        if self._running:
            logger.warning("Service already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Live trading service started")

    def stop(self):
        """Stop the live trading service."""
        if not self._running:
            return

        logger.info("Stopping live trading service...")
        self._running = False

        # Stop HTTP server
        if self.http_server:
            self.http_server.shutdown()

        # Stop event loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        logger.info("Live trading service stopped")

    def _run(self):
        """Main service loop (runs in background thread)."""
        try:
            # Create new event loop for this thread
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # Initialize components
            self._init_components()

            # Run async main
            self._loop.run_until_complete(self._async_main())

        except Exception as e:
            logger.error(f"Service error: {e}", exc_info=True)
        finally:
            if self._loop:
                self._loop.close()
            self._running = False

    def _init_components(self):
        """Initialize trading components."""
        # Trading client
        self.trader = get_trading_client(paper=self.paper)
        logger.info(f"Trading client: {self.trader.name} (paper={self.paper})")

        # Quote provider
        self.quote_provider = InsightSentryQuoteProvider(
            on_quote=self._on_quote,
        )

        # Strategy engine
        self.engine = StrategyEngine(
            config=self.config,
            trader=self.trader,
            on_subscribe=self._on_subscribe,
            on_unsubscribe=self._on_unsubscribe,
            paper=self.paper,
        )

        logger.info(f"Strategy config: {self.config.to_dict()}")

    async def _async_main(self):
        """Async main loop."""
        # Start HTTP server for alerts
        self._start_http_server()

        # Connect to quote provider
        quote_task = asyncio.create_task(self._run_quote_provider())

        # Process alerts
        alert_task = asyncio.create_task(self._process_alerts())

        # Wait for shutdown
        try:
            while self._running:
                await asyncio.sleep(1)
                self._broadcast_status()
        except asyncio.CancelledError:
            pass
        finally:
            quote_task.cancel()
            alert_task.cancel()
            try:
                await quote_task
            except asyncio.CancelledError:
                pass
            try:
                await alert_task
            except asyncio.CancelledError:
                pass

    def _start_http_server(self):
        """Start HTTP server for receiving alerts."""
        AlertHandler.alert_queue = self._alert_queue
        AlertHandler.service = self

        self.http_server = HTTPServer(("0.0.0.0", self.alert_port), AlertHandler)

        # Run in background thread
        http_thread = threading.Thread(
            target=self.http_server.serve_forever,
            daemon=True,
        )
        http_thread.start()
        logger.info(f"HTTP server listening on port {self.alert_port}")

    async def _run_quote_provider(self):
        """Run the WebSocket quote provider."""
        try:
            await self.quote_provider.connect()
        except Exception as e:
            logger.error(f"Quote provider error: {e}")
            raise

    async def _process_alerts(self):
        """Process alerts from the queue."""
        while self._running:
            try:
                # Non-blocking check for alerts
                if not self._alert_queue.empty():
                    data = self._alert_queue.get_nowait()
                    await self._handle_alert(data)
                else:
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error processing alert: {e}")

    async def _handle_alert(self, data: dict):
        """Handle an incoming alert from Discord."""
        try:
            # Parse the alert into an Announcement
            content = data.get("content", "")
            channel = data.get("channel", "")
            author = data.get("author")
            timestamp_str = data.get("timestamp")

            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.now()

            # Parse the message content
            announcement = parse_message_line(content, channel, author, timestamp)

            if not announcement:
                logger.warning(f"Could not parse alert: {content[:100]}")
                return

            logger.info(f"Alert received: {announcement.ticker} @ ${announcement.price_threshold}")

            # Pass to strategy engine
            accepted = self.engine.on_alert(announcement)

            if accepted:
                logger.info(f"Alert accepted, tracking {announcement.ticker}")

        except Exception as e:
            logger.error(f"Error handling alert: {e}", exc_info=True)

    def _on_quote(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """Callback for quote updates from InsightSentry."""
        if self.engine:
            self.engine.on_quote(ticker, price, volume, timestamp)

    def _on_subscribe(self, ticker: str):
        """Callback when strategy needs quotes for a ticker."""
        if self.quote_provider:
            self.quote_provider.subscribe_sync(ticker)
            # Trigger re-send of subscriptions on next opportunity
            if self._loop:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self.quote_provider.subscribe(ticker))
                )

    def _on_unsubscribe(self, ticker: str):
        """Callback when strategy no longer needs quotes."""
        if self.quote_provider:
            self.quote_provider.unsubscribe_sync(ticker)
            if self._loop:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self.quote_provider.unsubscribe(ticker))
                )

    def _broadcast_status(self):
        """Broadcast current status to listeners."""
        if self.on_status_change and self.engine:
            status = self.get_status()
            self.on_status_change(status)

    def get_status(self) -> dict:
        """Get current service status."""
        status = {
            "running": self._running,
            "paper": self.paper,
            "quote_connected": self.quote_provider.is_connected if self.quote_provider else False,
            "subscriptions": list(self.quote_provider.subscribed_tickers) if self.quote_provider else [],
        }

        if self.engine:
            status.update(self.engine.get_status())

        if self.trader:
            try:
                account = self.trader.get_account_info()
                status["account"] = {
                    "equity": account.get("equity", 0),
                    "buying_power": account.get("buying_power", 0),
                }
            except Exception:
                pass

        return status

    @property
    def is_running(self) -> bool:
        return self._running


# Global instance for dashboard integration
_service_instance: Optional[LiveTradingService] = None


def start_live_trading(config: StrategyConfig, paper: bool = True) -> LiveTradingService:
    """Start the global live trading service."""
    global _service_instance

    if _service_instance and _service_instance.is_running:
        logger.warning("Service already running, stopping first...")
        _service_instance.stop()

    _service_instance = LiveTradingService(config, paper=paper)
    _service_instance.start()
    return _service_instance


def stop_live_trading():
    """Stop the global live trading service."""
    global _service_instance

    if _service_instance:
        _service_instance.stop()
        _service_instance = None


def get_live_trading_status() -> Optional[dict]:
    """Get status of the global live trading service."""
    global _service_instance

    if _service_instance and _service_instance.is_running:
        return _service_instance.get_status()
    return None


def is_live_trading_active() -> bool:
    """Check if live trading is active."""
    global _service_instance
    return _service_instance is not None and _service_instance.is_running
