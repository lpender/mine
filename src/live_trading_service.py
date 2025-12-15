"""Live trading service that coordinates quotes and trading."""

import asyncio
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict

from .strategy import StrategyConfig, StrategyEngine
from .trading import get_trading_client, TradingClient
from .quote_provider import InsightSentryQuoteProvider
from .parser import parse_message_line
from .alert_service import set_alert_callback
from .strategy_store import get_strategy_store, Strategy

logger = logging.getLogger(__name__)

# Lock file to detect if service is already running (survives module reloads)
TRADING_LOCK_FILE = Path(__file__).parent.parent / "data" / ".trading.lock"
# Status file for cross-process status sharing
TRADING_STATUS_FILE = Path(__file__).parent.parent / "data" / ".trading_status.json"


class TradingEngine:
    """
    Trading engine that processes alerts and manages multiple strategies.

    Does NOT run its own HTTP server - receives alerts via callback from AlertService.
    Supports multiple concurrent strategies, each with independent position tracking.
    """

    def __init__(self, paper: bool = True):
        self.paper = paper

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Components initialized in start()
        self.trader: Optional[TradingClient] = None
        self.quote_provider: Optional[InsightSentryQuoteProvider] = None

        # Multi-strategy support: strategy_id -> StrategyEngine
        self.strategies: Dict[str, StrategyEngine] = {}
        self.strategy_names: Dict[str, str] = {}  # strategy_id -> name

        # Track subscriptions per strategy for proper cleanup
        self._strategy_subscriptions: Dict[str, set] = {}  # strategy_id -> set of tickers

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

        # Note: callback is registered in _init_components after strategies are loaded
        logger.info("Trading engine started")

    def stop(self):
        """Stop the trading engine."""
        if not self._running:
            return

        logger.info("Stopping trading engine...")
        self._running = False

        # Unregister callback
        set_alert_callback(None)

        # Give the loop time to exit gracefully (up to 3 seconds)
        # This allows the finally block to run and disconnect WebSocket
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        # If still running, force stop the event loop
        if self._thread and self._thread.is_alive():
            logger.warning("Forcing event loop stop...")
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2.0)

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

        # Quote provider (shared across all strategies)
        self.quote_provider = InsightSentryQuoteProvider(
            on_quote=self._on_quote,
        )

        # Load enabled strategies from database
        self._load_enabled_strategies()

        # Register callback with alert service (after strategies are loaded)
        set_alert_callback(self._on_alert_received)
        logger.info("Alert callback registered")

    def _load_enabled_strategies(self):
        """Load all enabled strategies from the database."""
        store = get_strategy_store()
        enabled = store.list_strategies(enabled_only=True)

        for strategy in enabled:
            self._add_strategy_engine(strategy.id, strategy.name, strategy.config)

        logger.info(f"Loaded {len(enabled)} enabled strategies")

    def _add_strategy_engine(self, strategy_id: str, name: str, config: StrategyConfig):
        """Create and add a StrategyEngine for a strategy."""
        if strategy_id in self.strategies:
            logger.warning(f"Strategy {strategy_id} already running")
            return

        engine = StrategyEngine(
            strategy_id=strategy_id,
            strategy_name=name,
            config=config,
            trader=self.trader,
            on_subscribe=lambda ticker, sid=strategy_id: self._on_subscribe(ticker, sid),
            on_unsubscribe=lambda ticker, sid=strategy_id: self._on_unsubscribe(ticker, sid),
            paper=self.paper,
        )

        self.strategies[strategy_id] = engine
        self.strategy_names[strategy_id] = name
        self._strategy_subscriptions[strategy_id] = set()

        logger.info(f"Added strategy '{name}' ({strategy_id})")

    def _remove_strategy_engine(self, strategy_id: str):
        """Remove a StrategyEngine."""
        if strategy_id not in self.strategies:
            return

        name = self.strategy_names.get(strategy_id, strategy_id)

        # Unsubscribe from all tickers this strategy was watching
        for ticker in list(self._strategy_subscriptions.get(strategy_id, [])):
            self._on_unsubscribe(ticker, strategy_id)

        del self.strategies[strategy_id]
        del self.strategy_names[strategy_id]
        if strategy_id in self._strategy_subscriptions:
            del self._strategy_subscriptions[strategy_id]

        logger.info(f"Removed strategy '{name}' ({strategy_id})")

    def add_strategy(self, strategy_id: str, name: str, config: StrategyConfig):
        """Add and start tracking a strategy (call from main thread)."""
        if not self._running:
            logger.warning("Trading engine not running, cannot add strategy")
            return

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: self._add_strategy_engine(strategy_id, name, config)
            )

    def remove_strategy(self, strategy_id: str):
        """Stop tracking a strategy (call from main thread)."""
        if not self._running:
            return

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: self._remove_strategy_engine(strategy_id)
            )

    async def _async_main(self):
        """Async main loop."""
        # Connect to quote provider
        quote_task = asyncio.create_task(self._run_quote_provider())

        # Reconciliation counter (run every 30 iterations = 30 seconds)
        reconcile_counter = 0
        RECONCILE_INTERVAL = 300  # 5 minutes - Alpaca rate limits are strict

        # Wait for shutdown
        try:
            while self._running:
                await asyncio.sleep(1)
                self._broadcast_status()

                # Periodic position reconciliation
                reconcile_counter += 1
                if reconcile_counter >= RECONCILE_INTERVAL:
                    reconcile_counter = 0
                    self._reconcile_all_positions()
        except asyncio.CancelledError:
            pass
        finally:
            # Cancel the quote task
            quote_task.cancel()
            try:
                await quote_task
            except asyncio.CancelledError:
                pass

            # Explicitly disconnect WebSocket
            if self.quote_provider:
                try:
                    await self.quote_provider.disconnect()
                    logger.info("WebSocket disconnected")
                except Exception as e:
                    logger.warning(f"Error disconnecting WebSocket: {e}")

    async def _run_quote_provider(self):
        """Run the WebSocket quote provider."""
        try:
            await self.quote_provider.connect()
        except Exception as e:
            logger.error(f"Quote provider error: {e}")
            raise

    def _on_alert_received(self, data: dict):
        """Handle alert from AlertService (called from HTTP thread)."""
        if not self._running:
            logger.warning("Alert received but trading engine not running - dropping")
            return
        if not self.strategies:
            logger.warning(f"Alert received but no strategies loaded - dropping: {data.get('content', '')[:50]}")
            return

        # Schedule processing in our event loop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._handle_alert(data))
            )

    async def _handle_alert(self, data: dict):
        """Process an alert from Discord - fan out to all strategies."""
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

            # Fan out to all strategies - each decides independently
            accepted_by = []
            for strategy_id, engine in self.strategies.items():
                name = self.strategy_names.get(strategy_id, strategy_id)
                if engine.on_alert(announcement):
                    accepted_by.append(name)

            if accepted_by:
                logger.info(f"Alert accepted by: {', '.join(accepted_by)}")

        except Exception as e:
            logger.error(f"Error handling alert: {e}", exc_info=True)

    def _on_quote(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """Callback for quote updates - fan out to all strategies tracking this ticker."""
        for strategy_id, engine in self.strategies.items():
            # Each strategy receives all quotes, filters internally
            engine.on_quote(ticker, price, volume, timestamp)

    def _on_subscribe(self, ticker: str, strategy_id: str):
        """Callback when a strategy needs quotes for a ticker."""
        # Track which strategy subscribed
        if strategy_id not in self._strategy_subscriptions:
            self._strategy_subscriptions[strategy_id] = set()
        self._strategy_subscriptions[strategy_id].add(ticker)

        # Only subscribe to WebSocket if this is a new ticker (not already subscribed by another strategy)
        all_subscribed = set()
        for subs in self._strategy_subscriptions.values():
            all_subscribed.update(subs)

        if self.quote_provider:
            # Check if already subscribed via quote provider
            if ticker not in self.quote_provider.subscribed_tickers:
                self.quote_provider.subscribe_sync(ticker)
                if self._loop and self._loop.is_running():
                    logger.info(f"Scheduling async subscribe for {ticker} (requested by strategy {strategy_id})")
                    self._loop.call_soon_threadsafe(
                        lambda t=ticker: asyncio.create_task(self.quote_provider.subscribe(t))
                    )
                else:
                    logger.warning(f"Event loop not running, cannot send subscription for {ticker}")

    def _on_unsubscribe(self, ticker: str, strategy_id: str):
        """Callback when a strategy no longer needs quotes for a ticker."""
        # Remove from this strategy's subscriptions
        if strategy_id in self._strategy_subscriptions:
            self._strategy_subscriptions[strategy_id].discard(ticker)

        # Check if any other strategy still needs this ticker
        still_needed = False
        for sid, subs in self._strategy_subscriptions.items():
            if ticker in subs:
                still_needed = True
                break

        # Only unsubscribe from WebSocket if no strategy needs it
        if not still_needed and self.quote_provider:
            self.quote_provider.unsubscribe_sync(ticker)
            if self._loop:
                self._loop.call_soon_threadsafe(
                    lambda t=ticker: asyncio.create_task(self.quote_provider.unsubscribe(t))
                )

    def _broadcast_status(self):
        """Broadcast current status to listeners and persist to file."""
        # Update lock file heartbeat
        self._update_lock()

        # Get and persist status
        status = self.get_status()
        self._write_status_file(status)

        if self.on_status_change:
            self.on_status_change(status)

    def _write_status_file(self, status: dict):
        """Write status to file for cross-process reading."""
        try:
            TRADING_STATUS_FILE.write_text(json.dumps(status))
        except Exception:
            pass  # Don't fail on status write errors

    def _reconcile_all_positions(self):
        """Reconcile positions across all strategies with broker.

        Fetches positions once and passes to all strategies to avoid rate limits.
        """
        if not self.strategies:
            return

        # Fetch positions once for all strategies
        try:
            positions = self.trader.get_positions()
            broker_positions = {p.ticker: p for p in positions}
            logger.debug(f"Reconciliation: fetched {len(broker_positions)} positions from broker")
        except Exception as e:
            if "429" in str(e):
                logger.warning("Rate limited by broker - skipping reconciliation")
            else:
                logger.error(f"Failed to fetch positions for reconciliation: {e}")
            return

        # Pass pre-fetched positions to each strategy
        for strategy_id, engine in self.strategies.items():
            try:
                engine.reconcile_positions(broker_positions)
            except Exception as e:
                logger.error(f"Reconciliation failed for strategy {strategy_id}: {e}")

    def get_status(self) -> dict:
        """Get current engine status with per-strategy breakdown."""
        status = {
            "running": self._running,
            "paper": self.paper,
            "quote_connected": self.quote_provider.is_connected if self.quote_provider else False,
            "subscriptions": list(self.quote_provider.subscribed_tickers) if self.quote_provider else [],
            "strategy_count": len(self.strategies),
        }

        # Aggregate stats across all strategies
        total_pending = []
        total_active = {}
        total_completed = 0

        # Per-strategy status
        strategies_status = {}
        for strategy_id, engine in self.strategies.items():
            name = self.strategy_names.get(strategy_id, strategy_id)
            engine_status = engine.get_status()
            strategies_status[strategy_id] = {
                "name": name,
                **engine_status,
            }
            # Aggregate
            total_pending.extend(engine_status.get("pending_entries", []))
            total_active.update(engine_status.get("active_trades", {}))
            total_completed += engine_status.get("completed_trades", 0)

        status["strategies"] = strategies_status
        status["pending_entries"] = total_pending
        status["active_trades"] = total_active
        status["completed_trades"] = total_completed

        if self.trader:
            try:
                account = self.trader.get_account_info()
                status["account"] = {
                    "equity": account.get("equity", 0),
                    "buying_power": account.get("buying_power", 0),
                }
            except Exception:
                pass

            try:
                open_orders = self.trader.get_open_orders()
                status["open_orders"] = [
                    {
                        "order_id": o.order_id,
                        "ticker": o.ticker,
                        "side": o.side,
                        "shares": o.shares,
                        "status": o.status,
                    }
                    for o in open_orders
                ]
            except Exception:
                status["open_orders"] = []

        return status

    def get_strategy_status(self, strategy_id: str) -> Optional[dict]:
        """Get status for a specific strategy."""
        if strategy_id not in self.strategies:
            return None

        engine = self.strategies[strategy_id]
        name = self.strategy_names.get(strategy_id, strategy_id)
        return {
            "id": strategy_id,
            "name": name,
            **engine.get_status(),
        }

    @property
    def is_running(self) -> bool:
        return self._running


# Backwards compatibility aliases
LiveTradingService = TradingEngine

# Global instance for dashboard integration
_trading_engine: Optional[TradingEngine] = None


def start_live_trading(paper: bool = True) -> Optional[TradingEngine]:
    """Start the global trading engine (loads enabled strategies from DB)."""
    global _trading_engine

    # Check if already running in this process
    if _trading_engine and _trading_engine.is_running:
        logger.warning("Trading engine already running")
        return _trading_engine

    # Check if running in another process (or after module reload)
    if is_trading_locked():
        logger.warning("Trading appears to be running (lock file exists). Use force_release_trading_lock() if stuck.")
        return None

    _trading_engine = TradingEngine(paper=paper)
    _trading_engine.start()
    return _trading_engine


def stop_live_trading():
    """Stop the global trading engine."""
    global _trading_engine

    if _trading_engine:
        _trading_engine.stop()
        _trading_engine = None

    # Clean up status file
    try:
        if TRADING_STATUS_FILE.exists():
            TRADING_STATUS_FILE.unlink()
    except Exception:
        pass


def get_trading_engine() -> Optional[TradingEngine]:
    """Get the global trading engine instance."""
    global _trading_engine
    return _trading_engine


def get_live_trading_status() -> Optional[dict]:
    """Get status of the global trading engine."""
    global _trading_engine

    # First try in-memory engine
    if _trading_engine and _trading_engine.is_running:
        return _trading_engine.get_status()

    # Fall back to status file (for cross-process/module-reload)
    if is_trading_locked():
        try:
            if TRADING_STATUS_FILE.exists():
                return json.loads(TRADING_STATUS_FILE.read_text())
        except Exception:
            pass

    return None


def is_live_trading_active() -> bool:
    """Check if live trading is active (in this process or another)."""
    global _trading_engine
    # First check in-memory reference
    if _trading_engine is not None and _trading_engine.is_running:
        return True
    # Fall back to lock file check (for cross-process/module-reload detection)
    return is_trading_locked()


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
        if TRADING_STATUS_FILE.exists():
            TRADING_STATUS_FILE.unlink()
            logger.info("Removed stale status file")
    except Exception as e:
        logger.error(f"Error force releasing lock: {e}")


def enable_strategy(strategy_id: str) -> bool:
    """Enable a strategy for live trading."""
    global _trading_engine

    store = get_strategy_store()

    # Update database
    if not store.set_enabled(strategy_id, True):
        logger.error(f"Strategy {strategy_id} not found")
        return False

    # If engine is running, add the strategy dynamically
    if _trading_engine and _trading_engine.is_running:
        strategy = store.get_strategy(strategy_id)
        if strategy:
            _trading_engine.add_strategy(strategy.id, strategy.name, strategy.config)

    return True


def disable_strategy(strategy_id: str) -> bool:
    """Disable a strategy from live trading."""
    global _trading_engine

    store = get_strategy_store()

    # Update database
    if not store.set_enabled(strategy_id, False):
        logger.error(f"Strategy {strategy_id} not found")
        return False

    # If engine is running, remove the strategy dynamically
    if _trading_engine and _trading_engine.is_running:
        _trading_engine.remove_strategy(strategy_id)

    return True
