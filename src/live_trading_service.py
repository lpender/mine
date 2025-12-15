"""Live trading service that coordinates quotes and trading."""

import asyncio
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from .strategy import StrategyConfig, StrategyEngine
from .trading import get_trading_client, TradingClient
from .quote_provider import InsightSentryQuoteProvider
from .parser import parse_message_line
from .alert_service import set_alert_callback

logger = logging.getLogger(__name__)

# Lock file to detect if service is already running (survives module reloads)
TRADING_LOCK_FILE = Path(__file__).parent.parent / "data" / ".trading.lock"


class TradingEngine:
    """
    Trading engine that processes alerts and manages positions.

    Does NOT run its own HTTP server - receives alerts via callback from AlertService.
    """

    def __init__(
        self,
        config: StrategyConfig,
        paper: bool = True,
    ):
        self.config = config
        self.paper = paper

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Components initialized in start()
        self.trader: Optional[TradingClient] = None
        self.quote_provider: Optional[InsightSentryQuoteProvider] = None
        self.engine: Optional[StrategyEngine] = None

        # Callbacks for external status updates
        self.on_status_change: Optional[Callable[[dict], None]] = None

    def _acquire_lock(self) -> bool:
        """Try to acquire the trading lock file. Returns True if acquired."""
        import os
        import time

        try:
            TRADING_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Check if lock file exists and is recent (within 30 seconds)
            if TRADING_LOCK_FILE.exists():
                try:
                    lock_data = TRADING_LOCK_FILE.read_text().strip().split("\n")
                    lock_pid = int(lock_data[0])
                    lock_time = float(lock_data[1])

                    # Check if the process is still alive
                    try:
                        os.kill(lock_pid, 0)  # Doesn't kill, just checks
                        # Process exists - check if lock is stale (>60s old)
                        if time.time() - lock_time < 60:
                            logger.warning(f"Trading already running (PID {lock_pid})")
                            return False
                    except OSError:
                        # Process is dead, lock is stale
                        logger.info("Found stale lock file, removing...")
                except (ValueError, IndexError):
                    pass  # Invalid lock file, overwrite it

            # Write our lock
            TRADING_LOCK_FILE.write_text(f"{os.getpid()}\n{time.time()}")
            return True

        except Exception as e:
            logger.error(f"Error acquiring lock: {e}")
            return True  # Proceed anyway if lock check fails

    def _release_lock(self):
        """Release the trading lock file."""
        try:
            if TRADING_LOCK_FILE.exists():
                TRADING_LOCK_FILE.unlink()
        except Exception as e:
            logger.warning(f"Error releasing lock: {e}")

    def _update_lock(self):
        """Update lock file timestamp (heartbeat)."""
        import os
        import time
        try:
            TRADING_LOCK_FILE.write_text(f"{os.getpid()}\n{time.time()}")
        except Exception:
            pass

    def start(self):
        """Start the trading engine in a background thread."""
        if self._running:
            logger.warning("Trading engine already running")
            return

        # Check lock file to prevent duplicate instances
        if not self._acquire_lock():
            logger.error("Cannot start: trading engine already running in another instance")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="TradingEngine")
        self._thread.start()

        # Register callback with alert service
        set_alert_callback(self._on_alert_received)

        logger.info("Trading engine started")

    def stop(self):
        """Stop the trading engine."""
        if not self._running:
            return

        logger.info("Stopping trading engine...")
        self._running = False

        # Unregister callback
        set_alert_callback(None)

        # Stop event loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        # Release lock file
        self._release_lock()

        logger.info("Trading engine stopped")

    def _run(self):
        """Main engine loop (runs in background thread)."""
        try:
            # Create new event loop for this thread
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # Initialize components
            self._init_components()

            # Run async main
            self._loop.run_until_complete(self._async_main())

        except Exception as e:
            logger.error(f"Trading engine error: {e}", exc_info=True)
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
        # Connect to quote provider
        quote_task = asyncio.create_task(self._run_quote_provider())

        # Wait for shutdown
        try:
            while self._running:
                await asyncio.sleep(1)
                self._broadcast_status()
        except asyncio.CancelledError:
            pass
        finally:
            quote_task.cancel()
            try:
                await quote_task
            except asyncio.CancelledError:
                pass

    async def _run_quote_provider(self):
        """Run the WebSocket quote provider."""
        try:
            await self.quote_provider.connect()
        except Exception as e:
            logger.error(f"Quote provider error: {e}")
            raise

    def _on_alert_received(self, data: dict):
        """Handle alert from AlertService (called from HTTP thread)."""
        if not self._running or not self.engine:
            return

        # Schedule processing in our event loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._handle_alert(data))
            )

    async def _handle_alert(self, data: dict):
        """Process an alert from Discord."""
        try:
            content = data.get("content", "")
            channel = data.get("channel", "")
            author = data.get("author")
            timestamp_str = data.get("timestamp")

            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                timestamp = datetime.now()

            # Parse the message content
            announcement = parse_message_line(content, timestamp)

            if not announcement:
                logger.warning(f"Could not parse alert: {content[:100]}")
                return

            # Set channel and author from the alert data
            announcement.channel = channel
            if author:
                announcement.author = author

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
        # Update lock file heartbeat
        self._update_lock()

        if self.on_status_change and self.engine:
            status = self.get_status()
            self.on_status_change(status)

    def get_status(self) -> dict:
        """Get current engine status."""
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


# Backwards compatibility aliases
LiveTradingService = TradingEngine

# Global instance for dashboard integration
_trading_engine: Optional[TradingEngine] = None


def start_live_trading(config: StrategyConfig, paper: bool = True) -> Optional[TradingEngine]:
    """Start the global trading engine."""
    global _trading_engine

    # Check if already running in this process
    if _trading_engine and _trading_engine.is_running:
        logger.warning("Trading engine already running, stopping first...")
        _trading_engine.stop()

    # Check if running in another process (or after module reload)
    if is_trading_locked():
        logger.warning("Trading appears to be running (lock file exists). Use force_release_trading_lock() if stuck.")
        return None

    _trading_engine = TradingEngine(config, paper=paper)
    _trading_engine.start()
    return _trading_engine


def stop_live_trading():
    """Stop the global trading engine."""
    global _trading_engine

    if _trading_engine:
        _trading_engine.stop()
        _trading_engine = None


def get_live_trading_status() -> Optional[dict]:
    """Get status of the global trading engine."""
    global _trading_engine

    if _trading_engine and _trading_engine.is_running:
        return _trading_engine.get_status()
    return None


def is_live_trading_active() -> bool:
    """Check if live trading is active."""
    global _trading_engine
    return _trading_engine is not None and _trading_engine.is_running


def is_trading_locked() -> bool:
    """Check if trading is running (even across module reloads)."""
    import os
    import time

    try:
        if not TRADING_LOCK_FILE.exists():
            return False

        lock_data = TRADING_LOCK_FILE.read_text().strip().split("\n")
        lock_pid = int(lock_data[0])
        lock_time = float(lock_data[1])

        # Check if process is alive
        try:
            os.kill(lock_pid, 0)
            # Process exists - check if lock is fresh (<60s old)
            return time.time() - lock_time < 60
        except OSError:
            return False  # Process is dead

    except Exception:
        return False


def force_release_trading_lock():
    """Force release the trading lock (for debugging/recovery)."""
    try:
        if TRADING_LOCK_FILE.exists():
            TRADING_LOCK_FILE.unlink()
            logger.info("Force released trading lock")
    except Exception as e:
        logger.error(f"Error force releasing lock: {e}")
