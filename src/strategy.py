"""Strategy engine for live trading."""

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, parse_qs

from .models import Announcement
from .trading import TradingClient, Position
from .trade_store import get_trade_store
from .active_trade_store import get_active_trade_store
from .pending_entry_store import get_pending_entry_store
from .order_store import get_order_store
from .orphaned_order_store import get_orphaned_order_store
from .trade_logger import log_buy_fill, log_sell_fill
from .postgres_client import get_postgres_client
from .trace_store import get_trace_store

logger = logging.getLogger(__name__)
# Separate logger for verbose volume/candle logs - writes to logs/volume.log
quotes_logger = logging.getLogger(__name__ + '.quotes')
# Separate logger for real-time status updates - writes to logs/prices.log (not stdout)
status_logger = logging.getLogger(__name__ + '.status')


@dataclass
class StrategyConfig:
    """Configuration for a trading strategy."""

    # Filters (which alerts to trade)
    channels: List[str] = field(default_factory=lambda: ["select-news"])
    directions: List[str] = field(default_factory=lambda: ["up_right"])
    price_min: float = 1.0
    price_max: float = 10.0
    sessions: List[str] = field(default_factory=lambda: ["premarket", "market"])
    country_blacklist: List[str] = field(default_factory=list)  # e.g., ["CN", "IL"]
    max_intraday_mentions: Optional[int] = None  # Max intraday mentions (e.g., 2 = only if < 2 mentions)
    exclude_financing_headlines: bool = False  # Exclude offerings, reverse splits, etc.
    exclude_biotech: bool = False  # Exclude therapeutics, clinical, trial, phase, fda, drug, treatment
    max_prior_move_pct: Optional[float] = None  # Skip if scanner_gain_pct > this (e.g., 40 = skip if already moved 40%+)
    max_market_cap_millions: Optional[float] = None  # Skip if market cap > this (e.g., 50 = skip if > $50M)

    # Entry rules
    consec_green_candles: int = 1
    min_candle_volume: int = 5000
    entry_window_minutes: int = 5  # How long to wait for entry conditions after alert
    buy_order_timeout_seconds: int = int(os.getenv("BUY_ORDER_TIMEOUT_SECONDS", "5"))  # Cancel unfilled buy orders (includes orphaned orders)

    # Exit rules
    sell_order_timeout_seconds: int = 5  # Cancel and retry unfilled sell orders after this many seconds
    take_profit_pct: float = 10.0
    stop_loss_pct: float = 11.0
    stop_loss_from_open: bool = True
    trailing_stop_pct: float = 7.0
    timeout_minutes: int = 15  # How long to hold position before timeout exit

    # Position sizing
    stake_mode: str = "fixed"  # "fixed" or "volume_pct"
    stake_amount: float = 50.0  # Dollar amount for fixed stake
    volume_pct: float = 1.0  # Percentage of prev candle volume (e.g., 1.0 = 1%)
    max_stake: float = 10000.0  # Max dollar amount for volume-based sizing

    def __post_init__(self):
        """Validate and clamp config parameters to reasonable ranges."""
        # Price range validation
        if self.price_min < 0:
            logger.warning(f"price_min={self.price_min} is negative, setting to 0")
            self.price_min = 0
        if self.price_max < self.price_min:
            logger.warning(f"price_max={self.price_max} < price_min={self.price_min}, swapping")
            self.price_min, self.price_max = self.price_max, self.price_min
        if self.price_max > 100000:
            logger.warning(f"price_max={self.price_max} is unrealistic, capping at 100000")
            self.price_max = 100000

        # Stop loss validation (must be positive)
        if self.stop_loss_pct <= 0:
            logger.warning(f"stop_loss_pct={self.stop_loss_pct} must be positive, setting to 5%")
            self.stop_loss_pct = 5.0
        if self.stop_loss_pct > 100:
            logger.warning(f"stop_loss_pct={self.stop_loss_pct} > 100%, capping at 100%")
            self.stop_loss_pct = 100.0

        # Take profit validation
        if self.take_profit_pct <= 0:
            logger.warning(f"take_profit_pct={self.take_profit_pct} must be positive, setting to 10%")
            self.take_profit_pct = 10.0
        if self.take_profit_pct > 1000:
            logger.warning(f"take_profit_pct={self.take_profit_pct} > 1000%, capping at 1000%")
            self.take_profit_pct = 1000.0

        # Trailing stop validation (0 = disabled, must not be negative)
        if self.trailing_stop_pct < 0:
            logger.warning(f"trailing_stop_pct={self.trailing_stop_pct} is negative, setting to 0 (disabled)")
            self.trailing_stop_pct = 0.0
        if self.trailing_stop_pct > 100:
            logger.warning(f"trailing_stop_pct={self.trailing_stop_pct} > 100%, capping at 100%")
            self.trailing_stop_pct = 100.0

        # Entry window validation
        if self.entry_window_minutes <= 0:
            logger.warning(f"entry_window_minutes={self.entry_window_minutes} must be positive, setting to 1")
            self.entry_window_minutes = 1
        if self.entry_window_minutes > 1440:  # Max 24 hours
            logger.warning(f"entry_window_minutes={self.entry_window_minutes} > 24h, capping at 1440")
            self.entry_window_minutes = 1440

        # Timeout validation
        if self.timeout_minutes <= 0:
            logger.warning(f"timeout_minutes={self.timeout_minutes} must be positive, setting to 1")
            self.timeout_minutes = 1
        if self.timeout_minutes > 1440:
            logger.warning(f"timeout_minutes={self.timeout_minutes} > 24h, capping at 1440")
            self.timeout_minutes = 1440

        # Stake validation
        if self.stake_amount <= 0:
            logger.warning(f"stake_amount={self.stake_amount} must be positive, setting to 50")
            self.stake_amount = 50.0
        if self.max_stake <= 0:
            logger.warning(f"max_stake={self.max_stake} must be positive, setting to 10000")
            self.max_stake = 10000.0

        # Volume percent validation
        if self.volume_pct < 0:
            logger.warning(f"volume_pct={self.volume_pct} is negative, setting to 0")
            self.volume_pct = 0.0
        if self.volume_pct > 100:
            logger.warning(f"volume_pct={self.volume_pct} > 100%, capping at 100%")
            self.volume_pct = 100.0

        # Stake mode validation
        if self.stake_mode not in ("fixed", "volume_pct"):
            logger.warning(f"stake_mode={self.stake_mode} is invalid, setting to 'fixed'")
            self.stake_mode = "fixed"

        # Min candle volume validation
        if self.min_candle_volume < 0:
            self.min_candle_volume = 0

        # Consecutive green candles validation
        if self.consec_green_candles < 0:
            self.consec_green_candles = 0

    def get_shares(self, price: float, prev_candle_volume: Optional[int] = None) -> int:
        """
        Calculate number of shares based on stake mode and price.

        Args:
            price: Entry price per share
            prev_candle_volume: Volume of the previous candle (for volume_pct mode)

        Returns:
            Number of shares to buy
        """
        if price <= 0:
            return 0

        if self.stake_mode == "volume_pct" and prev_candle_volume is not None:
            # Volume-based sizing: buy volume_pct% of prev candle volume
            shares_from_volume = int(prev_candle_volume * self.volume_pct / 100)
            # Cap by max_stake
            max_shares = int(self.max_stake / price)
            shares = min(shares_from_volume, max_shares)
            return max(1, shares) if shares > 0 else 0
        else:
            # Fixed stake mode (default)
            return max(1, int(self.stake_amount / price))

    @classmethod
    def from_url_params(cls, url_or_params) -> "StrategyConfig":
        """
        Parse dashboard URL params into config.

        Args:
            url_or_params: Either a URL string or a dict of params
        """
        if isinstance(url_or_params, str):
            parsed = urlparse(url_or_params)
            params = parse_qs(parsed.query)
            # parse_qs returns lists, extract first value
            params = {k: v[0] if v else "" for k, v in params.items()}
        else:
            params = url_or_params

        def parse_list(val: str) -> List[str]:
            if not val:
                return []
            return [v.strip() for v in val.split(",") if v.strip()]

        return cls(
            channels=parse_list(params.get("channel", "")),
            directions=parse_list(params.get("direction", "")),
            price_min=float(params.get("price_min", 0)),
            price_max=float(params.get("price_max", 100)),
            sessions=parse_list(params.get("sess", "premarket,market")),
            consec_green_candles=int(params.get("consec", 0)),
            min_candle_volume=int(params.get("min_vol", 0)),
            entry_window_minutes=int(params.get("entry_window", 5)),
            take_profit_pct=float(params.get("tp", 10)),
            stop_loss_pct=float(params.get("sl", 5)),
            stop_loss_from_open=params.get("sl_open", "0") == "1",
            trailing_stop_pct=float(params.get("trail", 0)),
            timeout_minutes=int(params.get("hold", 60)),
            stake_mode=params.get("stake_mode", "fixed"),
            stake_amount=float(params.get("stake", 50)),
            volume_pct=float(params.get("vol_pct", 1.0)),
            max_stake=float(params.get("max_stake", 10000)),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "filters": {
                "channels": self.channels,
                "directions": self.directions,
                "price_min": self.price_min,
                "price_max": self.price_max,
                "sessions": self.sessions,
                "country_blacklist": self.country_blacklist,
                "max_intraday_mentions": self.max_intraday_mentions,
                "exclude_financing_headlines": self.exclude_financing_headlines,
                "exclude_biotech": self.exclude_biotech,
                "max_prior_move_pct": self.max_prior_move_pct,
                "max_market_cap_millions": self.max_market_cap_millions,
            },
            "entry": {
                "consec_green_candles": self.consec_green_candles,
                "min_candle_volume": self.min_candle_volume,
                "entry_window_minutes": self.entry_window_minutes,
            },
            "exit": {
                "take_profit_pct": self.take_profit_pct,
                "stop_loss_pct": self.stop_loss_pct,
                "stop_loss_from_open": self.stop_loss_from_open,
                "trailing_stop_pct": self.trailing_stop_pct,
                "timeout_minutes": self.timeout_minutes,
            },
            "position": {
                "stake_mode": self.stake_mode,
                "stake_amount": self.stake_amount,
                "volume_pct": self.volume_pct,
                "max_stake": self.max_stake,
            },
        }


