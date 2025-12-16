"""Live trading service that coordinates quotes and trading."""

import asyncio
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, List, Tuple

from .strategy import StrategyConfig, StrategyEngine
from .trading import get_trading_client, TradingClient
from .trading.alpaca_stream import AlpacaTradeStream
from .quote_provider import InsightSentryQuoteProvider
from .parser import parse_message_line
from .alert_service import set_alert_callback
from .strategy_store import get_strategy_store, Strategy
from .live_bar_store import get_live_bar_store

logger = logging.getLogger(__name__)

# Lock file to detect if service is already running (survives module reloads)
TRADING_LOCK_FILE = Path(__file__).parent.parent / "data" / ".trading.lock"
# Status file for cross-process status sharing
TRADING_STATUS_FILE = Path(__file__).parent.parent / "data" / ".trading_status.json"


def _get_max_positions_from_jwt() -> int:
    """
    Parse InsightSentry JWT token to extract websocket_symbols limit.

    JWT payload contains: {"websocket_symbols": 5, ...}
    Falls back to 5 if parsing fails.
    """
    from .jwt_utils import get_websocket_symbols_limit
    return get_websocket_symbols_limit()


# Maximum number of open positions across all strategies (0 = unlimited)
# Derived from InsightSentry JWT token's websocket_symbols limit
MAX_OPEN_POSITIONS = _get_max_positions_from_jwt()


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
        self.trade_stream: Optional[AlpacaTradeStream] = None

        # Multi-strategy support: strategy_id -> StrategyEngine
        self.strategies: Dict[str, StrategyEngine] = {}
        self.strategy_names: Dict[str, str] = {}  # strategy_id -> name
        self.strategy_priorities: Dict[str, int] = {}  # strategy_id -> priority

        # Track subscriptions per strategy for proper cleanup
        self._strategy_subscriptions: Dict[str, set] = {}  # strategy_id -> set of tickers

        # Ticker lock for ALERT ROUTING only - prevents same alert from triggering
        # multiple strategies. Does NOT affect position management - multiple strategies
        # CAN hold independent positions in the same ticker.
        self._locked_tickers: Dict[str, str] = {}  # ticker -> strategy_id for alert routing

        # Orphaned tickers (positions in DB but strategy disabled)
        self._orphaned_tickers: set = set()

        # Live bar storage for TradingView visualization
        self._live_bar_store = get_live_bar_store()

        # Callbacks for external status updates
        self.on_status_change: Optional[Callable[[dict], None]] = None

        # Cache for Alpaca API calls (reduce rate limiting)
        self._cached_account: Optional[dict] = None
        self._cached_orders: Optional[list] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 30.0  # Cache for 30 seconds

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

        # Stop the trade stream
        if self.trade_stream:
            self.trade_stream.stop()

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

        # Trade updates stream (order fills, cancellations)
        self.trade_stream = AlpacaTradeStream(
            paper=self.paper,
            on_fill=self._on_order_fill,
            on_partial_fill=self._on_partial_fill,  # Log only, wait for final fill
            on_canceled=self._on_order_canceled,
            on_rejected=self._on_order_rejected,
        )
        self.trade_stream.start()
        logger.info("Alpaca trade stream started")

        # Quote provider (shared across all strategies)
        self.quote_provider = InsightSentryQuoteProvider(
            on_quote=self._on_quote,
            on_bar=self._on_bar,
            on_symbol_error=self._on_symbol_error,
        )

        # Load enabled strategies from database
        self._load_enabled_strategies()

        # Register callback with alert service (after strategies are loaded)
        set_alert_callback(self._on_alert_received)
        logger.info("Alert callback registered")

    def _load_enabled_strategies(self):
        """Load all enabled strategies from the database (ordered by priority).

        Multi-strategy support: Multiple strategies CAN hold positions in the same ticker.
        Each strategy tracks its own position independently (e.g., strategy A has 100 shares,
        strategy B has 50 shares of the same stock). The ticker lock is ONLY used for
        alert routing - preventing the same alert from triggering entries in multiple strategies.
        """
        store = get_strategy_store()
        enabled = store.list_strategies(enabled_only=True)  # Already ordered by priority

        for strategy in enabled:
            self._add_strategy_engine(strategy.id, strategy.name, strategy.config, strategy.priority)

        # Log recovered positions per strategy (no locking needed for positions)
        total_active_trades = 0
        total_pending_entries = 0
        for strategy_id, engine in self.strategies.items():
            name = self.strategy_names[strategy_id]
            active_count = len(engine.active_trades)
            pending_count = len(engine.pending_entries)
            total_active_trades += active_count
            total_pending_entries += pending_count
            if active_count > 0:
                logger.info(f"Strategy '{name}' recovered {active_count} active trades: {list(engine.active_trades.keys())}")
            if pending_count > 0:
                logger.info(f"Strategy '{name}' recovered {pending_count} pending entries: {list(engine.pending_entries.keys())}")

        logger.info(f"Loaded {len(enabled)} enabled strategies with {total_active_trades} active trades, {total_pending_entries} pending entries")
        logger.info(f"Position limit: {MAX_OPEN_POSITIONS} (from JWT websocket_symbols)")

        # Check for orphaned trades (trades in DB but strategy is disabled)
        self._check_orphaned_trades()

        # Enforce position limit at startup (close excess positions if any)
        if total_active_trades > MAX_OPEN_POSITIONS > 0:
            logger.warning(f"Startup: {total_active_trades} positions exceed limit of {MAX_OPEN_POSITIONS}")
            self._enforce_position_limit()

    def _check_orphaned_trades(self):
        """Check for trades in DB whose strategy is disabled (orphaned positions).

        These positions exist at the broker but won't be monitored because their
        owning strategy isn't enabled. This is dangerous - stop losses won't be enforced.
        """
        from src.active_trade_store import get_active_trade_store
        trade_store = get_active_trade_store()
        all_trades = trade_store.get_all_trades()

        if not all_trades:
            return

        # Find trades whose strategy is not currently loaded
        loaded_strategy_ids = set(self.strategies.keys())
        orphaned = []

        for trade in all_trades:
            if trade.strategy_id not in loaded_strategy_ids:
                orphaned.append(trade)

        if orphaned:
            logger.warning("=" * 60)
            logger.warning("ORPHANED POSITIONS DETECTED - NOT BEING MONITORED!")
            logger.warning("These positions exist but their strategy is DISABLED:")
            for trade in orphaned:
                logger.warning(f"  {trade.ticker}: {trade.shares} shares @ ${trade.entry_price:.2f} "
                              f"(strategy: {trade.strategy_name})")
            logger.warning("Stop losses will NOT be enforced for these positions!")
            logger.warning("Enable the strategy or manually close these positions.")
            logger.warning("=" * 60)

            # Store orphaned tickers for reference
            self._orphaned_tickers = {t.ticker for t in orphaned}
        else:
            self._orphaned_tickers = set()

    def _add_strategy_engine(self, strategy_id: str, name: str, config: StrategyConfig, priority: int = 0):
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
        self.strategy_priorities[strategy_id] = priority
        self._strategy_subscriptions[strategy_id] = set()

        logger.info(f"Added strategy '{name}' ({strategy_id}) [priority={priority}]")
        logger.info(f"  Filters: channels={config.channels}, price=${config.price_min:.2f}-${config.price_max:.2f}")
        logger.info(f"  Entry: {config.consec_green_candles} green candles, {config.min_candle_volume:,} min volume")
        logger.info(f"  Exit: TP={config.take_profit_pct}%, SL={config.stop_loss_pct}%, timeout={config.timeout_minutes}m")

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
        if strategy_id in self.strategy_priorities:
            del self.strategy_priorities[strategy_id]
        if strategy_id in self._strategy_subscriptions:
            del self._strategy_subscriptions[strategy_id]

        logger.info(f"Removed strategy '{name}' ({strategy_id})")

    def add_strategy(self, strategy_id: str, name: str, config: StrategyConfig, priority: int = 0):
        """Add and start tracking a strategy (call from main thread)."""
        if not self._running:
            logger.warning("Trading engine not running, cannot add strategy")
            return

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                lambda sid=strategy_id, n=name, c=config, p=priority: self._add_strategy_engine(sid, n, c, p)
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
        """Process an alert from Discord - route to highest priority strategy that accepts it.

        The ticker lock ensures only ONE strategy can enter on a given alert. This prevents
        the same alert from triggering entries in multiple strategies. However, multiple
        strategies CAN hold independent positions in the same ticker (e.g., from different
        alerts or different entry times).
        """
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

            ticker = announcement.ticker
            logger.info(f"Alert received: {ticker} @ ${announcement.price_threshold}")

            # Check if ticker is already locked by another strategy
            if ticker in self._locked_tickers:
                owner = self._locked_tickers[ticker]
                owner_name = self.strategy_names.get(owner, owner)
                logger.info(f"[{ticker}] Already locked by strategy '{owner_name}' - skipping alert")
                return

            # Sort strategies by priority (lower = higher priority)
            sorted_strategies = sorted(
                self.strategies.items(),
                key=lambda x: self.strategy_priorities.get(x[0], 999)
            )

            # Offer to strategies in priority order - first to accept wins
            for strategy_id, engine in sorted_strategies:
                name = self.strategy_names.get(strategy_id, strategy_id)
                if engine.on_alert(announcement):
                    # Lock the ticker for this strategy
                    self._locked_tickers[ticker] = strategy_id
                    logger.info(f"[{ticker}] Alert accepted by '{name}' (locked)")
                    return

            logger.info(f"[{ticker}] No strategy accepted the alert (filtered by all {len(sorted_strategies)} strategies)")

        except Exception as e:
            logger.error(f"Error handling alert: {e}", exc_info=True)

    def _on_quote(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """Callback for quote updates - dispatch to ALL strategies with positions.

        Multi-strategy support: each strategy tracks its own position independently.
        A strategy with 100 shares of AAPL is separate from another with 50 shares.
        Quote updates go to all strategies that have active trades OR pending entries
        for this ticker, allowing each to manage their own exit conditions.
        """
        dispatched = False
        for strategy_id, engine in self.strategies.items():
            # Dispatch if strategy has active trade OR pending entry for this ticker
            if ticker in engine.active_trades or ticker in engine.pending_entries:
                engine.on_quote(ticker, price, volume, timestamp)
                dispatched = True

        if not dispatched:
            # No strategy is tracking this ticker - shouldn't happen for subscribed tickers
            logger.debug(f"[{ticker}] Quote received but no strategy tracking it")

    def _on_bar(
        self,
        ticker: str,
        timestamp: datetime,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: int,
    ):
        """Callback for 1-second bar data - store for TradingView visualization."""
        # Only store bars for tickers we're actively tracking
        is_tracked = False
        strategy_id = None

        for sid, engine in self.strategies.items():
            if ticker in engine.pending_entries or ticker in engine.active_trades:
                is_tracked = True
                strategy_id = sid
                break

        if not is_tracked:
            return

        # Store the bar
        self._live_bar_store.save_bar(
            ticker=ticker,
            timestamp=timestamp,
            open_price=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            strategy_id=strategy_id,
        )

    def _on_symbol_error(self, ticker: str, error_type: str, message: str):
        """Handle symbol errors from WebSocket (e.g., invalid symbol, series error)."""
        logger.warning(f"[{ticker}] Symbol error: {message} ({error_type}) - aborting pending entries")

        # Abort pending entries for this ticker across all strategies
        for strategy_id, engine in self.strategies.items():
            if ticker in engine.pending_entries:
                engine._abandon_pending(ticker)
                logger.info(f"[{ticker}] Removed from pending entries for strategy {self.strategy_names.get(strategy_id, strategy_id)}")

        # Also unsubscribe from this ticker
        if self.quote_provider:
            self.quote_provider.unsubscribe_sync(ticker)

    def _on_partial_fill(
        self,
        order_id: str,
        ticker: str,
        side: str,
        shares: int,
        filled_price: float,
        timestamp: datetime,
    ):
        """Handle partial fill - just log, wait for final fill event."""
        logger.info(f"[{ticker}] Partial fill: {side} {shares} @ ${filled_price:.4f} (order={order_id}) - waiting for full fill")

    def _on_order_fill(
        self,
        order_id: str,
        ticker: str,
        side: str,
        shares: int,
        filled_price: float,
        timestamp: datetime,
    ):
        """Handle order fill from Alpaca stream - route to appropriate strategy."""
        logger.info(f"[{ticker}] Order fill: {side} {shares} @ ${filled_price:.4f} (order={order_id})")

        # Find which strategy has this pending order
        for strategy_id, engine in self.strategies.items():
            if order_id in engine.pending_orders:
                if side == "buy":
                    engine.on_buy_fill(order_id, ticker, shares, filled_price, timestamp)
                    # After new position opens, enforce the position limit
                    self._enforce_position_limit()
                else:
                    engine.on_sell_fill(order_id, ticker, shares, filled_price, timestamp)
                return

        # Fallback: for sell fills, try to find the position in the database
        if side == "sell":
            logger.warning(f"[{ticker}] Fill for unknown sell order {order_id} - checking database for position")
            from src.active_trade_store import get_active_trade_store
            store = get_active_trade_store()

            # Find which strategy had this position
            for strategy_id, engine in self.strategies.items():
                trade = store.get_trade(ticker, strategy_id)
                if trade:
                    logger.info(f"[{ticker}] Found position in DB for strategy {strategy_id[:8]} - completing trade")
                    # Create a minimal completed trade record
                    return_pct = ((filled_price - trade.entry_price) / trade.entry_price) * 100
                    pnl = (filled_price - trade.entry_price) * trade.shares

                    # Record to trade history
                    try:
                        from src.trade_history import get_trade_history_client
                        trade_record = {
                            "ticker": ticker,
                            "entry_price": trade.entry_price,
                            "exit_price": filled_price,
                            "entry_time": trade.entry_time,
                            "exit_time": timestamp,
                            "shares": trade.shares,
                            "exit_reason": "filled_after_restart",
                            "return_pct": return_pct,
                            "pnl": pnl,
                            "strategy_params": {},
                        }
                        get_trade_history_client().save_trade(
                            trade=trade_record,
                            paper=self.paper,
                            strategy_id=strategy_id,
                            strategy_name=trade.strategy_name,
                        )
                        logger.info(f"[{ticker}] ✅ Trade recorded: {return_pct:+.2f}% (${pnl:+.2f})")
                    except Exception as e:
                        logger.error(f"[{ticker}] Failed to record trade: {e}")

                    # Remove from database
                    store.delete_trade(ticker, strategy_id)

                    # Unsubscribe
                    self._on_unsubscribe(ticker, strategy_id)
                    return

            logger.warning(f"[{ticker}] No position found in database for any strategy")
        else:
            logger.warning(f"[{ticker}] Fill for unknown order {order_id} - no strategy owns it")

    def _on_order_canceled(self, order_id: str, ticker: str, side: str):
        """Handle order cancellation from Alpaca stream."""
        logger.warning(f"[{ticker}] Order canceled: {side} (order={order_id})")

        for strategy_id, engine in self.strategies.items():
            if order_id in engine.pending_orders:
                engine.on_order_canceled(order_id, ticker, side)
                return

    def _on_order_rejected(self, order_id: str, ticker: str, side: str, reason: str):
        """Handle order rejection from Alpaca stream."""
        logger.error(f"[{ticker}] Order rejected: {side} - {reason} (order={order_id})")

        for strategy_id, engine in self.strategies.items():
            if order_id in engine.pending_orders:
                engine.on_order_rejected(order_id, ticker, side, reason)
                return

    def _enforce_position_limit(self):
        """
        Enforce MAX_OPEN_POSITIONS limit by closing most recently entered positions.

        When we have more positions than allowed, close the newest ones first (LIFO).
        This helps stay within quote subscription limits (e.g., InsightSentry's 5-symbol limit).
        """
        if MAX_OPEN_POSITIONS <= 0:
            return  # Unlimited positions

        # Collect all active trades across all strategies with their entry times
        # Format: [(strategy_id, ticker, entry_time), ...]
        all_positions: List[Tuple[str, str, datetime]] = []

        for strategy_id, engine in self.strategies.items():
            for ticker, trade in engine.active_trades.items():
                all_positions.append((strategy_id, ticker, trade.entry_time))

        total_positions = len(all_positions)
        if total_positions <= MAX_OPEN_POSITIONS:
            return  # Within limit

        # Get existing sell orders from broker to avoid duplicates
        existing_sell_tickers = set()
        try:
            open_orders = self.trader.get_open_orders()
            for order in open_orders:
                if order.side == "sell":
                    existing_sell_tickers.add(order.ticker)
            if existing_sell_tickers:
                logger.info(f"Found existing sell orders for: {existing_sell_tickers}")
        except Exception as e:
            logger.warning(f"Could not check existing orders: {e}")

        # Sort by entry_time descending (newest first)
        all_positions.sort(key=lambda x: x[2], reverse=True)

        # Close the newest positions until we're at the limit
        positions_to_close = total_positions - MAX_OPEN_POSITIONS
        logger.warning(
            f"Position limit exceeded: {total_positions}/{MAX_OPEN_POSITIONS} - "
            f"closing {positions_to_close} most recent position(s)"
        )

        closed_count = 0
        for i in range(len(all_positions)):
            if closed_count >= positions_to_close:
                break

            strategy_id, ticker, entry_time = all_positions[i]
            engine = self.strategies[strategy_id]
            strategy_name = self.strategy_names.get(strategy_id, strategy_id[:8])

            # Skip if there's already a sell order for this ticker
            if ticker in existing_sell_tickers:
                logger.info(f"[{ticker}] Already has pending sell order, skipping new sell")
                closed_count += 1  # Count as "being closed"
                # Remove from active trades and unsubscribe (no need for quotes on pending sells)
                trade = engine.active_trades.pop(ticker, None)
                if trade:
                    logger.info(f"[{ticker}] Removed from active_trades (pending sell at broker)")
                self._on_unsubscribe(ticker, strategy_id)
                continue

            trade = engine.active_trades.get(ticker)
            if trade:
                # Get current price for exit (use entry price as fallback)
                current_price = trade.last_price if trade.last_price > 0 else trade.entry_price

                logger.warning(
                    f"[{ticker}] Closing position (LIFO limit) from strategy '{strategy_name}' - "
                    f"entered at {entry_time.strftime('%H:%M:%S')}"
                )

                # Trigger exit via the strategy engine
                engine._execute_exit(ticker, current_price, "position_limit", datetime.now())
                closed_count += 1

    def _on_subscribe(self, ticker: str, strategy_id: str):
        """Callback when a strategy needs quotes for a ticker."""
        # Track which strategy subscribed
        if strategy_id not in self._strategy_subscriptions:
            self._strategy_subscriptions[strategy_id] = set()
        self._strategy_subscriptions[strategy_id].add(ticker)

        # Log subscription state
        all_strategy_subs = set()
        for subs in self._strategy_subscriptions.values():
            all_strategy_subs.update(subs)
        logger.debug(f"[{ticker}] Subscribe requested by strategy, all tracked: {all_strategy_subs}")

        if self.quote_provider:
            # Check if already subscribed via quote provider
            current_ws_subs = self.quote_provider.subscribed_tickers
            if ticker not in current_ws_subs:
                # Check if this subscription would exceed limits
                if len(current_ws_subs) >= self.quote_provider.max_subscriptions:
                    logger.warning(f"[{ticker}] Subscription queued - would exceed limit of {self.quote_provider.max_subscriptions} symbols (current: {current_ws_subs}). Will retry during reconciliation.")
                    # Keep in strategy subscriptions for later retry during reconciliation
                    # Don't return - let the strategy keep tracking this ticker
                else:
                    self.quote_provider.subscribe_sync(ticker)
                    logger.info(f"[{ticker}] Added to WS subscriptions: {self.quote_provider.subscribed_tickers}")
                    if self._loop and self._loop.is_running():
                        logger.info(f"Scheduling async subscribe for {ticker}")
                        self._loop.call_soon_threadsafe(
                            lambda t=ticker: asyncio.create_task(self.quote_provider.subscribe(t))
                        )
                    else:
                        logger.warning(f"[{ticker}] Event loop not running - subscription queued (will send on WS connect)")
            else:
                logger.debug(f"[{ticker}] Already in WS subscriptions")

    def _on_unsubscribe(self, ticker: str, strategy_id: str):
        """Callback when a strategy no longer needs quotes for a ticker."""
        strategy_name = self.strategy_names.get(strategy_id, strategy_id[:8])

        # Release the ticker lock if this strategy owned it
        if self._locked_tickers.get(ticker) == strategy_id:
            del self._locked_tickers[ticker]
            logger.info(f"[{ticker}] Lock released by '{strategy_name}'")

        # Remove from this strategy's subscriptions
        if strategy_id in self._strategy_subscriptions:
            self._strategy_subscriptions[strategy_id].discard(ticker)

        # Log current subscription state across all strategies
        all_strategy_subs = set()
        for subs in self._strategy_subscriptions.values():
            all_strategy_subs.update(subs)
        logger.info(f"[{ticker}] Unsubscribe requested by {strategy_name}, all tracked: {all_strategy_subs}")

        # Check if any other strategy still needs this ticker
        still_needed = ticker in all_strategy_subs

        if still_needed:
            logger.debug(f"[{ticker}] Still needed by another strategy - keeping WS subscription")
            return

        # Only unsubscribe from WebSocket if no strategy needs it
        if self.quote_provider:
            current_ws_subs = self.quote_provider.subscribed_tickers
            logger.info(f"[{ticker}] No longer needed by any strategy, removing from WS. Current WS subs: {current_ws_subs}")

            # Use async unsubscribe - it handles both removing from set and sending to WebSocket
            # Don't call unsubscribe_sync first, as that would cause the async version to skip
            # updating the WebSocket (it checks if ticker is still in subscriptions)
            if self._loop:
                self._loop.call_soon_threadsafe(
                    lambda t=ticker: asyncio.create_task(self.quote_provider.unsubscribe(t))
                )
            else:
                # Fallback: at least remove from local tracking
                logger.warning(f"[{ticker}] Event loop not running - using sync unsubscribe")
                self.quote_provider.unsubscribe_sync(ticker)

            # Try to fulfill any pending subscriptions now that a slot is freed
            self._try_fulfill_pending_subscriptions()

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
            # Still reconcile subscriptions even with no strategies
            self._reconcile_subscriptions()
            return

        # Fetch positions once for all strategies
        broker_positions = None
        try:
            positions = self.trader.get_positions()
            broker_positions = {p.ticker: p for p in positions}
            logger.debug(f"Reconciliation: fetched {len(broker_positions)} positions from broker")
        except Exception as e:
            if "429" in str(e):
                logger.warning("Rate limited by broker - skipping position reconciliation (will still reconcile subscriptions)")
            else:
                logger.error(f"Failed to fetch positions for reconciliation: {e}")

        # Pass pre-fetched positions to each strategy (if we got them)
        if broker_positions is not None:
            for strategy_id, engine in self.strategies.items():
                try:
                    engine.reconcile_positions(broker_positions)
                except Exception as e:
                    logger.error(f"Reconciliation failed for strategy {strategy_id}: {e}")

        # Always reconcile subscriptions - this doesn't require broker API calls
        # and catches drift even when we can't check broker positions
        self._reconcile_subscriptions()

    def _reconcile_subscriptions(self):
        """Ensure WebSocket subscriptions match what strategies actually need.

        Handles oversubscription by prioritizing active trades over pending entries,
        and fulfilling as many strategy requests as possible within limits.
        """
        if not self.quote_provider:
            return

        # What tickers do strategies want? (from strategy subscriptions)
        wanted_tickers: set = set()
        for strategy_subs in self._strategy_subscriptions.values():
            wanted_tickers.update(strategy_subs)

        # What tickers are actually needed for trading? (active + pending)
        needed_for_trading: set = set()
        active_trade_tickers = set()
        pending_entry_tickers = set()

        for strategy_id, engine in self.strategies.items():
            active_trade_tickers.update(engine.active_trades.keys())
            pending_entry_tickers.update(engine.pending_entries.keys())
            needed_for_trading.update(engine.active_trades.keys())
            needed_for_trading.update(engine.pending_entries.keys())

        # What does the quote provider think it's subscribed to?
        current_subs = self.quote_provider.subscribed_tickers

        # Find extras (subscribed but not wanted by any strategy)
        extras = current_subs - wanted_tickers
        if extras:
            logger.warning(f"Subscription cleanup - unsubscribing from unwanted tickers: {extras}")
            for ticker in extras:
                if self._loop:
                    self._loop.call_soon_threadsafe(
                        lambda t=ticker: asyncio.create_task(self.quote_provider.unsubscribe(t))
                    )

        # Find missing (wanted but not subscribed)
        missing = wanted_tickers - current_subs
        if missing:
            current_count = len(current_subs)
            max_allowed = self.quote_provider.max_subscriptions
            available_slots = max_allowed - current_count

            if available_slots <= 0:
                logger.warning(f"At subscription limit ({current_count}/{max_allowed}). {len(missing)} tickers wanted but cannot subscribe: {missing}")
                return

            # Prioritize subscriptions:
            # 1. Active trades (highest priority - critical for position management)
            # 2. Pending entries (medium priority - for new positions)
            # 3. Other wanted tickers (lowest priority - general monitoring)
            prioritized_missing = []

            for ticker in missing:
                if ticker in active_trade_tickers:
                    prioritized_missing.insert(0, ticker)  # Front of list (highest priority)
                elif ticker in pending_entry_tickers:
                    # Insert after active trades but before general wanted
                    insert_pos = 0
                    while insert_pos < len(prioritized_missing) and prioritized_missing[insert_pos] in active_trade_tickers:
                        insert_pos += 1
                    prioritized_missing.insert(insert_pos, ticker)
                else:
                    prioritized_missing.append(ticker)  # End of list (lowest priority)

            # Subscribe to as many as we can within the limit
            to_subscribe = prioritized_missing[:available_slots]

            logger.info(f"Fulfilling {len(to_subscribe)}/{len(missing)} wanted subscriptions (prioritized): {to_subscribe}")

            for ticker in to_subscribe:
                if self._loop:
                    self._loop.call_soon_threadsafe(
                        lambda t=ticker: asyncio.create_task(self.quote_provider.subscribe(t))
                    )

            # Log what we couldn't subscribe to due to limits
            skipped = len(missing) - len(to_subscribe)
            if skipped > 0:
                skipped_tickers = prioritized_missing[available_slots:]
                active_skipped = [t for t in skipped_tickers if t in active_trade_tickers]
                pending_skipped = [t for t in skipped_tickers if t in pending_entry_tickers]
                other_skipped = [t for t in skipped_tickers if t not in active_trade_tickers and t not in pending_entry_tickers]

                logger.warning(f"Oversubscribed - skipped {skipped} subscriptions due to {max_allowed} symbol limit:")
                if active_skipped:
                    logger.warning(f"  Active trades skipped: {active_skipped}")
                if pending_skipped:
                    logger.warning(f"  Pending entries skipped: {pending_skipped}")
                if other_skipped:
                    logger.warning(f"  Other subscriptions skipped: {other_skipped}")

        if not extras and not missing:
            logger.debug(f"Subscriptions OK: {len(current_subs)}/{self.quote_provider.max_subscriptions} tickers")

        # Log oversubscription status
        wanted_count = len(wanted_tickers)
        subscribed_count = len(current_subs)
        if wanted_count > subscribed_count:
            logger.info(f"Oversubscribed: {wanted_count} tickers wanted, {subscribed_count}/{self.quote_provider.max_subscriptions} subscribed")

    def _try_fulfill_pending_subscriptions(self):
        """Try to subscribe to any wanted tickers that couldn't be subscribed due to limits.

        Called when subscriptions are freed up (e.g., when strategies complete trades).
        """
        if not self.quote_provider:
            return

        wanted_tickers = set()
        for strategy_subs in self._strategy_subscriptions.values():
            wanted_tickers.update(strategy_subs)

        current_subs = self.quote_provider.subscribed_tickers
        missing = wanted_tickers - current_subs

        if missing:
            current_count = len(current_subs)
            max_allowed = self.quote_provider.max_subscriptions
            available_slots = max_allowed - current_count

            if available_slots > 0:
                logger.info(f"Trying to fulfill {min(len(missing), available_slots)} pending subscriptions from freed slots")

                # Prioritize same way as reconciliation
                active_trade_tickers = set()
                pending_entry_tickers = set()

                for strategy_id, engine in self.strategies.items():
                    active_trade_tickers.update(engine.active_trades.keys())
                    pending_entry_tickers.update(engine.pending_entries.keys())

                prioritized_missing = []
                for ticker in missing:
                    if ticker in active_trade_tickers:
                        prioritized_missing.insert(0, ticker)
                    elif ticker in pending_entry_tickers:
                        insert_pos = 0
                        while insert_pos < len(prioritized_missing) and prioritized_missing[insert_pos] in active_trade_tickers:
                            insert_pos += 1
                        prioritized_missing.insert(insert_pos, ticker)
                    else:
                        prioritized_missing.append(ticker)

                to_subscribe = prioritized_missing[:available_slots]

                for ticker in to_subscribe:
                    if self._loop:
                        self._loop.call_soon_threadsafe(
                            lambda t=ticker: asyncio.create_task(self.quote_provider.subscribe(t))
                        )

                if to_subscribe:
                    logger.info(f"Fulfilled {len(to_subscribe)} pending subscriptions: {to_subscribe}")

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
        status["orphaned_tickers"] = list(self._orphaned_tickers)

        if self.trader:
            # Use cached values if fresh, otherwise fetch from Alpaca
            now = time.time()
            cache_expired = (now - self._cache_time) > self._cache_ttl

            if cache_expired:
                # Refresh cache
                try:
                    self._cached_account = self.trader.get_account_info()
                except Exception as e:
                    logger.debug(f"Failed to fetch account info: {e}")
                    # Keep old cache on error

                try:
                    self._cached_orders = self.trader.get_open_orders()
                except Exception as e:
                    logger.debug(f"Failed to fetch open orders: {e}")
                    # Keep old cache on error

                self._cache_time = now

            # Use cached values
            if self._cached_account:
                status["account"] = {
                    "equity": self._cached_account.get("equity", 0),
                    "buying_power": self._cached_account.get("buying_power", 0),
                }

            if self._cached_orders is not None:
                status["open_orders"] = [
                    {
                        "order_id": o.order_id,
                        "ticker": o.ticker,
                        "side": o.side,
                        "shares": o.shares,
                        "status": o.status,
                    }
                    for o in self._cached_orders
                ]
            else:
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
    """Enable a strategy for live trading (hot-reload supported)."""
    global _trading_engine

    store = get_strategy_store()

    # Update database
    if not store.set_enabled(strategy_id, True):
        logger.error(f"Strategy {strategy_id} not found")
        return False

    # If engine is running, add the strategy dynamically (no restart needed)
    if _trading_engine and _trading_engine.is_running:
        strategy = store.get_strategy(strategy_id)
        if strategy:
            _trading_engine.add_strategy(strategy.id, strategy.name, strategy.config, strategy.priority)
            logger.info(f"Hot-reloaded strategy '{strategy.name}' (enabled)")

    return True


def _exit_strategy_positions(trades, trader, context="log"):
    """Helper to exit positions for trades. Context can be 'log' or 'ui'."""
    results = []
    for trade in trades:
        try:
            order = trader.sell(trade.ticker, trade.shares)
            msg = f"Sold {trade.ticker}: {order.status}"
            results.append((trade.ticker, msg, None))
        except Exception as e:
            msg = f"Failed to sell {trade.ticker}: {e}"
            results.append((trade.ticker, None, msg))
    return results


def disable_strategy(strategy_id: str) -> bool:
    """Disable a strategy from live trading (hot-reload supported)."""
    global _trading_engine

    store = get_strategy_store()
    strategy = store.get_strategy(strategy_id)
    strategy_name = strategy.name if strategy else strategy_id

    # Exit all positions for this strategy before disabling
    if strategy and strategy.enabled:
        logger.info(f"Exiting positions for strategy '{strategy_name}' before disabling...")
        from .trading import get_trading_client
        from .active_trade_store import get_active_trade_store

        trader = get_trading_client(paper=_trading_engine.paper if _trading_engine else True)
        active_store = get_active_trade_store()
        active_trades = active_store.get_trades_for_strategy(strategy_id)

        if active_trades:
            results = _exit_strategy_positions(active_trades, trader, context="log")
            for ticker, success_msg, error_msg in results:
                if success_msg:
                    logger.info(success_msg)
                if error_msg:
                    logger.error(error_msg)

    # Update database
    if not store.set_enabled(strategy_id, False):
        logger.error(f"Strategy {strategy_id} not found")
        return False

    # If engine is running, remove the strategy dynamically (no restart needed)
    if _trading_engine and _trading_engine.is_running:
        _trading_engine.remove_strategy(strategy_id)
        logger.info(f"Hot-reloaded strategy '{strategy_name}' (disabled)")

    return True


def exit_all_positions(paper: bool = True) -> Dict[str, str]:
    """
    Exit all open positions at the broker.

    Args:
        paper: Use paper trading (default True)

    Returns:
        Dict mapping ticker to result ("sold", "failed: reason", etc.)
    """
    from .trading import get_trading_client

    results = {}
    trader = get_trading_client(paper=paper)

    # Get all positions from broker
    positions = trader.get_positions()
    if not positions:
        logger.info("No positions to exit")
        return results

    logger.warning(f"Exiting {len(positions)} positions...")

    # Cancel all open orders first to free up shares
    try:
        canceled = trader.cancel_all_orders()
        logger.info(f"Canceled {canceled} open orders")
    except Exception as e:
        logger.error(f"Failed to cancel orders: {e}")

    # Submit sell orders for each position
    for pos in positions:
        ticker = pos.ticker
        shares = pos.shares
        try:
            # Calculate current price from market_value, fall back to entry price
            if pos.market_value > 0 and shares > 0:
                price = pos.market_value / shares
            else:
                price = pos.avg_entry_price
            order = trader.sell(ticker, shares, limit_price=price)
            results[ticker] = f"sell order submitted ({order.status})"
            logger.info(f"[{ticker}] Submitted sell for {shares} shares @ ${price:.2f}")
        except Exception as e:
            results[ticker] = f"failed: {e}"
            logger.error(f"[{ticker}] Failed to sell: {e}")

    return results
