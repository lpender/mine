"""Strategy engine for live trading."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, parse_qs

from .models import Announcement
from .trading import TradingClient, Position
from .trade_history import get_trade_history_client
from .active_trade_store import get_active_trade_store
from .order_store import get_order_store
from .trade_logger import log_buy_fill, log_sell_fill

logger = logging.getLogger(__name__)
# Separate logger for verbose quote/candle logs - writes to logs/quotes.log
quotes_logger = logging.getLogger(__name__ + '.quotes')
# Separate logger for real-time status updates - writes to logs/trading.log (not stdout)
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

    # Entry rules
    consec_green_candles: int = 1
    min_candle_volume: int = 5000
    entry_window_minutes: int = 5  # How long to wait for entry conditions after alert

    # Exit rules
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
        paper: bool = True,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ):
        self.config = config
        self.trader = trader
        self.on_subscribe = on_subscribe  # Called when we need quotes for a ticker (returns True if subscribed)
        self.on_unsubscribe = on_unsubscribe  # Called when done with a ticker
        self.paper = paper
        self.strategy_id = strategy_id
        self.strategy_name = strategy_name or "default"

        # Keyed by trade_id (UUID) - allows multiple positions per ticker
        self.pending_entries: Dict[str, PendingEntry] = {}  # trade_id -> PendingEntry
        self.pending_orders: Dict[str, PendingOrder] = {}   # order_id -> PendingOrder
        self.active_trades: Dict[str, ActiveTrade] = {}     # trade_id -> ActiveTrade
        self.completed_trades: List[dict] = []

        # Shared candle data per ticker (not per pending entry)
        self._ticker_candles: Dict[str, List[CandleBar]] = {}  # ticker -> completed candles
        self._ticker_building_candle: Dict[str, dict] = {}  # ticker -> current candle being built
        self._ticker_candle_start: Dict[str, datetime] = {}  # ticker -> start time of building candle

        # Trade history persistence
        self._trade_history = get_trade_history_client()
        self._active_trade_store = get_active_trade_store()
        self._order_store = get_order_store()

        # Recover any open positions from database and broker
        self._recover_positions()

    def _recover_positions(self):
        """Recover open positions from database and broker on startup."""
        # First, load from our database (has accurate entry times, SL/TP)
        logger.info(f"Loading active trades from database for strategy {self.strategy_id}...")
        try:
            db_trades = self._active_trade_store.get_trades_for_strategy(self.strategy_id)
            logger.info(f"Database returned {len(db_trades)} active trades")

            for t in db_trades:
                # Generate trade_id if not present (migration from old data)
                trade_id = t.trade_id if t.trade_id else str(uuid.uuid4())
                if not t.trade_id:
                    logger.info(f"[{t.ticker}] Generated trade_id for legacy trade: {trade_id[:8]}")

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
                logger.info(f"[{t.ticker}] Recovered from DB: {t.shares} shares @ ${t.entry_price:.2f}, "
                           f"SL=${t.stop_loss_price:.2f}, TP=${t.take_profit_price:.2f} (trade_id={trade_id[:8]})")

                # Subscribe to quotes
                if self.on_subscribe:
                    subscribed = self.on_subscribe(t.ticker)
                    if not subscribed:
                        logger.warning(f"[{t.ticker}] Could not subscribe for quotes (at limit) - position recovered but won't get live updates until slot available")

        except Exception as e:
            logger.error(f"Failed to load from database: {e}", exc_info=True)

        # Verify our positions still exist at broker (positions may have been manually closed)
        # NOTE: We do NOT auto-claim broker positions. A strategy only owns positions that
        # were explicitly created through its entry flow (on_alert -> on_buy_fill).
        # This prevents all strategies from claiming all broker positions.
        logger.info("Verifying positions with broker...")
        try:
            positions = self.trader.get_positions()
            broker_tickers = {p.ticker for p in positions}
            logger.info(f"Broker has positions in: {broker_tickers}")

            # Check for positions we track but broker doesn't have (manually closed)
            for trade_id, trade in self.active_trades.items():
                if trade.ticker not in broker_tickers:
                    logger.warning(f"[{trade.ticker}] Position not found at broker - may have been manually closed (trade_id={trade_id[:8]})")

        except Exception as e:
            logger.error(f"Failed to recover from broker: {e}", exc_info=True)

        # Also check for pending orders
        self._recover_pending_orders()

    def _recover_pending_orders(self):
        """Check for pending orders - we don't track these, just log them.

        Note: We don't subscribe to quotes for pending orders because we don't
        have the context (announcement, entry time, etc.) to manage them properly.
        They'll be picked up on the next startup after they fill.
        """
        try:
            orders = self.trader.get_open_orders()
            if orders:
                logger.info(f"Broker has {len(orders)} open orders (not tracking)")
                for order in orders:
                    logger.info(f"[{order.ticker}] Pending {order.side} order: {order.shares} shares ({order.status})")
        except Exception as e:
            logger.error(f"Failed to check pending orders: {e}", exc_info=True)

    # --- Helper methods for multi-position support ---

    def _get_pending_for_ticker(self, ticker: str) -> List[PendingEntry]:
        """Get all pending entries for a ticker."""
        return [p for p in self.pending_entries.values() if p.ticker == ticker]

    def _get_trades_for_ticker(self, ticker: str) -> List[ActiveTrade]:
        """Get all active trades for a ticker."""
        return [t for t in self.active_trades.values() if t.ticker == ticker]

    def _has_pending_or_trade(self, ticker: str) -> bool:
        """Check if we have any pending entries or active trades for a ticker."""
        return bool(self._get_pending_for_ticker(ticker) or self._get_trades_for_ticker(ticker))

    def _get_candles_for_ticker(self, ticker: str) -> List[CandleBar]:
        """Get shared candle data for a ticker."""
        return self._ticker_candles.get(ticker, [])

    def _set_candles_for_ticker(self, ticker: str, candles: List[CandleBar]):
        """Set shared candle data for a ticker."""
        self._ticker_candles[ticker] = candles

    def _clear_candles_for_ticker(self, ticker: str):
        """Clear shared candle data when no longer tracking ticker."""
        self._ticker_candles.pop(ticker, None)

    def on_alert(self, announcement: Announcement) -> bool:
        """
        Handle new alert from Discord.

        Returns True if alert passes filters and is being tracked.
        Multiple alerts for the same ticker create independent pending entries.
        """
        ticker = announcement.ticker

        # Check filters
        if not self._passes_filters(announcement):
            return False

        # Check if tradeable on broker before tracking
        tradeable, reason = self.trader.is_tradeable(ticker)
        if not tradeable:
            logger.warning(f"[{ticker}] Not tradeable: {reason}")
            return False

        logger.info(f"[{ticker}] Alert passed filters, checking subscription availability")

        # Request quote subscription only if not already subscribed for this ticker
        already_tracking = self._has_pending_or_trade(ticker)
        if not already_tracking and self.on_subscribe:
            subscribed = self.on_subscribe(ticker)
            if not subscribed:
                logger.warning(f"[{ticker}] Cannot subscribe for quotes (at websocket limit) - rejecting alert")
                return False

        # Generate unique trade ID for this entry
        trade_id = str(uuid.uuid4())

        # Start tracking for entry (keyed by trade_id, not ticker)
        existing_count = len(self._get_pending_for_ticker(ticker)) + len(self._get_trades_for_ticker(ticker))
        logger.info(f"[{ticker}] Starting to track for entry (trade_id={trade_id[:8]}, existing positions: {existing_count})")
        self.pending_entries[trade_id] = PendingEntry(
            trade_id=trade_id,
            ticker=ticker,
            announcement=announcement,
            alert_time=datetime.now(),
        )

        return True

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
            status_logger.info(f"[{ticker}] ${price:.2f} ({pnl_pct:+.1f}%) | SL=${trade.stop_loss_price:.2f} TP=${trade.take_profit_price:.2f} (trade={trade.trade_id[:8]})")
            self._check_exit(trade.trade_id, price, timestamp)

    def _passes_filters(self, ann: Announcement) -> bool:
        """Check if announcement passes all filters."""
        cfg = self.config

        # Channel filter
        if cfg.channels and ann.channel not in cfg.channels:
            logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': channel '{ann.channel}' not in {cfg.channels}")
            return False

        # Direction filter
        if cfg.directions and ann.direction not in cfg.directions:
            logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': direction '{ann.direction}' not in {cfg.directions}")
            return False

        # Session filter
        if cfg.sessions and ann.market_session not in cfg.sessions:
            logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': session '{ann.market_session}' not in {cfg.sessions}")
            return False

        # Price filter (using price_threshold from announcement as proxy)
        # Note: Real price check happens at entry time
        if ann.price_threshold:
            if ann.price_threshold <= cfg.price_min or ann.price_threshold > cfg.price_max:
                logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': price ${ann.price_threshold} outside ${cfg.price_min}-${cfg.price_max}")
                return False

        # Country blacklist filter
        if cfg.country_blacklist and ann.country in cfg.country_blacklist:
            logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': country '{ann.country}' in blacklist")
            return False

        # Intraday mentions filter (must be less than max)
        if cfg.max_intraday_mentions is not None and ann.mention_count is not None:
            if ann.mention_count >= cfg.max_intraday_mentions:
                logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': {ann.mention_count} mentions >= max {cfg.max_intraday_mentions}")
                return False

        # Financing headline filter (offerings, reverse splits, etc.)
        if cfg.exclude_financing_headlines and ann.headline_is_financing:
            logger.info(f"[{ann.ticker}] Filtered by '{self.strategy_name}': financing headline ({ann.headline_financing_type})")
            return False

        return True

    def _check_entry(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """Check if entry conditions are met for any pending entries for this ticker.

        Candle data is shared across all pending entries for the same ticker.
        Each pending entry is evaluated independently for entry conditions.
        """
        cfg = self.config

        # Log every quote for debugging (to quotes.log)
        quotes_logger.info(f"[{ticker}] QUOTE: ${price:.4f} vol={volume:,} | filter: ${cfg.price_min:.2f}-${cfg.price_max:.2f}")

        # Price filter at actual price
        if price <= cfg.price_min or price > cfg.price_max:
            quotes_logger.info(f"[{ticker}] FILTERED: ${price:.4f} outside ${cfg.price_min:.2f}-${cfg.price_max:.2f}")
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

                # Detailed candle close logging (to quotes.log file)
                color = "GREEN" if candle.is_green else "RED"
                meets_vol = candle.volume >= cfg.min_candle_volume
                qualifies = candle.is_green and meets_vol
                quotes_logger.info(f"")
                quotes_logger.info(f"[{ticker}] ━━━ CANDLE CLOSED ━━━")
                quotes_logger.info(f"[{ticker}] {color} candle | O={candle.open:.2f} H={candle.high:.2f} L={candle.low:.2f} C={candle.close:.2f}")
                quotes_logger.info(f"[{ticker}] Volume: {candle.volume:,} {'>=✓' if meets_vol else '<✗'} {cfg.min_candle_volume:,} threshold")
                quotes_logger.info(f"[{ticker}] Qualifies for entry: {'YES ✓' if qualifies else 'NO ✗'}")
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

        # Log current candle volume progress (to quotes.log file)
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

        # Log completed candles status (to quotes.log file)
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
            if time_since_alert > cfg.entry_window_minutes:
                logger.info(f"[{ticker}] Entry window timeout for trade_id={pending.trade_id[:8]} ({time_since_alert:.1f}m > {cfg.entry_window_minutes}m)")
                self._abandon_pending(pending.trade_id)
                continue

            # Record first price for this pending entry
            if pending.first_price is None:
                pending.first_price = price
                logger.info(f"[{ticker}] First price for trade_id={pending.trade_id[:8]}: ${price:.2f}")

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
                        logger.info(f"[{ticker}] 🚀🚀🚀 EARLY ENTRY! Building candle hit {curr_vol:,} volume while GREEN! (trade_id={pending.trade_id[:8]})")
                        logger.info(f"[{ticker}] {completed_green_count} completed + 1 building = {green_count} green candles")
                        logger.info(f"{'='*60}")
                        logger.info(f"")
                        self._execute_entry(pending.trade_id, price, timestamp, trigger=f"early_entry_{green_count}_green")
                        continue

            if completed_green_count >= cfg.consec_green_candles:
                # Fallback: enter on completed candles if early entry didn't trigger
                logger.info(f"")
                logger.info(f"{'='*60}")
                logger.info(f"[{ticker}] 🚀🚀🚀 ENTRY CONDITION MET! {completed_green_count} completed green candles with volume! (trade_id={pending.trade_id[:8]})")
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
                logger.info(f"[{ticker}] Entry window timeout for trade_id={pending.trade_id[:8]} ({time_since_alert:.1f}m > {cfg.entry_window_minutes}m)")
                self._abandon_pending(pending.trade_id)

    def _execute_entry(self, trade_id: str, price: float, timestamp: datetime, trigger: str = "entry_signal"):
        """Execute entry order.

        Args:
            trade_id: The trade_id of the pending entry to execute
            trigger: Reason for entry, e.g., "early_entry", "completed_candles", "no_candle_req"
        """
        pending = self.pending_entries.pop(trade_id)
        ticker = pending.ticker
        cfg = self.config

        # Calculate stop loss price
        if cfg.stop_loss_from_open and pending.first_price:
            stop_loss_price = pending.first_price * (1 - cfg.stop_loss_pct / 100)
            # Sanity check: stop should not be above entry
            if stop_loss_price > price:
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
            logger.error(f"[{ticker}] Cannot calculate shares for price ${price:.2f} (trade_id={trade_id[:8]})")
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
        logger.info(f"[{ticker}] 💰💰💰 EXECUTING BUY ORDER 💰💰💰")
        logger.info(f"[{ticker}] ENTRY @ ${price:.4f}, {sizing_info}")
        logger.info(f"[{ticker}] Config: price_min=${cfg.price_min:.2f}, price_max=${cfg.price_max:.2f}")
        logger.info(f"[{ticker}] SL=${stop_loss_price:.4f}, TP=${take_profit_price:.4f}")
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
            paper=self.paper,
        )

        # Execute buy order (limit order at current price + slippage)
        try:
            order = self.trader.buy(ticker, shares, limit_price=price)
            logger.info(f"[{ticker}] ✅ Buy order submitted: {order.order_id} ({order.status})")

            # Update database with broker order ID
            if db_order_id:
                self._order_store.update_broker_order_id(db_order_id, order.order_id)
                self._order_store.record_event(
                    event_type="submitted",
                    event_timestamp=timestamp,
                    order_id=db_order_id,
                    broker_order_id=order.order_id,
                )

        except Exception as e:
            logger.error(f"[{ticker}] Buy order failed: {e}")
            # Update order status to rejected
            if db_order_id:
                self._order_store.update_order_status(order_id=db_order_id, status="rejected")
                self._order_store.record_event(
                    event_type="rejected",
                    event_timestamp=timestamp,
                    order_id=db_order_id,
                    raw_data={"error": str(e)},
                )
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
        )
        logger.info(f"[{ticker}] Order {order.order_id} pending fill confirmation (trade_id={trade_id[:8]})")

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
            logger.warning(f"[{ticker}] Fill for unknown order {order_id}")
            return

        logger.info(f"[{ticker}] ✅ BUY FILLED: {shares} shares @ ${filled_price:.4f}")

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
            logger.warning(f"[{ticker}] Missing trade_id on buy order, generating new: {trade_id[:8]}")

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
        )

        logger.info(f"[{ticker}] Created active trade (trade_id={trade_id[:8]})")

        # Persist to database
        self._active_trade_store.save_trade(
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
            logger.warning(f"[{ticker}] Fill for unknown sell order {order_id}")
            return

        return_pct = ((filled_price - pending.entry_price) / pending.entry_price) * 100
        pnl = (filled_price - pending.entry_price) * shares

        logger.info(f"[{ticker}] ✅ SELL FILLED: {shares} shares @ ${filled_price:.4f} | P&L: ${pnl:+.2f} ({return_pct:+.2f}%)")

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
            self._trade_history.save_trade(
                trade=trade_record,
                paper=self.paper,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
            )
        except Exception as e:
            logger.error(f"[{ticker}] Failed to record trade: {e}")

        # Remove from database active trades using trade_id
        trade_id = pending.trade_id
        if trade_id:
            try:
                self._active_trade_store.delete_trade(trade_id)
            except Exception as e:
                logger.error(f"[{ticker}] Failed to delete trade_id={trade_id[:8]} from active_trades: {e}")
        else:
            logger.warning(f"[{ticker}] No trade_id on sell order - skipping active_trade_store delete")

        # Only unsubscribe if no more pending entries or active trades for this ticker
        if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
            self.on_unsubscribe(ticker)

    def on_order_canceled(self, order_id: str, ticker: str, side: str, timestamp: Optional[datetime] = None):
        """Handle order cancellation."""
        pending = self.pending_orders.pop(order_id, None)
        if pending:
            logger.warning(f"[{ticker}] Order {order_id} ({side}) was CANCELED")

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
                # Buy canceled - just unsubscribe
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)
            else:
                # Sell canceled - need to re-add to active trades or retry
                logger.warning(f"[{ticker}] Sell order canceled - position still open!")

    def on_order_rejected(self, order_id: str, ticker: str, side: str, reason: str, timestamp: Optional[datetime] = None):
        """Handle order rejection."""
        pending = self.pending_orders.pop(order_id, None)
        if pending:
            logger.error(f"[{ticker}] Order {order_id} ({side}) was REJECTED: {reason}")

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
                # Buy rejected - just unsubscribe
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)
            else:
                # Sell rejected - position still open
                logger.error(f"[{ticker}] Sell order rejected - position still open!")

    def _check_exit(self, trade_id: str, price: float, timestamp: datetime):
        """Check exit conditions for a specific active trade."""
        trade = self.active_trades.get(trade_id)
        if not trade:
            return

        ticker = trade.ticker
        cfg = self.config

        # Update highest price for trailing stop
        if price > trade.highest_since_entry:
            trade.highest_since_entry = price

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
            logger.warning(f"No active trade found for trade_id={trade_id[:8]}")
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
                        logger.info(f"[{ticker}] Found existing sell order at broker ({order.shares} shares), skipping (trade_id={trade_id[:8]})")
                        # Remove this trade from active trades since we're already exiting
                        self.active_trades.pop(trade_id, None)
                        # Only unsubscribe if no more positions for this ticker
                        if not self._has_pending_or_trade(ticker) and self.on_unsubscribe:
                            self.on_unsubscribe(ticker)
                        return
            except Exception as e:
                logger.warning(f"[{ticker}] Could not check broker orders: {e}")

        return_pct = ((price - trade.entry_price) / trade.entry_price) * 100

        logger.info(f"[{ticker}] EXIT @ ${price:.2f} ({reason}) - Return: {return_pct:+.2f}%")

        # Create order record in database before submitting to broker
        db_order_id = self._order_store.create_order(
            ticker=ticker,
            side="sell",
            order_type="limit",
            requested_shares=trade.shares,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            limit_price=price,
            paper=self.paper,
        )

        # Execute sell order (limit order at current price - slippage)
        try:
            order = self.trader.sell(ticker, trade.shares, limit_price=price)
            logger.info(f"[{ticker}] Sell order submitted: {order.order_id} ({order.status})")

            # Update database with broker order ID
            if db_order_id:
                self._order_store.update_broker_order_id(db_order_id, order.order_id)
                self._order_store.record_event(
                    event_type="submitted",
                    event_timestamp=timestamp,
                    order_id=db_order_id,
                    broker_order_id=order.order_id,
                )

        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"[{ticker}] Sell order failed: {e}")

            # Update order status to rejected
            if db_order_id:
                self._order_store.update_order_status(order_id=db_order_id, status="rejected")
                self._order_store.record_event(
                    event_type="rejected",
                    event_timestamp=timestamp,
                    order_id=db_order_id,
                    raw_data={"error": str(e)},
                )

            # Check if error indicates position doesn't exist at broker
            if "insufficient qty" in error_msg or "position does not exist" in error_msg:
                # Verify with broker - maybe position was already closed
                broker_position = self.trader.get_position(ticker)
                if broker_position is None or broker_position.shares == 0:
                    logger.warning(f"[{ticker}] Position not found at broker - removing orphaned trade from tracking")
                    self._remove_orphaned_trade(trade_id, reason="position_not_found")
                    return
                elif broker_position.shares != trade.shares:
                    # Position exists but with different quantity - update our tracking
                    logger.warning(f"[{ticker}] Broker has {broker_position.shares} shares, we tracked {trade.shares} - updating")
                    trade.shares = broker_position.shares

            # Track sell attempts - after 3 failures, stop retrying
            trade.sell_attempts += 1
            if trade.sell_attempts >= 3:
                trade.needs_manual_exit = True
                logger.error(f"[{ticker}] ⚠️ SELL FAILED 3 TIMES - needs manual exit! Position: {trade.shares} shares @ ${trade.entry_price:.4f}")
            else:
                logger.warning(f"[{ticker}] Sell attempt {trade.sell_attempts}/3 failed - will retry on next exit signal")
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
        )
        logger.info(f"[{ticker}] Sell order {order.order_id} pending fill confirmation (trade_id={trade_id[:8]})")

        # Remove from active trades (we have a pending sell order now)
        self.active_trades.pop(trade_id, None)

    def _abandon_pending(self, trade_id: str):
        """Abandon a pending entry (timeout or other reason)."""
        pending = self.pending_entries.pop(trade_id, None)
        if pending:
            ticker = pending.ticker
            logger.info(f"[{ticker}] Abandoned pending entry (trade_id={trade_id[:8]})")
            # Only unsubscribe if no more pending entries or active trades for this ticker
            if not self._has_pending_or_trade(ticker):
                # Also clear shared candle data for this ticker
                self._clear_candles_for_ticker(ticker)
                self._ticker_building_candle.pop(ticker, None)
                self._ticker_candle_start.pop(ticker, None)
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)

    def _remove_orphaned_trade(self, trade_id: str, reason: str = "orphaned"):
        """Remove an orphaned trade (position doesn't exist at broker)."""
        trade = self.active_trades.get(trade_id)
        if not trade:
            logger.warning(f"Cannot remove orphaned trade - trade_id={trade_id[:8]} not found")
            return

        ticker = trade.ticker
        logger.warning(f"[{ticker}] Removing orphaned trade: {trade.shares} shares @ ${trade.entry_price:.4f} ({reason}, trade_id={trade_id[:8]})")

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
            self._trade_history.save_trade(
                trade=trade_record,
                paper=self.paper,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
            )
        except Exception as e:
            logger.error(f"[{ticker}] Failed to record orphaned trade: {e}")

        # Remove from active trades
        self.active_trades.pop(trade_id, None)

        # Remove from database
        try:
            self._active_trade_store.delete_trade(trade_id)
        except Exception as e:
            logger.error(f"[{ticker}] Failed to delete orphaned trade from database: {e}")

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
                logger.info(f"Reconciliation removed {len(stale_trade_ids)} stale positions: {stale_tickers}")

        except Exception as e:
            logger.error(f"Position reconciliation failed: {e}", exc_info=True)

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