@dataclass
class CandleBar:
    """Represents a candle bar built from tick data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def is_green(self) -> bool:
        return self.close > self.open


@dataclass
class PendingEntry:
    """Tracks a potential entry waiting for conditions."""
    trade_id: str  # Unique identifier (UUID)
    ticker: str
    announcement: Announcement
    alert_time: datetime
    first_price: Optional[float] = None
    current_candle_start: Optional[datetime] = None
    current_candle_data: Optional[dict] = None  # Building current candle
    trace_id: Optional[str] = None  # Link to trace for event logging


@dataclass
class ActiveTrade:
    """Tracks an active trade position."""
    trade_id: str  # Unique identifier (UUID)
    ticker: str
    announcement: Announcement
    entry_price: float
    entry_time: datetime
    first_candle_open: float
    shares: int
    highest_since_entry: float
    stop_loss_price: float
    take_profit_price: float
    last_price: float = 0.0  # Updated by quotes
    last_quote_time: Optional[datetime] = None
    sell_attempts: int = 0  # Track failed sell attempts
    needs_manual_exit: bool = False  # True after 3 failed sell attempts
    trace_id: Optional[str] = None  # Link to trace for event logging


@dataclass
class PendingOrder:
    """Tracks an order waiting for fill confirmation."""
    order_id: str
    ticker: str
    side: str  # "buy" or "sell"
    shares: int
    limit_price: float
    submitted_at: datetime
    trade_id: str  # Links to PendingEntry (buy) or ActiveTrade (sell)
    # Database order ID for tracking
    db_order_id: Optional[int] = None
    # Context for creating ActiveTrade on fill (buy orders)
    announcement: Optional[Announcement] = None
    first_candle_open: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    # Entry trigger details (for logging)
    entry_trigger: Optional[str] = None  # e.g., "early_entry", "completed_candles", "no_candle_req"
    # Trace tracking
    trace_id: Optional[str] = None  # Link to trace for event logging
    sizing_info: Optional[str] = None  # e.g., "1.0% of 50,000 vol = 26 shares ($76)"
    # Context for recording trade on fill (sell orders)
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    exit_reason: Optional[str] = None  # e.g., "take_profit", "stop_loss", "timeout"


class StrategyEngine:
    """
    Executes trading strategy based on alerts and price updates.

    Flow:
    1. on_alert() - New alert arrives, check filters, add to pending
    2. on_quote() - Price update, check entry/exit conditions
    """

    def __init__(
        self,
        config: StrategyConfig,
        trader: TradingClient,
        on_subscribe: Optional[Callable[[str], bool]] = None,
        on_unsubscribe: Optional[Callable[[str], None]] = None,
        on_fetch_price: Optional[Callable[[str], Optional[float]]] = None,
        paper: bool = True,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ):
        self.config = config
        self.trader = trader
        self.on_subscribe = on_subscribe  # Called when we need quotes for a ticker (returns True if subscribed)
        self.on_unsubscribe = on_unsubscribe  # Called when done with a ticker
        self.on_fetch_price = on_fetch_price  # Called to fetch current price from REST API
        self.paper = paper
        self.strategy_id = strategy_id
        self.strategy_name = strategy_name or "default"

        # Keyed by trade_id (UUID) - allows multiple positions per ticker
        # Protected by _state_lock for thread-safe access from multiple threads
        self.pending_entries: Dict[str, PendingEntry] = {}  # trade_id -> PendingEntry
        self.pending_orders: Dict[str, PendingOrder] = {}   # order_id -> PendingOrder
        self.active_trades: Dict[str, ActiveTrade] = {}     # trade_id -> ActiveTrade
        self.completed_trades: List[dict] = []

        # Lock for thread-safe access to state dicts (pending_entries, pending_orders, active_trades)
        # Accessed from: asyncio event loop (quotes), trading callbacks (order fills), HTTP thread (Streamlit)
        self._state_lock = threading.RLock()

        # Shared candle data per ticker (not per pending entry)
        self._ticker_candles: Dict[str, List[CandleBar]] = {}  # ticker -> completed candles
        self._ticker_building_candle: Dict[str, dict] = {}  # ticker -> current candle being built
        self._ticker_candle_start: Dict[str, datetime] = {}  # ticker -> start time of building candle

        # Trade history persistence
        self._trade_store = get_trade_store()
        self._active_trade_store = get_active_trade_store()
        self._order_store = get_order_store()

        # Recover any open positions from database and broker
        self._recover_positions()

    # ========== Thread-safe accessors for external callers (e.g., Streamlit) ==========

    def get_active_trades_snapshot(self) -> Dict[str, "ActiveTrade"]:
        """
        Return a thread-safe copy of active_trades.

        Use this from external threads (like Streamlit) to avoid race conditions.
        """
        with self._state_lock:
            return dict(self.active_trades)

    def get_pending_entries_snapshot(self) -> Dict[str, "PendingEntry"]:
        """Return a thread-safe copy of pending_entries."""
        with self._state_lock:
            return dict(self.pending_entries)

    def get_pending_orders_snapshot(self) -> Dict[str, "PendingOrder"]:
        """Return a thread-safe copy of pending_orders."""
        with self._state_lock:
            return dict(self.pending_orders)

    def get_state_summary(self) -> dict:
        """
        Return a thread-safe summary of current state.

        Useful for status displays without exposing mutable state.
        """
        with self._state_lock:
            return {
                "active_trades": len(self.active_trades),
                "pending_entries": len(self.pending_entries),
                "pending_orders": len(self.pending_orders),
                "completed_trades": len(self.completed_trades),
                "active_tickers": list({t.ticker for t in self.active_trades.values()}),
                "pending_tickers": list({e.ticker for e in self.pending_entries.values()}),
            }

    def _recover_positions(self):
        """Recover open positions from database and broker on startup."""
        # First, load from our database (has accurate entry times, SL/TP)
        logger.info(f"[{self.strategy_name}] Loading active trades from database...")
        try:
            db_trades = self._active_trade_store.get_trades_for_strategy(self.strategy_id)
            logger.info(f"[{self.strategy_name}] Database returned {len(db_trades)} active trades")

            for t in db_trades:
                # Generate trade_id if not present (migration from old data)
                trade_id = t.trade_id if t.trade_id else str(uuid.uuid4())
                if not t.trade_id:
                    logger.info(f"[{self.strategy_name}] [{t.ticker}] Generated trade_id for legacy trade: {trade_id[:8]}")

                self.active_trades[trade_id] = ActiveTrade(
                    trade_id=trade_id,
                    ticker=t.ticker,
                    announcement=None,  # Lost on restart
                    entry_price=t.entry_price,
                    entry_time=t.entry_time,
                    first_candle_open=t.first_candle_open,
                    shares=t.shares,
                    highest_since_entry=t.highest_since_entry,
                    stop_loss_price=t.stop_loss_price,
                    take_profit_price=t.take_profit_price,
                    last_price=t.last_price or 0.0,
                    last_quote_time=t.last_quote_time,
                )
                logger.info(f"[{self.strategy_name}] [{t.ticker}] Recovered from DB: {t.shares} shares @ ${t.entry_price:.2f}, "
                           f"SL=${t.stop_loss_price:.2f}, TP=${t.take_profit_price:.2f} (trade_id={trade_id[:8]})")

                # Subscribe to quotes
                if self.on_subscribe:
                    subscribed = self.on_subscribe(t.ticker)
                    if not subscribed:
                        logger.warning(f"[{self.strategy_name}] [{t.ticker}] Could not subscribe for quotes (at limit) - position recovered but won't get live updates until slot available")

        except Exception as e:
            logger.error(f"[{self.strategy_name}] Failed to load from database: {e}", exc_info=True)

        # Recover pending entries from database
        logger.info(f"[{self.strategy_name}] Loading pending entries from database...")
        try:
            pending_store = get_pending_entry_store()
            db_entries = pending_store.get_entries_for_strategy(self.strategy_id)
            logger.info(f"[{self.strategy_name}] Database returned {len(db_entries)} pending entries")

            postgres_client = get_postgres_client()
            cfg = self.config
            recovered_count = 0
            expired_count = 0

            for e in db_entries:
                # Check if entry window has expired
                time_since_alert = (datetime.now() - e.alert_time).total_seconds() / 60
                if time_since_alert > cfg.entry_window_minutes:
                    logger.info(f"[{self.strategy_name}] [{e.ticker}] Pending entry expired during restart ({time_since_alert:.1f}m > {cfg.entry_window_minutes}m window) - removing")
                    pending_store.delete_entry(e.trade_id)
                    expired_count += 1
                    continue

                # Reconstruct announcement from database
                announcement = postgres_client.get_announcement(e.announcement_ticker, e.announcement_timestamp)
                if not announcement:
                    logger.warning(f"[{self.strategy_name}] [{e.ticker}] Could not find announcement for pending entry - creating minimal announcement")
                    # Create minimal announcement for recovery
                    announcement = Announcement(
                        ticker=e.announcement_ticker,
                        timestamp=e.announcement_timestamp,
                        price_threshold=0.0,
                        headline="Recovered from restart",
                        country="US",
                        channel=None,
                        direction=None,
                    )

                self.pending_entries[e.trade_id] = PendingEntry(
                    trade_id=e.trade_id,
                    ticker=e.ticker,
                    announcement=announcement,
                    alert_time=e.alert_time,
                    first_price=e.first_price,
                )
                recovered_count += 1
                remaining_window = cfg.entry_window_minutes - time_since_alert
                logger.info(f"[{self.strategy_name}] [{e.ticker}] Recovered pending entry (trade_id={e.trade_id[:8]}, {remaining_window:.1f}m remaining in entry window)")

                # Subscribe to quotes (if not already subscribed via active trades)
                if not self._get_trades_for_ticker(e.ticker) and self.on_subscribe:
                    subscribed = self.on_subscribe(e.ticker)
                    if not subscribed:
                        logger.warning(f"[{self.strategy_name}] [{e.ticker}] Could not subscribe for quotes (at limit) - pending entry recovered but won't get live updates")

            if expired_count > 0:
                logger.info(f"[{self.strategy_name}] Removed {expired_count} expired pending entries")

        except Exception as e:
            logger.error(f"[{self.strategy_name}] Failed to load pending entries from database: {e}", exc_info=True)

        # Verify our positions still exist at broker (positions may have been manually closed)
        # NOTE: We do NOT auto-claim broker positions. A strategy only owns positions that
        # were explicitly created through its entry flow (on_alert -> on_buy_fill).
        # This prevents all strategies from claiming all broker positions.
        logger.info(f"[{self.strategy_name}] Verifying positions with broker...")
        try:
            positions = self.trader.get_positions()
            broker_tickers = {p.ticker for p in positions}
            logger.info(f"[{self.strategy_name}] Broker has positions in: {broker_tickers}")

            # Check for positions we track but broker doesn't have (manually closed)
            for trade_id, trade in self.active_trades.items():
                if trade.ticker not in broker_tickers:
                    logger.warning(f"[{self.strategy_name}] [{trade.ticker}] Position not found at broker - may have been manually closed (trade_id={trade_id[:8]})")

        except Exception as e:
            logger.error(f"[{self.strategy_name}] Failed to recover from broker: {e}", exc_info=True)

        # Also check for pending orders
        self._recover_pending_orders()

    def _recover_pending_orders(self):
        """
        Check for orphaned orders in broker that we're not tracking.

        Auto-cancels old orders based on config.orphaned_order_timeout_minutes.
        Logs warnings for untracked orders and stores them in the database.
        """
        try:
            orders = self.trader.get_open_orders()
            if not orders:
                return

            # Filter to only orders we're not already tracking
            untracked_orders = [
                order for order in orders
                if order.order_id not in self.pending_orders
            ]

            if not untracked_orders:
                return

            # Get the orphaned order store
            orphaned_store = get_orphaned_order_store()
            current_time = datetime.utcnow()
            timeout_seconds = self.config.buy_order_timeout_seconds

            logger.warning(
                f"[{self.strategy_name}] ⚠️  FOUND {len(untracked_orders)} UNTRACKED ORDERS IN BROKER ⚠️"
            )

            for order in untracked_orders:
                # Calculate order age
                age_str = "unknown age"
                should_cancel = False

                if order.created_at:
                    age = current_time - order.created_at
                    age_seconds = age.total_seconds()
                    age_str = f"{age_seconds:.1f}s old"

                    # Check if we should auto-cancel
                    if timeout_seconds > 0 and age_seconds > timeout_seconds:
                        should_cancel = True

                # Log warning
                price_str = f"${order.limit_price:.4f}" if order.limit_price else "$N/A"
                logger.warning(
                    f"[{self.strategy_name}] [{order.ticker}] "
                    f"🚨 Orphaned {order.side} order: {order.shares} shares "
                    f"@ {price_str} "
                    f"({order.status}, {age_str}, order_id={order.order_id})"
                )

                # Record to database
                orphaned_store.record_orphaned_order(
                    broker_order_id=order.order_id,
                    ticker=order.ticker,
                    side=order.side,
                    shares=order.shares,
                    order_type=order.order_type,
                    status=order.status,
                    limit_price=order.limit_price,
                    order_created_at=order.created_at,
                    strategy_name=self.strategy_name,
                    reason=f"Found untracked order ({age_str})",
                    paper=self.paper,
                )

                # Auto-cancel if too old
                if should_cancel:
                    logger.warning(
                        f"[{self.strategy_name}] [{order.ticker}] "
                        f"♻️  Auto-canceling order {order.order_id} "
                        f"(age {age_seconds:.1f}s > threshold {timeout_seconds}s)"
                    )
                    try:
                        if self.trader.cancel_order(order.order_id):
                            orphaned_store.mark_as_cancelled(
                                order.order_id,
                                reason=f"Auto-cancelled after {age_seconds:.1f}s (threshold: {timeout_seconds}s)"
                            )
                            logger.info(
                                f"[{self.strategy_name}] [{order.ticker}] "
                                f"✅ Successfully cancelled orphaned order {order.order_id}"
                            )
                    except Exception as e:
                        logger.error(
                            f"[{self.strategy_name}] [{order.ticker}] "
                            f"Failed to cancel orphaned order {order.order_id}: {e}"
                        )

            # Summary warning
            if any(order.created_at and (current_time - order.created_at).total_seconds() > timeout_seconds for order in untracked_orders if timeout_seconds > 0):
                logger.warning(
                    f"[{self.strategy_name}] ⚠️  Check your broker dashboard - orphaned orders detected!"
                )

        except Exception as e:
            logger.error(f"[{self.strategy_name}] Failed to check pending orders: {e}", exc_info=True)

    # --- Helper methods for multi-position support ---

    def _get_pending_for_ticker(self, ticker: str) -> List[PendingEntry]:
        """Get all pending entries for a ticker."""
        return [p for p in self.pending_entries.values() if p.ticker == ticker]

    def _get_trades_for_ticker(self, ticker: str) -> List[ActiveTrade]:
        """Get all active trades for a ticker."""
        return [t for t in self.active_trades.values() if t.ticker == ticker]

    def _has_pending_or_trade(self, ticker: str) -> bool:
        """Check if we have any pending entries, pending orders, or active trades for a ticker."""
        if self._get_pending_for_ticker(ticker) or self._get_trades_for_ticker(ticker):
            return True
        # Also check for pending orders (buy orders waiting to fill)
        for pending in self.pending_orders.values():
            if pending.ticker == ticker:
                return True
        return False

    def _get_candles_for_ticker(self, ticker: str) -> List[CandleBar]:
        """Get shared candle data for a ticker."""
        return self._ticker_candles.get(ticker, [])

    def _set_candles_for_ticker(self, ticker: str, candles: List[CandleBar]):
        """Set shared candle data for a ticker."""
        self._ticker_candles[ticker] = candles

    def _clear_candles_for_ticker(self, ticker: str):
        """Clear shared candle data when no longer tracking ticker."""
        self._ticker_candles.pop(ticker, None)

    def on_alert(self, announcement: Announcement, trace_id: Optional[str] = None) -> bool:
        """
        Handle new alert from Discord.

        Returns True if alert passes filters and is being tracked.
        Multiple alerts for the same ticker create independent pending entries.

        Args:
            announcement: The parsed announcement data
            trace_id: Optional trace ID for lifecycle tracking
        """
        ticker = announcement.ticker
        trace_store = get_trace_store() if trace_id else None

        # Check filters with reason tracking
        passes, rejection_reason = self._passes_filters_with_reason(announcement)
        if not passes:
            logger.info(f"[{self.strategy_name}] [{ticker}] Filtered: {rejection_reason}")
            if trace_store and trace_id:
                trace_store.add_event(
                    trace_id=trace_id,
                    event_type='filter_rejected',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    reason=rejection_reason,
                )
            return False

        # Record filter accepted
        if trace_store and trace_id:
            trace_store.add_event(
                trace_id=trace_id,
                event_type='filter_accepted',
                event_timestamp=datetime.utcnow(),
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
            )

        # Check if tradeable on broker before tracking
        tradeable, reason = self.trader.is_tradeable(ticker)
        if not tradeable:
            logger.warning(f"[{self.strategy_name}] [{ticker}] Not tradeable: {reason}")
            if trace_store and trace_id:
                trace_store.add_event(
                    trace_id=trace_id,
                    event_type='not_tradeable',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    reason=reason,
                )
            return False

        logger.info(f"[{self.strategy_name}] [{ticker}] Alert passed filters, checking subscription availability")

        # Request quote subscription only if not already subscribed for this ticker
        already_tracking = self._has_pending_or_trade(ticker)
        if not already_tracking and self.on_subscribe:
            subscribed = self.on_subscribe(ticker)
            if not subscribed:
                logger.warning(f"[{self.strategy_name}] [{ticker}] Cannot subscribe for quotes (at websocket limit) - rejecting alert")
                if trace_store and trace_id:
                    trace_store.add_event(
                        trace_id=trace_id,
                        event_type='subscription_failed',
                        event_timestamp=datetime.utcnow(),
                        strategy_id=self.strategy_id,
                        strategy_name=self.strategy_name,
                        reason='at websocket subscription limit',
                    )
                return False

        # Generate unique trade ID for this entry
        trade_id = str(uuid.uuid4())

        # Start tracking for entry (keyed by trade_id, not ticker)
        existing_count = len(self._get_pending_for_ticker(ticker)) + len(self._get_trades_for_ticker(ticker))
        logger.info(f"[{self.strategy_name}] [{ticker}] Starting to track for entry (trade_id={trade_id[:8]}, existing positions: {existing_count})")
        alert_time = datetime.utcnow()  # Use UTC to match quote timestamps
        self.pending_entries[trade_id] = PendingEntry(
            trade_id=trade_id,
            ticker=ticker,
            announcement=announcement,
            alert_time=alert_time,
            trace_id=trace_id,
        )

        # Persist pending entry to database for recovery on restart
        store = get_pending_entry_store()
        store.save_entry(
            trade_id=trade_id,
            ticker=ticker,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            alert_time=alert_time,
            first_price=None,
            announcement_ticker=announcement.ticker,
            announcement_timestamp=announcement.timestamp,
        )

        # Record pending entry created event
        if trace_store and trace_id:
            trace_store.add_event(
                trace_id=trace_id,
                event_type='pending_entry_created',
                event_timestamp=alert_time,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                details={'trade_id': trade_id},
            )
            trace_store.update_trace_status(
                trace_id=trace_id,
                status='pending_entry',
                pending_entry_trade_id=trade_id,
            )

        return True

    def initialize_building_candle(self, ticker: str, candle_data: dict):
        """
        Initialize the building candle with data from REST API.

        This is called when subscribing to a ticker mid-minute, so we know
        the OHLCV that already occurred before we subscribed.

        Args:
            ticker: Stock symbol
            candle_data: dict with keys: open, high, low, close, volume, timestamp
        """
        if not candle_data:
            return

        # Calculate the candle start time (floor to minute) in UTC
        candle_ts = candle_data.get("timestamp", 0)
        if candle_ts:
            candle_start = datetime.utcfromtimestamp(candle_ts)
        else:
            candle_start = datetime.utcnow().replace(second=0, microsecond=0)

        # Initialize building candle with REST data
        self._ticker_candle_start[ticker] = candle_start
        self._ticker_building_candle[ticker] = {
            "open": candle_data.get("open", 0),
            "high": candle_data.get("high", 0),
            "low": candle_data.get("low", 0),
            "close": candle_data.get("close", 0),
            "volume": candle_data.get("volume", 0),
        }

        vol = candle_data.get("volume", 0)
        logger.info(
            f"[{ticker}] Initialized building candle from REST: {candle_start.strftime('%H:%M')} | "
            f"O={candle_data.get('open', 0):.2f} H={candle_data.get('high', 0):.2f} "
            f"L={candle_data.get('low', 0):.2f} C={candle_data.get('close', 0):.2f} V={vol:,}"
        )

    def on_quote(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """
        Handle price update from InsightSentry.

        Args:
            ticker: Stock symbol
            price: Current price (last/close)
            volume: Current volume
            timestamp: Quote timestamp
        """
        # Check all pending entries for this ticker (may be multiple)
        pending_entries = self._get_pending_for_ticker(ticker)
        if pending_entries:
            self._check_entry(ticker, price, volume, timestamp)

        # Check all active trades for this ticker (may be multiple)
        active_trades = self._get_trades_for_ticker(ticker)
        for trade in active_trades:
            # Update last price for display
            trade.last_price = price
            trade.last_quote_time = timestamp
            pnl_pct = ((price - trade.entry_price) / trade.entry_price) * 100
            status_logger.info(f"[{self.strategy_name}] [{ticker}] ${price:.2f} ({pnl_pct:+.1f}%) | SL=${trade.stop_loss_price:.2f} TP=${trade.take_profit_price:.2f} (trade={trade.trade_id[:8]})")
            self._check_exit(trade.trade_id, price, timestamp)

        # Check for pending buy orders that have timed out
        self._check_pending_buy_order_timeouts(ticker, timestamp)

        # Check for pending sell orders that have timed out (cancel and retry with fresh price)
        self._check_pending_sell_order_timeouts(ticker, timestamp)

    def _passes_filters(self, ann: Announcement) -> bool:
        """Check if announcement passes all filters."""
        cfg = self.config

        # Channel filter
        if cfg.channels and ann.channel not in cfg.channels:
            logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: channel '{ann.channel}' not in {cfg.channels}")
            return False

        # Direction filter
        if cfg.directions and ann.direction not in cfg.directions:
            logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: direction '{ann.direction}' not in {cfg.directions}")
            return False

        # Session filter
        if cfg.sessions and ann.market_session not in cfg.sessions:
            logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: session '{ann.market_session}' not in {cfg.sessions}")
            return False

        # Price filter (using price_threshold from announcement as proxy)
        # Note: Real price check happens at entry time
        if ann.price_threshold:
            if ann.price_threshold <= cfg.price_min or ann.price_threshold > cfg.price_max:
                logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: price ${ann.price_threshold} outside ${cfg.price_min}-${cfg.price_max}")
                return False

        # Country blacklist filter
        if cfg.country_blacklist and ann.country in cfg.country_blacklist:
            logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: country '{ann.country}' in blacklist")
            return False

        # Intraday mentions filter (must be less than max)
        if cfg.max_intraday_mentions is not None and ann.mention_count is not None:
            if ann.mention_count >= cfg.max_intraday_mentions:
                logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: {ann.mention_count} mentions >= max {cfg.max_intraday_mentions}")
                return False

        # Financing headline filter (offerings, reverse splits, etc.)
        if cfg.exclude_financing_headlines and ann.headline_is_financing:
            logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: financing headline ({ann.headline_financing_type})")
            return False

        # Biotech/pharma filter
        if cfg.exclude_biotech and ann.headline:
            biotech_keywords = ['therapeutics', 'clinical', 'trial', 'phase', 'fda', 'drug', 'treatment']
            headline_lower = ann.headline.lower()
            if any(kw in headline_lower for kw in biotech_keywords):
                logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: biotech headline")
                return False

        # Prior move filter (skip if already moved too much)
        if cfg.max_prior_move_pct is not None and ann.scanner_gain_pct is not None:
            if ann.scanner_gain_pct > cfg.max_prior_move_pct:
                logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: prior move {ann.scanner_gain_pct:.1f}% > max {cfg.max_prior_move_pct}%")
                return False

        # Market cap filter
        if cfg.max_market_cap_millions is not None and ann.market_cap is not None:
            max_cap = cfg.max_market_cap_millions * 1e6
            if ann.market_cap > max_cap:
                logger.info(f"[{self.strategy_name}] [{ann.ticker}] Filtered: market cap ${ann.market_cap/1e6:.1f}M > max ${cfg.max_market_cap_millions}M")
                return False

        return True

    def _passes_filters_with_reason(self, ann: Announcement) -> tuple:
        """Check if announcement passes all filters, returning rejection reason if not.

        Returns:
            (True, None) if passes all filters
            (False, reason) if rejected
        """
        cfg = self.config

        # Channel filter
        if cfg.channels and ann.channel not in cfg.channels:
            return False, f"channel '{ann.channel}' not in {cfg.channels}"

        # Direction filter
        if cfg.directions and ann.direction not in cfg.directions:
            return False, f"direction '{ann.direction}' not in {cfg.directions}"

        # Session filter
        if cfg.sessions and ann.market_session not in cfg.sessions:
            return False, f"session '{ann.market_session}' not in {cfg.sessions}"

        # Price filter (using price_threshold from announcement as proxy)
        if ann.price_threshold:
            if ann.price_threshold <= cfg.price_min or ann.price_threshold > cfg.price_max:
                return False, f"price ${ann.price_threshold} outside ${cfg.price_min}-${cfg.price_max}"

        # Country blacklist filter
        if cfg.country_blacklist and ann.country in cfg.country_blacklist:
            return False, f"country '{ann.country}' in blacklist"

        # Intraday mentions filter (must be less than max)
        if cfg.max_intraday_mentions is not None and ann.mention_count is not None:
            if ann.mention_count >= cfg.max_intraday_mentions:
                return False, f"{ann.mention_count} mentions >= max {cfg.max_intraday_mentions}"

        # Financing headline filter (offerings, reverse splits, etc.)
        if cfg.exclude_financing_headlines and ann.headline_is_financing:
            return False, f"financing headline ({ann.headline_financing_type})"

        # Biotech/pharma filter
        if cfg.exclude_biotech and ann.headline:
            biotech_keywords = ['therapeutics', 'clinical', 'trial', 'phase', 'fda', 'drug', 'treatment']
            headline_lower = ann.headline.lower()
            if any(kw in headline_lower for kw in biotech_keywords):
                return False, "biotech/pharma headline"

        # Prior move filter (skip if already moved too much)
        if cfg.max_prior_move_pct is not None and ann.scanner_gain_pct is not None:
            if ann.scanner_gain_pct > cfg.max_prior_move_pct:
                return False, f"prior move {ann.scanner_gain_pct:.1f}% > max {cfg.max_prior_move_pct}%"

        # Market cap filter
        if cfg.max_market_cap_millions is not None and ann.market_cap is not None:
            max_cap = cfg.max_market_cap_millions * 1e6
            if ann.market_cap > max_cap:
                return False, f"market cap ${ann.market_cap/1e6:.1f}M > max ${cfg.max_market_cap_millions}M"

        return True, None

    def _check_entry(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """Check if entry conditions are met for any pending entries for this ticker.

        Candle data is shared across all pending entries for the same ticker.
        Each pending entry is evaluated independently for entry conditions.
        """
        cfg = self.config

        # Log every quote for debugging (to volume.log)
        quotes_logger.info(f"[{self.strategy_name}] [{ticker}] QUOTE: ${price:.4f} vol={volume:,} | filter: ${cfg.price_min:.2f}-${cfg.price_max:.2f}")

        # Price filter at actual price
        if price <= cfg.price_min or price > cfg.price_max:
            quotes_logger.info(f"[{self.strategy_name}] [{ticker}] FILTERED: ${price:.4f} outside ${cfg.price_min:.2f}-${cfg.price_max:.2f}")
            # Still check timeouts even if price is filtered
            self._check_pending_timeouts(ticker, timestamp)
            return

        # --- Build shared candles for this ticker ---
        candles = self._ticker_candles.get(ticker, [])
        candle_start = timestamp.replace(second=0, microsecond=0)
        building_candle = self._ticker_building_candle.get(ticker)
        building_start = self._ticker_candle_start.get(ticker)

        if building_start != candle_start:
            # New candle starting - finalize previous if exists
            if building_candle:
                candle = CandleBar(
                    timestamp=building_start,
                    open=building_candle["open"],
                    high=building_candle["high"],
                    low=building_candle["low"],
                    close=building_candle["close"],
                    volume=building_candle["volume"],
                )
                candles.append(candle)
                self._ticker_candles[ticker] = candles

                # Detailed candle close logging (to volume.log file)
                color = "GREEN" if candle.is_green else "RED"
                meets_vol = candle.volume >= cfg.min_candle_volume
                qualifies = candle.is_green and meets_vol
                quotes_logger.info(f"")
                quotes_logger.info(f"[{self.strategy_name}] [{ticker}] ━━━ CANDLE CLOSED ━━━")
                quotes_logger.info(f"[{self.strategy_name}] [{ticker}] {color} candle | O={candle.open:.2f} H={candle.high:.2f} L={candle.low:.2f} C={candle.close:.2f}")
                quotes_logger.info(f"[{self.strategy_name}] [{ticker}] Volume: {candle.volume:,} {'>=✓' if meets_vol else '<✗'} {cfg.min_candle_volume:,} threshold")
                quotes_logger.info(f"[{self.strategy_name}] [{ticker}] Qualifies for entry: {'YES ✓' if qualifies else 'NO ✗'}")
                quotes_logger.info(f"")

            # Start new candle
            self._ticker_candle_start[ticker] = candle_start
            self._ticker_building_candle[ticker] = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
            building_candle = self._ticker_building_candle[ticker]
        else:
            # Update current candle
            if building_candle:
                building_candle["high"] = max(building_candle["high"], price)
                building_candle["low"] = min(building_candle["low"], price)
                building_candle["close"] = price
                building_candle["volume"] += volume  # Sum volume from all 1-second bars

        # Log current candle volume progress (to volume.log file)
        if building_candle:
            curr_vol = building_candle["volume"]
            curr_open = building_candle["open"]
            curr_close = building_candle["close"]
            is_green = curr_close > curr_open
            pct_of_threshold = (curr_vol / cfg.min_candle_volume * 100) if cfg.min_candle_volume > 0 else 0
            color = "GREEN" if is_green else "RED"
            quotes_logger.info(
                f"[{ticker}] CANDLE BUILDING: {color} | Vol: {curr_vol:,} / {cfg.min_candle_volume:,} ({pct_of_threshold:.0f}%) | "
                f"O={curr_open:.2f} C={curr_close:.2f}"
            )

        # Check for consecutive green candles with volume (from completed candles)
        completed_green_count = 0
        for candle in reversed(candles):
            if candle.is_green and candle.volume >= cfg.min_candle_volume:
                completed_green_count += 1
            else:
                break

        # Log completed candles status (to volume.log file)
        if candles:
            last_candle = candles[-1]
            meets_vol = last_candle.volume >= cfg.min_candle_volume
            quotes_logger.info(
                f"[{ticker}] LAST COMPLETED CANDLE: {'GREEN' if last_candle.is_green else 'RED'} | "
                f"Vol: {last_candle.volume:,} {'>=✓' if meets_vol else '<✗'} {cfg.min_candle_volume:,} | "
                f"Completed green candles with vol: {completed_green_count}/{cfg.consec_green_candles} needed"
            )

        # --- Evaluate entry conditions for each pending entry ---
        pending_entries = self._get_pending_for_ticker(ticker)
        for pending in pending_entries:
            # Check entry window timeout for this specific pending entry
            time_since_alert = (timestamp - pending.alert_time).total_seconds() / 60
            logger.debug(f"[{ticker}] Timeout check: quote_ts={timestamp}, alert_time={pending.alert_time}, diff={time_since_alert:.1f}m")
            if time_since_alert > cfg.entry_window_minutes:
                logger.info(f"[{self.strategy_name}] [{ticker}] Entry window timeout for trade_id={pending.trade_id[:8]} ({time_since_alert:.1f}m > {cfg.entry_window_minutes}m)")
                self._abandon_pending_entry(pending.trade_id)
                continue

            # Record first price for this pending entry
            if pending.first_price is None:
                pending.first_price = price
                logger.info(f"[{self.strategy_name}] [{ticker}] First price for trade_id={pending.trade_id[:8]}: ${price:.2f}")

            # If no consecutive candle requirement, enter immediately
            if cfg.consec_green_candles == 0:
                self._execute_entry(pending.trade_id, price, timestamp, trigger="no_candle_req")
                continue

            # EARLY ENTRY: If current building candle is green and hits volume threshold,
            # count it toward the green candle requirement and enter immediately
            green_count = completed_green_count
            if building_candle:
                curr_vol = building_candle["volume"]
                curr_open = building_candle["open"]
                curr_close = building_candle["close"]
                curr_is_green = curr_close > curr_open
                curr_meets_vol = curr_vol >= cfg.min_candle_volume

                if curr_is_green and curr_meets_vol:
                    green_count += 1  # Count building candle toward requirement
                    if green_count >= cfg.consec_green_candles:
                        logger.info(f"")
                        logger.info(f"{'='*60}")
                        logger.info(f"[{self.strategy_name}] [{ticker}] 🚀🚀🚀 EARLY ENTRY! Building candle hit {curr_vol:,} volume while GREEN! (trade_id={pending.trade_id[:8]})")
                        logger.info(f"[{self.strategy_name}] [{ticker}] {completed_green_count} completed + 1 building = {green_count} green candles")
                        logger.info(f"{'='*60}")
                        logger.info(f"")
                        self._execute_entry(pending.trade_id, price, timestamp, trigger=f"early_entry_{green_count}_green")
                        continue

            if completed_green_count >= cfg.consec_green_candles:
                # Fallback: enter on completed candles if early entry didn't trigger
                logger.info(f"")
                logger.info(f"{'='*60}")
                logger.info(f"[{self.strategy_name}] [{ticker}] 🚀🚀🚀 ENTRY CONDITION MET! {completed_green_count} completed green candles with volume! (trade_id={pending.trade_id[:8]})")
                logger.info(f"{'='*60}")
                logger.info(f"")
                self._execute_entry(pending.trade_id, price, timestamp, trigger=f"completed_{completed_green_count}_green")

    def _check_pending_timeouts(self, ticker: str, timestamp: datetime):
        """Check and abandon pending entries that have timed out."""
        cfg = self.config
        pending_entries = self._get_pending_for_ticker(ticker)
        for pending in pending_entries:
            time_since_alert = (timestamp - pending.alert_time).total_seconds() / 60
            if time_since_alert > cfg.entry_window_minutes:
                logger.info(f"[{self.strategy_name}] [{ticker}] Entry window timeout for trade_id={pending.trade_id[:8]} ({time_since_alert:.1f}m > {cfg.entry_window_minutes}m)")
                self._abandon_pending_entry(pending.trade_id)

    def _execute_entry(self, trade_id: str, price: float, timestamp: datetime, trigger: str = "entry_signal"):
        """Execute entry order.

        Args:
            trade_id: The trade_id of the pending entry to execute
            trigger: Reason for entry, e.g., "early_entry", "completed_candles", "no_candle_req"
        """
        pending = self.pending_entries.pop(trade_id)
        ticker = pending.ticker
        trace_id = pending.trace_id

        # Record entry_condition_met event
        if trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=trace_id,
                event_type='entry_condition_met',
                event_timestamp=timestamp,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                reason=trigger,
                details={'trade_id': trade_id, 'price': price},
            )

        # Remove from database (entry is being executed)
        get_pending_entry_store().delete_entry(trade_id)
        cfg = self.config

        # Calculate stop loss price
        if cfg.stop_loss_from_open and pending.first_price:
            stop_loss_price = pending.first_price * (1 - cfg.stop_loss_pct / 100)
            # Sanity check: stop should not be at or above entry
            # (that would cause immediate stop-out or act as a take profit)
            if stop_loss_price >= price:
                stop_loss_price = price * (1 - cfg.stop_loss_pct / 100)
        else:
            stop_loss_price = price * (1 - cfg.stop_loss_pct / 100)

        take_profit_price = price * (1 + cfg.take_profit_pct / 100)

        # Get candle volume for volume-based sizing (from shared candle data)
        # Use last completed candle, or extrapolate current candle for early entry
        candle_volume = None
        extrapolated = False
        actual_vol = None
        elapsed_secs = None
        shared_candles = self._ticker_candles.get(ticker, [])
        building_candle = self._ticker_building_candle.get(ticker)
        building_start = self._ticker_candle_start.get(ticker)

        if shared_candles:
            candle_volume = shared_candles[-1].volume
        elif building_candle and building_start:
            # Early entry on first candle - extrapolate to full minute
            actual_vol = building_candle["volume"]
            elapsed_secs = (timestamp - building_start).total_seconds()
            if elapsed_secs > 0:
                # Project what the full minute's volume would be
                candle_volume = int(actual_vol * (60.0 / elapsed_secs))
                extrapolated = True
            else:
                candle_volume = actual_vol

        # Calculate shares based on position sizing mode
        shares = cfg.get_shares(price, candle_volume)
        if shares <= 0:
            logger.error(f"[{self.strategy_name}] [{ticker}] Cannot calculate shares for price ${price:.2f} (trade_id={trade_id[:8]})")
            # Only unsubscribe if no more pending entries or active trades for this ticker
            if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
                self.on_unsubscribe(ticker)
            return

        # Log entry with sizing details
        position_cost = shares * price
        if cfg.stake_mode == "volume_pct" and candle_volume:
            if extrapolated and actual_vol is not None and elapsed_secs is not None:
                sizing_info = f"{cfg.volume_pct}% of {candle_volume:,} vol (extrapolated from {actual_vol:,} in {elapsed_secs:.0f}s) = {shares} shares (${position_cost:.0f})"
            else:
                sizing_info = f"{cfg.volume_pct}% of {candle_volume:,} vol = {shares} shares (${position_cost:.0f})"
        else:
            sizing_info = f"${cfg.stake_amount:.0f} stake = {shares} shares"

        logger.info(f"")
        logger.info(f"{'$'*60}")
        logger.info(f"[{self.strategy_name}] [{ticker}] 💰💰💰 EXECUTING BUY ORDER 💰💰💰")
        logger.info(f"[{self.strategy_name}] [{ticker}] ENTRY @ ${price:.4f}, {sizing_info}")
        logger.info(f"[{self.strategy_name}] [{ticker}] Config: price_min=${cfg.price_min:.2f}, price_max=${cfg.price_max:.2f}")
        logger.info(f"[{self.strategy_name}] [{ticker}] SL=${stop_loss_price:.4f}, TP=${take_profit_price:.4f}")
        logger.info(f"{'$'*60}")
        logger.info(f"")

        # Create order record in database before submitting to broker
        db_order_id = self._order_store.create_order(
            ticker=ticker,
            side="buy",
            order_type="limit",
            requested_shares=shares,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            limit_price=price,
            trade_id=trade_id,
            paper=self.paper,
        )

        # Execute buy order (limit order at current price + slippage)
        try:
            order = self.trader.buy(ticker, shares, limit_price=price)
            logger.info(f"[{self.strategy_name}] [{ticker}] ✅ Buy order submitted: {order.order_id} ({order.status})")

            # Update database with broker order ID
            if db_order_id:
                self._order_store.update_broker_order_id(db_order_id, order.order_id)
                self._order_store.record_event(
                    event_type="submitted",
                    event_timestamp=datetime.utcnow(),  # Use UTC for database storage
                    order_id=db_order_id,
                    broker_order_id=order.order_id,
                )

            # Record buy_order_submitted trace event
            if trace_id:
                trace_store = get_trace_store()
                trace_store.add_event(
                    trace_id=trace_id,
                    event_type='buy_order_submitted',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    details={
                        'trade_id': trade_id,
                        'order_id': order.order_id,
                        'shares': shares,
                        'price': price,
                    },
                )
                trace_store.update_trace_status(trace_id, status='buy_submitted')

        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Buy order failed: {e}", exc_info=True)
            # Update order status to rejected
            if db_order_id:
                self._order_store.update_order_status(order_id=db_order_id, status="rejected")
                self._order_store.record_event(
                    event_type="rejected",
                    event_timestamp=timestamp,
                    order_id=db_order_id,
                    raw_data={"error": str(e)},
                )
            # Record buy_order_rejected trace event
            if trace_id:
                trace_store = get_trace_store()
                trace_store.add_event(
                    trace_id=trace_id,
                    event_type='buy_order_rejected',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    reason=str(e),
                )
                trace_store.update_trace_status(trace_id, status='error')
            # Only unsubscribe if no more pending entries or active trades for this ticker
            if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
                self.on_unsubscribe(ticker)
            return

        # Track pending order - will create ActiveTrade when fill confirmed
        self.pending_orders[order.order_id] = PendingOrder(
            order_id=order.order_id,
            ticker=ticker,
            side="buy",
            shares=shares,
            limit_price=price,
            submitted_at=timestamp,
            trade_id=trade_id,  # Link to the original pending entry
            db_order_id=db_order_id,
            announcement=pending.announcement,
            first_candle_open=pending.first_price or price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            entry_trigger=trigger,
            sizing_info=sizing_info,
            trace_id=trace_id,
        )
        logger.info(f"[{self.strategy_name}] [{ticker}] Order {order.order_id} pending fill confirmation (trade_id={trade_id[:8]})")

    def on_buy_fill(
        self,
        order_id: str,
        ticker: str,
        shares: int,
        filled_price: float,
        timestamp: datetime,
    ):
        """Handle buy order fill - create ActiveTrade."""
        pending = self.pending_orders.pop(order_id, None)
        if not pending:
            logger.warning(f"[{self.strategy_name}] [{ticker}] Fill for unknown order {order_id}")
            return

        logger.info(f"[{self.strategy_name}] [{ticker}] ✅ BUY FILLED: {shares} shares @ ${filled_price:.4f}")
        trace_id = pending.trace_id

        # Record buy_order_filled event
        if trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=trace_id,
                event_type='buy_order_filled',
                event_timestamp=timestamp,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                details={
                    'trade_id': pending.trade_id,
                    'order_id': order_id,
                    'fill_price': filled_price,
                    'shares': shares,
                },
            )

        # Log to dedicated trades file
        log_buy_fill(
            ticker=ticker,
            shares=shares,
            price=filled_price,
            strategy_name=self.strategy_name,
            trigger=pending.entry_trigger or "",
            sizing_info=pending.sizing_info or "",
        )

        # Record fill event and update order status
        if pending.db_order_id:
            self._order_store.record_event(
                event_type="fill",
                event_timestamp=timestamp,
                order_id=pending.db_order_id,
                broker_order_id=order_id,
                filled_shares=shares,
                fill_price=filled_price,
                cumulative_filled=shares,
            )
            self._order_store.update_order_status(
                order_id=pending.db_order_id,
                status="filled",
                filled_shares=shares,
                avg_fill_price=filled_price,
            )

        # Recalculate SL/TP based on actual fill price
        cfg = self.config
        if pending.stop_loss_price and pending.first_candle_open:
            # Keep the original SL if it was from candle open
            stop_loss_price = pending.stop_loss_price
        else:
            stop_loss_price = filled_price * (1 - cfg.stop_loss_pct / 100)
        take_profit_price = filled_price * (1 + cfg.take_profit_pct / 100)

        # Get trade_id from the pending order
        trade_id = pending.trade_id
        if not trade_id:
            # Fallback for legacy orders without trade_id
            trade_id = str(uuid.uuid4())
            logger.warning(f"[{self.strategy_name}] [{ticker}] Missing trade_id on buy order, generating new: {trade_id[:8]}")

        # Create active trade with actual fill price, keyed by trade_id
        self.active_trades[trade_id] = ActiveTrade(
            trade_id=trade_id,
            ticker=ticker,
            announcement=pending.announcement,
            entry_price=filled_price,
            entry_time=timestamp,
            first_candle_open=pending.first_candle_open or filled_price,
            shares=shares,
            highest_since_entry=filled_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            trace_id=trace_id,
        )

        logger.info(f"[{self.strategy_name}] [{ticker}] Created active trade (trade_id={trade_id[:8]})")

        # Record active_trade_created event
        if trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=trace_id,
                event_type='active_trade_created',
                event_timestamp=timestamp,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                details={'trade_id': trade_id},
            )
            trace_store.update_trace_status(
                trace_id=trace_id,
                status='active_trade',
                active_trade_id=trade_id,
            )

        # Persist to database
        save_success = self._active_trade_store.save_trade(
            trade_id=trade_id,
            ticker=ticker,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            entry_price=filled_price,
            entry_time=timestamp,
            first_candle_open=pending.first_candle_open or filled_price,
            shares=shares,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            highest_since_entry=filled_price,
            paper=self.paper,
            announcement_ticker=pending.announcement.ticker if pending.announcement else None,
            announcement_timestamp=pending.announcement.timestamp if pending.announcement else None,
        )

        # CRITICAL: If save failed, immediately sell to prevent orphaned position at broker
        if not save_success:
            logger.error(f"[{self.strategy_name}] [{ticker}] ⚠️ FAILED TO SAVE TRADE TO DATABASE - LIQUIDATING POSITION TO PREVENT ORPHAN")
            logger.error(f"[{self.strategy_name}] [{ticker}] This is likely due to a duplicate constraint. Selling {shares} shares immediately.")
            try:
                # Remove from in-memory tracking
                self.active_trades.pop(trade_id, None)

                # Fetch current price from REST API (price may have moved since fill)
                current_price = None
                if self.on_fetch_price:
                    current_price = self.on_fetch_price(ticker)
                if current_price is None:
                    # Fallback to filled price if REST fetch fails
                    current_price = filled_price
                    logger.warning(f"[{self.strategy_name}] [{ticker}] Could not fetch current price, using fill price ${current_price:.4f}")

                # Create order record for proper logging
                db_order_id = self._order_store.create_order(
                    ticker=ticker,
                    side="sell",
                    order_type="limit",
                    requested_shares=shares,
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    limit_price=current_price,
                    trade_id=trade_id,
                    paper=self.paper,
                )

                # Sell the position immediately
                order = self.trader.sell(ticker, shares, limit_price=current_price)
                logger.error(f"[{self.strategy_name}] [{ticker}] Emergency sell submitted: {order.order_id} ({order.status}) @ ${current_price:.4f}")

                # Update order with broker order ID
                if db_order_id:
                    self._order_store.update_broker_order_id(db_order_id, order.order_id)
                    self._order_store.record_event(
                        event_type="submitted",
                        event_timestamp=datetime.utcnow(),  # Use actual submission time, not quote time
                        order_id=db_order_id,
                        broker_order_id=order.order_id,
                    )

            except Exception as e:
                logger.critical(f"[{ticker}] ❌ CRITICAL: Failed to emergency sell orphaned position: {e}")
                logger.critical(f"[{ticker}] MANUAL INTERVENTION REQUIRED: {shares} shares at broker without DB record!")

    def on_sell_fill(
        self,
        order_id: str,
        ticker: str,
        shares: int,
        filled_price: float,
        timestamp: datetime,
    ):
        """Handle sell order fill - complete the trade."""
        pending = self.pending_orders.pop(order_id, None)
        if not pending:
            logger.warning(f"[{self.strategy_name}] [{ticker}] Fill for unknown sell order {order_id}")
            return

        return_pct = ((filled_price - pending.entry_price) / pending.entry_price) * 100
        pnl = (filled_price - pending.entry_price) * shares

        logger.info(f"[{self.strategy_name}] [{ticker}] ✅ SELL FILLED: {shares} shares @ ${filled_price:.4f} | P&L: ${pnl:+.2f} ({return_pct:+.2f}%)")

        # Record sell_order_filled trace event
        if pending.trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=pending.trace_id,
                event_type='sell_order_filled',
                event_timestamp=timestamp,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                details={
                    'trade_id': pending.trade_id,
                    'broker_order_id': order_id,
                    'shares': shares,
                    'fill_price': filled_price,
                    'pnl': pnl,
                    'return_pct': return_pct,
                },
            )

        # Log to dedicated trades file
        log_sell_fill(
            ticker=ticker,
            shares=shares,
            price=filled_price,
            strategy_name=self.strategy_name,
            entry_price=pending.entry_price,
            pnl=pnl,
            pnl_pct=return_pct,
            exit_reason=pending.exit_reason or "",
        )

        # Record fill event and update order status
        if pending.db_order_id:
            self._order_store.record_event(
                event_type="fill",
                event_timestamp=timestamp,
                order_id=pending.db_order_id,
                broker_order_id=order_id,
                filled_shares=shares,
                fill_price=filled_price,
                cumulative_filled=shares,
            )
            self._order_store.update_order_status(
                order_id=pending.db_order_id,
                status="filled",
                filled_shares=shares,
                avg_fill_price=filled_price,
            )

        # Record completed trade
        completed = {
            "ticker": ticker,
            "entry_price": pending.entry_price,
            "entry_time": pending.entry_time.isoformat() if pending.entry_time else timestamp.isoformat(),
            "exit_price": filled_price,
            "exit_time": timestamp.isoformat(),
            "shares": shares,
            "pnl": pnl,
            "return_pct": return_pct,
        }
        self.completed_trades.append(completed)

        # Persist to trade history
        try:
            trade_record = {
                "ticker": ticker,
                "entry_price": pending.entry_price,
                "exit_price": filled_price,
                "entry_time": pending.entry_time or timestamp,
                "exit_time": timestamp,
                "shares": shares,
                "exit_reason": "filled",
                "return_pct": completed["return_pct"],
                "pnl": completed["pnl"],
                "strategy_params": self.config.to_dict(),
            }
            self._trade_store.save_trade(
                trade=trade_record,
                paper=self.paper,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                trade_id=pending.trade_id,
            )
        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Failed to record trade: {e}", exc_info=True)

        # Remove from in-memory and database active trades using trade_id
        trade_id = pending.trade_id
        if trade_id:
            # Remove from in-memory tracking
            self.active_trades.pop(trade_id, None)
            # Remove from database
            try:
                self._active_trade_store.delete_trade(trade_id)
            except Exception as e:
                logger.error(f"[{self.strategy_name}] [{ticker}] Failed to delete trade_id={trade_id[:8]} from active_trades: {e}", exc_info=True)
        else:
            logger.warning(f"[{self.strategy_name}] [{ticker}] No trade_id on sell order - skipping active_trade_store delete")

        # Only unsubscribe if no more pending entries or active trades for this ticker
        if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
            self.on_unsubscribe(ticker)

        # Record trade_completed trace event and update final status
        if pending.trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=pending.trace_id,
                event_type='trade_completed',
                event_timestamp=timestamp,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                details={
                    'trade_id': pending.trade_id,
                    'exit_reason': pending.exit_reason,
                    'pnl': pnl,
                    'return_pct': return_pct,
                },
            )
            trace_store.update_trace_status(
                trace_id=pending.trace_id,
                status='completed',
                exit_reason=pending.exit_reason,
                pnl=pnl,
                return_pct=return_pct,
                completed_at=timestamp,
            )

    def on_order_canceled(self, order_id: str, ticker: str, side: str, timestamp: Optional[datetime] = None):
        """Handle order cancellation."""
        pending = self.pending_orders.pop(order_id, None)
        if pending:
            logger.warning(f"[{self.strategy_name}] [{ticker}] Order {order_id} ({side}) was CANCELED")

            # Record canceled event and update order status
            if pending.db_order_id:
                event_time = timestamp or datetime.utcnow()
                self._order_store.record_event(
                    event_type="canceled",
                    event_timestamp=event_time,
                    order_id=pending.db_order_id,
                    broker_order_id=order_id,
                )
                self._order_store.update_order_status(
                    order_id=pending.db_order_id,
                    status="canceled",
                )

            if side == "buy":
                # Check if there were any partial fills before cancellation
                if pending.db_order_id:
                    events = self._order_store.get_events_for_order(pending.db_order_id)
                    filled_shares = 0
                    filled_price = None
                    for event in events:
                        if event.event_type in ["partial_fill", "fill"]:
                            filled_shares = event.cumulative_filled or 0
                            filled_price = event.fill_price

                    if filled_shares > 0 and filled_price is not None:
                        # Partial fill exists - save as active trade instead of just canceling
                        logger.warning(
                            f"[{self.strategy_name}] [{ticker}] Order canceled but {filled_shares} shares "
                            f"filled @ ${filled_price:.4f} - saving as active trade"
                        )
                        # Put the order back in pending_orders so on_buy_fill can find it
                        self.pending_orders[order_id] = pending
                        # Call on_buy_fill with the filled shares
                        self.on_buy_fill(order_id, ticker, filled_shares, filled_price, event_time)
                        return  # Don't unsubscribe since we have an active position

                # No fills - just unsubscribe as normal
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)
            else:
                # Sell canceled - need to re-add to active trades or retry
                logger.warning(f"[{self.strategy_name}] [{ticker}] Sell order canceled - position still open!")

    def on_order_rejected(self, order_id: str, ticker: str, side: str, reason: str, timestamp: Optional[datetime] = None):
        """Handle order rejection."""
        pending = self.pending_orders.pop(order_id, None)
        if pending:
            logger.error(f"[{self.strategy_name}] [{ticker}] Order {order_id} ({side}) was REJECTED: {reason}")

            # Record rejected event and update order status
            if pending.db_order_id:
                event_time = timestamp or datetime.utcnow()
                self._order_store.record_event(
                    event_type="rejected",
                    event_timestamp=event_time,
                    order_id=pending.db_order_id,
                    broker_order_id=order_id,
                    raw_data={"reason": reason},
                )
                self._order_store.update_order_status(
                    order_id=pending.db_order_id,
                    status="rejected",
                )

            if side == "buy":
                # Check if there were any partial fills before rejection
                if pending.db_order_id:
                    events = self._order_store.get_events_for_order(pending.db_order_id)
                    filled_shares = 0
                    filled_price = None
                    for event in events:
                        if event.event_type in ["partial_fill", "fill"]:
                            filled_shares = event.cumulative_filled or 0
                            filled_price = event.fill_price

                    if filled_shares > 0 and filled_price is not None:
                        # Partial fill exists - save as active trade instead of just rejecting
                        logger.warning(
                            f"[{self.strategy_name}] [{ticker}] Order rejected but {filled_shares} shares "
                            f"filled @ ${filled_price:.4f} - saving as active trade"
                        )
                        # Put the order back in pending_orders so on_buy_fill can find it
                        self.pending_orders[order_id] = pending
                        # Call on_buy_fill with the filled shares
                        self.on_buy_fill(order_id, ticker, filled_shares, filled_price, event_time)
                        return  # Don't unsubscribe since we have an active position

                # No fills - just unsubscribe as normal
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)
            else:
                # Sell rejected - position still open
                logger.error(f"[{self.strategy_name}] [{ticker}] Sell order rejected - position still open!")

    def _check_exit(self, trade_id: str, price: float, timestamp: datetime):
        """Check exit conditions for a specific active trade."""
        trade = self.active_trades.get(trade_id)
        if not trade:
            return

        ticker = trade.ticker
        cfg = self.config

        # Update highest price for trailing stop
        price_changed = False
        if price > trade.highest_since_entry:
            trade.highest_since_entry = price
            price_changed = True

        # Persist price updates to database (for recovery after restart)
        if price_changed or trade.last_price != price:
            self._active_trade_store.update_price(
                trade_id=trade_id,
                last_price=trade.last_price,
                highest_since_entry=trade.highest_since_entry,
                last_quote_time=trade.last_quote_time or timestamp,
            )

        exit_reason = None
        exit_price = price

        # Check take profit
        if price >= trade.take_profit_price:
            exit_reason = "take_profit"
            exit_price = trade.take_profit_price

        # Check fixed stop loss
        elif price <= trade.stop_loss_price:
            exit_reason = "stop_loss"
            exit_price = trade.stop_loss_price

        # Check trailing stop
        elif cfg.trailing_stop_pct > 0:
            trailing_stop = trade.highest_since_entry * (1 - cfg.trailing_stop_pct / 100)
            if price <= trailing_stop:
                exit_reason = "trailing_stop"
                exit_price = trailing_stop

        # Check timeout
        time_in_trade = (timestamp - trade.entry_time).total_seconds() / 60
        if time_in_trade >= cfg.timeout_minutes:
            exit_reason = "timeout"
            exit_price = price

        if exit_reason:
            self._execute_exit(trade_id, exit_price, exit_reason, timestamp)

    def _execute_exit(self, trade_id: str, price: float, reason: str, timestamp: datetime):
        """Execute exit order for a specific trade."""
        trade = self.active_trades.get(trade_id)
        if not trade:
            logger.warning(f"[{self.strategy_name}] No active trade found for trade_id={trade_id[:8]}")
            return

        ticker = trade.ticker

        # Skip if already marked as needing manual exit (3+ failed attempts)
        if trade.needs_manual_exit:
            return

        # Check if we already have a pending sell order for this trade_id (in-memory)
        for pending in self.pending_orders.values():
            if pending.trade_id == trade_id and pending.side == "sell":
                logger.debug(f"[{ticker}] Already have pending sell order for trade_id={trade_id[:8]}, skipping")
                return

        # On retry, check broker for existing sell orders (may exist from before restart)
        # Note: broker doesn't know about trade_id, so we check by ticker
        if trade.sell_attempts > 0:
            try:
                broker_orders = self.trader.get_open_orders()
                for order in broker_orders:
                    if order.ticker == ticker and order.side == "sell":
                        logger.info(f"[{self.strategy_name}] [{ticker}] Found existing sell order at broker ({order.shares} shares), skipping (trade_id={trade_id[:8]})")
                        # Remove this trade from active trades since we're already exiting
                        self.active_trades.pop(trade_id, None)
                        # Only unsubscribe if no more positions for this ticker
                        if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
                            self.on_unsubscribe(ticker)
                        return
            except Exception as e:
                logger.warning(f"[{self.strategy_name}] [{ticker}] Could not check broker orders: {e}")

        # Verify broker has the position before trying to sell (prevents 422 errors)
        try:
            broker_position = self.trader.get_position(ticker)
            if broker_position is None or broker_position.shares <= 0:
                logger.warning(
                    f"[{self.strategy_name}] [{ticker}] Cannot sell - no position at broker "
                    f"(trade_id={trade_id[:8]}, trade.shares={trade.shares}). "
                    f"Removing ghost position from database."
                )
                # Remove ghost position from active trades
                self.active_trades.pop(trade_id, None)
                # Also delete from database
                self._active_trade_store.delete_trade(trade_id)
                # Unsubscribe if no more positions
                if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
                    self.on_unsubscribe(ticker)
                return
            elif broker_position.shares != trade.shares:
                logger.warning(
                    f"[{self.strategy_name}] [{ticker}] Share mismatch: broker has {broker_position.shares}, "
                    f"we think we have {trade.shares}. Using broker share count."
                )
                trade.shares = broker_position.shares
                # Update database with correct share count
                self._active_trade_store.save_trade(trade)
        except Exception as e:
            logger.warning(f"[{self.strategy_name}] [{ticker}] Could not verify broker position: {e}")
            # Continue with sell attempt anyway - broker will reject if no position

        return_pct = ((price - trade.entry_price) / trade.entry_price) * 100

        logger.info(f"[{self.strategy_name}] [{ticker}] EXIT @ ${price:.2f} ({reason}) - Return: {return_pct:+.2f}%")

        # Record exit_condition_triggered event
        if trade.trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=trade.trace_id,
                event_type='exit_condition_triggered',
                event_timestamp=timestamp,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                reason=reason,
                details={
                    'trade_id': trade_id,
                    'exit_price': price,
                    'entry_price': trade.entry_price,
                    'return_pct': return_pct,
                },
            )
            trace_store.update_trace_status(trade.trace_id, status='exit_triggered')

        # Create order record in database before submitting to broker
        db_order_id = self._order_store.create_order(
            ticker=ticker,
            side="sell",
            order_type="limit",
            requested_shares=trade.shares,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            limit_price=price,
            trade_id=trade_id,
            paper=self.paper,
        )

        # Execute sell order (limit order at current price - slippage)
        try:
            order = self.trader.sell(ticker, trade.shares, limit_price=price)
            logger.info(f"[{self.strategy_name}] [{ticker}] Sell order submitted: {order.order_id} ({order.status})")

            # Update database with broker order ID
            if db_order_id:
                self._order_store.update_broker_order_id(db_order_id, order.order_id)
                self._order_store.record_event(
                    event_type="submitted",
                    event_timestamp=datetime.utcnow(),  # Use UTC for database storage
                    order_id=db_order_id,
                    broker_order_id=order.order_id,
                )

            # Record sell_order_submitted trace event
            if trade.trace_id:
                trace_store = get_trace_store()
                trace_store.add_event(
                    trace_id=trade.trace_id,
                    event_type='sell_order_submitted',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    details={
                        'trade_id': trade_id,
                        'broker_order_id': order.order_id,
                        'shares': trade.shares,
                        'limit_price': price,
                        'exit_reason': reason,
                    },
                )

        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"[{self.strategy_name}] [{ticker}] Sell order failed: {e}", exc_info=True)

            # Update order status to rejected
            if db_order_id:
                self._order_store.update_order_status(order_id=db_order_id, status="rejected")
                self._order_store.record_event(
                    event_type="rejected",
                    event_timestamp=timestamp,
                    order_id=db_order_id,
                    raw_data={"error": str(e)},
                )

            # Check if error indicates position doesn't exist at broker (DB ghost)
            if "insufficient qty" in error_msg or "position does not exist" in error_msg or "sold short" in error_msg:
                # First, cancel any existing open orders that might be holding shares
                canceled_order = False
                try:
                    open_orders = self.trader.get_open_orders()
                    for existing_order in open_orders:
                        if existing_order.ticker == ticker and existing_order.side == "sell":
                            logger.warning(f"[{self.strategy_name}] [{ticker}] Canceling existing sell order {existing_order.order_id} that may be holding shares")
                            self.trader.cancel_order(existing_order.order_id)
                            canceled_order = True
                except Exception as cancel_err:
                    logger.warning(f"[{self.strategy_name}] [{ticker}] Could not cancel existing orders: {cancel_err}")

                # Verify with broker - maybe position was already closed
                broker_position = self.trader.get_position(ticker)
                if broker_position is None or broker_position.shares == 0:
                    logger.warning(f"[{self.strategy_name}] [{ticker}] Position not found at broker - removing orphaned trade from tracking")
                    self._remove_orphaned_trade(trade_id, reason="position_not_found")
                    return
                elif broker_position.shares != trade.shares:
                    # Position exists but with different quantity - update our tracking
                    logger.warning(f"[{self.strategy_name}] [{ticker}] Broker has {broker_position.shares} shares, we tracked {trade.shares} - updating")
                    trade.shares = broker_position.shares
                    # Persist the corrected share count to database
                    self._active_trade_store.save_trade(trade)

                # If we canceled an order, fetch fresh price and retry immediately
                if canceled_order:
                    logger.info(f"[{self.strategy_name}] [{ticker}] Retrying sell after canceling stale order...")
                    # Fetch fresh price from REST API
                    fresh_price = None
                    if self.on_fetch_price:
                        fresh_price = self.on_fetch_price(ticker)
                    if fresh_price is None:
                        fresh_price = price  # fallback to original price
                    else:
                        logger.info(f"[{self.strategy_name}] [{ticker}] Fetched fresh price: ${fresh_price:.4f}")

                    # Retry the sell with fresh price
                    try:
                        import time
                        time.sleep(0.5)  # Brief pause for order cancellation to settle
                        retry_order = self.trader.sell(ticker, trade.shares, limit_price=fresh_price)
                        logger.info(f"[{self.strategy_name}] [{ticker}] Retry sell order submitted: {retry_order.order_id} ({retry_order.status}) @ ${fresh_price:.4f}")

                        # Track pending sell order
                        self.pending_orders[retry_order.order_id] = PendingOrder(
                            order_id=retry_order.order_id,
                            trade_id=trade_id,
                            ticker=ticker,
                            side="sell",
                            shares=trade.shares,
                            limit_price=fresh_price,
                            submitted_at=timestamp,
                            entry_price=trade.entry_price,
                            entry_time=trade.entry_time,
                            exit_reason=reason,
                            trace_id=trade.trace_id,
                        )
                        return  # Success on retry
                    except Exception as retry_err:
                        logger.error(f"[{self.strategy_name}] [{ticker}] Retry sell also failed: {retry_err}")

            # Track sell attempts - after 3 failures, stop retrying
            trade.sell_attempts += 1
            if trade.sell_attempts >= 3:
                trade.needs_manual_exit = True
                logger.error(f"[{self.strategy_name}] [{ticker}] ⚠️ SELL FAILED 3 TIMES - needs manual exit! Position: {trade.shares} shares @ ${trade.entry_price:.4f}")
            else:
                logger.warning(f"[{self.strategy_name}] [{ticker}] Sell attempt {trade.sell_attempts}/3 failed - will retry on next exit signal")
            return  # Don't remove from tracking

        # Track pending sell order - will complete trade when fill confirmed
        self.pending_orders[order.order_id] = PendingOrder(
            order_id=order.order_id,
            trade_id=trade_id,
            ticker=ticker,
            side="sell",
            shares=trade.shares,
            limit_price=price,
            submitted_at=timestamp,
            db_order_id=db_order_id,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time,
            exit_reason=reason,
            trace_id=trade.trace_id,
        )
        logger.info(f"[{self.strategy_name}] [{ticker}] Sell order {order.order_id} pending fill confirmation (trade_id={trade_id[:8]})")

        # Keep ActiveTrade in tracking until sell fill is confirmed
        # This ensures we can retry with fresh price if sell times out

    def _abandon_pending_entry(self, trade_id: str):
        """Abandon a pending entry (timeout or other reason)."""
        pending = self.pending_entries.pop(trade_id, None)
        if pending:
            ticker = pending.ticker
            logger.info(f"[{self.strategy_name}] [{ticker}] Abandoned pending entry (trade_id={trade_id[:8]})")

            # Record entry_timeout event
            if pending.trace_id:
                trace_store = get_trace_store()
                trace_store.add_event(
                    trace_id=pending.trace_id,
                    event_type='entry_timeout',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    details={
                        'trade_id': trade_id,
                        'window_minutes': self.config.entry_window_minutes,
                    },
                )
                trace_store.update_trace_status(
                    pending.trace_id,
                    status='entry_timeout',
                )

            # Remove from database
            get_pending_entry_store().delete_entry(trade_id)
            # Only unsubscribe if no more pending entries or active trades for this ticker
            if not self._has_pending_or_trade(ticker):
                # Also clear shared candle data for this ticker
                self._clear_candles_for_ticker(ticker)
                self._ticker_building_candle.pop(ticker, None)
                self._ticker_candle_start.pop(ticker, None)
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)

    def _cancel_pending_buy_order(self, order_id: str, reason: str = "timeout"):
        """Cancel a pending buy order at the broker and clean up tracking.

        Args:
            order_id: The broker order ID to cancel
            reason: Reason for cancellation (for logging/tracing)
        """
        pending = self.pending_orders.pop(order_id, None)
        if not pending:
            logger.warning(f"[{self.strategy_name}] Cannot cancel order {order_id} - not found in pending_orders")
            return

        ticker = pending.ticker
        trade_id = pending.trade_id

        logger.warning(f"[{self.strategy_name}] [{ticker}] Canceling pending buy order {order_id} ({reason}, trade_id={trade_id[:8]})")

        # Cancel at broker
        try:
            self.trader.cancel_order(order_id)
            logger.info(f"[{self.strategy_name}] [{ticker}] Buy order {order_id} canceled at broker")
        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Failed to cancel order {order_id} at broker: {e}", exc_info=True)
            # Still continue with cleanup - order may have already filled or been canceled

        # Update order status in database
        if pending.db_order_id:
            try:
                self._order_store.record_event(
                    event_type="canceled",
                    event_timestamp=datetime.utcnow(),
                    order_id=pending.db_order_id,
                    broker_order_id=order_id,
                    raw_data={"reason": reason},
                )
                self._order_store.update_order_status(
                    pending.db_order_id,
                    status="canceled",
                )
            except Exception as e:
                logger.error(f"[{self.strategy_name}] [{ticker}] Failed to update order status in DB: {e}")

        # Record trace event
        if pending.trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=pending.trace_id,
                event_type='buy_order_canceled',
                event_timestamp=datetime.utcnow(),
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                reason=reason,
                details={
                    'trade_id': trade_id,
                    'order_id': order_id,
                    'limit_price': pending.limit_price,
                    'shares': pending.shares,
                    'seconds_pending': (datetime.utcnow() - pending.submitted_at).total_seconds(),
                },
            )
            trace_store.update_trace_status(pending.trace_id, status='buy_order_canceled')

        # Only unsubscribe if no more pending entries or active trades for this ticker
        if not self._has_pending_or_trade(ticker):
            self._clear_candles_for_ticker(ticker)
            self._ticker_building_candle.pop(ticker, None)
            self._ticker_candle_start.pop(ticker, None)
            if self.on_unsubscribe:
                self.on_unsubscribe(ticker)

    def _check_pending_buy_order_timeouts(self, ticker: str, timestamp: datetime):
        """Check for and cancel buy orders that have exceeded the timeout.

        Args:
            ticker: The ticker symbol to check orders for
            timestamp: Current timestamp for calculating elapsed time
        """
        cfg = self.config
        timeout_seconds = cfg.buy_order_timeout_seconds

        # Find pending buy orders for this ticker that have timed out
        orders_to_cancel = []
        for order_id, pending in self.pending_orders.items():
            if pending.ticker == ticker and pending.side == "buy":
                elapsed = (timestamp - pending.submitted_at).total_seconds()
                if elapsed > timeout_seconds:
                    logger.info(
                        f"[{self.strategy_name}] [{ticker}] Buy order {order_id} timed out "
                        f"({elapsed:.1f}s > {timeout_seconds}s, trade_id={pending.trade_id[:8]})"
                    )
                    orders_to_cancel.append(order_id)

        # Cancel timed-out orders (separate loop to avoid modifying dict during iteration)
        for order_id in orders_to_cancel:
            self._cancel_pending_buy_order(order_id, reason=f"timeout_{timeout_seconds}s")

    def _cancel_pending_sell_order(self, order_id: str, timestamp: datetime, reason: str = "timeout"):
        """Cancel a pending sell order at the broker and retry with fresh price.

        Args:
            order_id: The broker order ID to cancel
            timestamp: Current timestamp for the retry order
            reason: Reason for cancellation (for logging/tracing)
        """
        pending = self.pending_orders.pop(order_id, None)
        if not pending:
            logger.warning(f"[{self.strategy_name}] Cannot cancel sell order {order_id} - not found in pending_orders")
            return

        ticker = pending.ticker
        trade_id = pending.trade_id

        logger.warning(f"[{self.strategy_name}] [{ticker}] Canceling pending sell order {order_id} ({reason}, trade_id={trade_id[:8]})")

        # Cancel at broker
        try:
            self.trader.cancel_order(order_id)
            logger.info(f"[{self.strategy_name}] [{ticker}] Sell order {order_id} canceled at broker")
        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Failed to cancel sell order {order_id} at broker: {e}")
            # Continue with retry - order may have already filled or been canceled

        # Update order status in database
        if pending.db_order_id:
            try:
                self._order_store.record_event(
                    event_type="canceled",
                    event_timestamp=datetime.utcnow(),
                    order_id=pending.db_order_id,
                    broker_order_id=order_id,
                    raw_data={"reason": reason},
                )
                self._order_store.update_order_status(
                    pending.db_order_id,
                    status="canceled",
                )
            except Exception as e:
                logger.error(f"[{self.strategy_name}] [{ticker}] Failed to update order status in DB: {e}")

        # Record trace event
        if pending.trace_id:
            trace_store = get_trace_store()
            trace_store.add_event(
                trace_id=pending.trace_id,
                event_type='sell_order_canceled',
                event_timestamp=datetime.utcnow(),
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                reason=reason,
                details={
                    'trade_id': trade_id,
                    'order_id': order_id,
                    'limit_price': pending.limit_price,
                    'shares': pending.shares,
                    'seconds_pending': (datetime.utcnow() - pending.submitted_at).total_seconds(),
                },
            )

        # Fetch fresh price and retry the sell
        fresh_price = None
        if self.on_fetch_price:
            fresh_price = self.on_fetch_price(ticker)
        if fresh_price is None:
            fresh_price = pending.limit_price  # fallback to original price
            logger.warning(f"[{self.strategy_name}] [{ticker}] Could not fetch fresh price, using original: ${fresh_price:.4f}")
        else:
            logger.info(f"[{self.strategy_name}] [{ticker}] Fetched fresh price for retry: ${fresh_price:.4f}")

        # Get the trade to check sell_attempts (may be in active_trades)
        trade = self.active_trades.get(trade_id)
        if trade:
            trade.sell_attempts += 1
            if trade.sell_attempts >= 3:
                trade.needs_manual_exit = True
                logger.error(f"[{self.strategy_name}] [{ticker}] ⚠️ SELL TIMED OUT 3 TIMES - needs manual exit! Position: {pending.shares} shares")
                # Record trace
                if pending.trace_id:
                    trace_store = get_trace_store()
                    trace_store.update_trace_status(pending.trace_id, status='needs_manual_exit')
                return

        # Retry the sell with fresh price
        try:
            retry_order = self.trader.sell(ticker, pending.shares, limit_price=fresh_price)
            logger.info(f"[{self.strategy_name}] [{ticker}] Retry sell order submitted: {retry_order.order_id} ({retry_order.status}) @ ${fresh_price:.4f}")

            # Create new order record in database
            db_order_id = self._order_store.create_order(
                ticker=ticker,
                side="sell",
                order_type="limit",
                requested_shares=pending.shares,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                limit_price=fresh_price,
                trade_id=trade_id,
                paper=self.paper,
            )
            if db_order_id:
                self._order_store.update_broker_order_id(db_order_id, retry_order.order_id)
                self._order_store.record_event(
                    event_type="submitted",
                    event_timestamp=datetime.utcnow(),
                    order_id=db_order_id,
                    broker_order_id=retry_order.order_id,
                )

            # Track the new pending sell order
            self.pending_orders[retry_order.order_id] = PendingOrder(
                order_id=retry_order.order_id,
                trade_id=trade_id,
                ticker=ticker,
                side="sell",
                shares=pending.shares,
                limit_price=fresh_price,
                submitted_at=timestamp,
                db_order_id=db_order_id,
                entry_price=pending.entry_price,
                entry_time=pending.entry_time,
                exit_reason=pending.exit_reason,
                trace_id=pending.trace_id,
            )

            # Record trace event for retry
            if pending.trace_id:
                trace_store = get_trace_store()
                trace_store.add_event(
                    trace_id=pending.trace_id,
                    event_type='sell_order_retry',
                    event_timestamp=datetime.utcnow(),
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    details={
                        'trade_id': trade_id,
                        'new_order_id': retry_order.order_id,
                        'new_limit_price': fresh_price,
                        'attempt': trade.sell_attempts if trade else 1,
                    },
                )

        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Retry sell failed: {e}", exc_info=True)
            # Note: sell_attempts was already incremented before the retry attempt
            # so we don't increment again here (that would cause double-counting)
            if trade and trade.sell_attempts >= 3:
                trade.needs_manual_exit = True
                logger.error(f"[{self.strategy_name}] [{ticker}] ⚠️ SELL RETRY FAILED 3 TIMES - needs manual exit!")

    def _check_pending_sell_order_timeouts(self, ticker: str, timestamp: datetime):
        """Check for and cancel/retry sell orders that have exceeded the timeout.

        Args:
            ticker: The ticker symbol to check orders for
            timestamp: Current timestamp for calculating elapsed time
        """
        cfg = self.config
        timeout_seconds = cfg.sell_order_timeout_seconds

        # Find pending sell orders for this ticker that have timed out
        orders_to_cancel = []
        for order_id, pending in self.pending_orders.items():
            if pending.ticker == ticker and pending.side == "sell":
                elapsed = (timestamp - pending.submitted_at).total_seconds()
                if elapsed > timeout_seconds:
                    # Check if we've already exceeded retry limit
                    trade = self.active_trades.get(pending.trade_id)
                    if trade and trade.needs_manual_exit:
                        continue  # Skip, already marked for manual exit

                    logger.info(
                        f"[{self.strategy_name}] [{ticker}] Sell order {order_id} timed out "
                        f"({elapsed:.1f}s > {timeout_seconds}s, trade_id={pending.trade_id[:8]})"
                    )
                    orders_to_cancel.append(order_id)

        # Cancel and retry timed-out orders (separate loop to avoid modifying dict during iteration)
        for order_id in orders_to_cancel:
            self._cancel_pending_sell_order(order_id, timestamp, reason=f"timeout_{timeout_seconds}s")

    def _remove_orphaned_trade(self, trade_id: str, reason: str = "orphaned"):
        """Remove an orphaned trade (position doesn't exist at broker)."""
        trade = self.active_trades.get(trade_id)
        if not trade:
            logger.warning(f"[{self.strategy_name}] Cannot remove orphaned trade - trade_id={trade_id[:8]} not found")
            return

        ticker = trade.ticker
        logger.warning(f"[{self.strategy_name}] [{ticker}] Removing orphaned trade: {trade.shares} shares @ ${trade.entry_price:.4f} ({reason}, trade_id={trade_id[:8]})")

        # Record as a failed/orphaned trade so we have a record
        try:
            trade_record = {
                "ticker": ticker,
                "entry_price": trade.entry_price,
                "exit_price": trade.entry_price,  # No actual exit, use entry price
                "entry_time": trade.entry_time,
                "exit_time": datetime.now(),
                "shares": trade.shares,
                "exit_reason": reason,
                "return_pct": 0,
                "pnl": 0,
                "strategy_params": self.config.to_dict(),
            }
            self._trade_store.save_trade(
                trade=trade_record,
                paper=self.paper,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
                trade_id=trade_id,
            )
        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Failed to record orphaned trade: {e}", exc_info=True)

        # Remove from active trades
        self.active_trades.pop(trade_id, None)

        # Remove from database
        try:
            self._active_trade_store.delete_trade(trade_id)
        except Exception as e:
            logger.error(f"[{self.strategy_name}] [{ticker}] Failed to delete orphaned trade from database: {e}", exc_info=True)

        # Only unsubscribe if no more positions for this ticker
        if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
            self.on_unsubscribe(ticker)

    def reconcile_positions(self, broker_positions: Optional[Dict[str, 'Position']] = None):
        """
        Reconcile our tracked positions with actual broker positions.

        Removes active_trades entries for positions that no longer exist
        (e.g., manually closed via broker dashboard).

        Args:
            broker_positions: Optional pre-fetched positions dict. If not provided,
                            will fetch from broker (but prefer passing to avoid rate limits).
        """
        try:
            if broker_positions is None:
                broker_positions = {p.ticker: p for p in self.trader.get_positions()}

            # Check each active trade
            stale_trade_ids = []
            for trade_id, trade in self.active_trades.items():
                if trade.ticker not in broker_positions:
                    logger.warning(f"[{trade.ticker}] Position no longer exists at broker - removing from tracking (trade_id={trade_id[:8]})")
                    stale_trade_ids.append((trade_id, trade.ticker))

            # Remove stale trades
            for trade_id, ticker in stale_trade_ids:
                del self.active_trades[trade_id]
                # Also remove from database
                self._active_trade_store.delete_trade(trade_id)
                # Only unsubscribe if no more positions for this ticker
                if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
                    self.on_unsubscribe(ticker)

            if stale_trade_ids:
                stale_tickers = [t[1] for t in stale_trade_ids]
                logger.info(f"[{self.strategy_name}] Reconciliation removed {len(stale_trade_ids)} stale positions: {stale_tickers}")

        except Exception as e:
            logger.error(f"[{self.strategy_name}] Position reconciliation failed: {e}", exc_info=True)

    def get_status(self) -> dict:
        """Get current engine status."""
        active_trades = {}
        for trade_id, t in self.active_trades.items():
            current_price = t.last_price if t.last_price > 0 else t.entry_price
            pnl_pct = ((current_price - t.entry_price) / t.entry_price) * 100 if t.entry_price > 0 else 0
            pnl_dollars = (current_price - t.entry_price) * t.shares

            # Calculate timeout time
            timeout_at = t.entry_time + timedelta(minutes=self.config.timeout_minutes)

            active_trades[trade_id] = {
                "trade_id": trade_id,
                "ticker": t.ticker,
                "entry_price": t.entry_price,
                "entry_time": t.entry_time.isoformat(),
                "shares": t.shares,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "pnl_dollars": pnl_dollars,
                "highest": t.highest_since_entry,
                "stop_loss": t.stop_loss_price,
                "take_profit": t.take_profit_price,
                "timeout_at": timeout_at.isoformat(),
                "last_quote_time": t.last_quote_time.isoformat() if t.last_quote_time else None,
                "needs_manual_exit": t.needs_manual_exit,
                "sell_attempts": t.sell_attempts,
            }

        # Build pending entries info with ticker and trade_id
        pending_info = []
        for trade_id, p in self.pending_entries.items():
            pending_info.append({
                "trade_id": trade_id,
                "ticker": p.ticker,
                "alert_time": p.alert_time.isoformat(),
            })

        return {
            "pending_entries": pending_info,
            "active_trades": active_trades,
            "completed_trades": len(self.completed_trades),
        }
