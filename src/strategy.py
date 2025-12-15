"""Strategy engine for live trading."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, parse_qs

from .models import Announcement
from .trading import TradingClient, Position
from .trade_history import get_trade_history_client

logger = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    """Configuration for a trading strategy."""

    # Filters (which alerts to trade)
    channels: List[str] = field(default_factory=lambda: ["select-news"])
    directions: List[str] = field(default_factory=lambda: ["up_right"])
    price_min: float = 1.0
    price_max: float = 10.0
    sessions: List[str] = field(default_factory=lambda: ["premarket", "market"])

    # Entry rules
    consec_green_candles: int = 1
    min_candle_volume: int = 5000

    # Exit rules
    take_profit_pct: float = 10.0
    stop_loss_pct: float = 11.0
    stop_loss_from_open: bool = True
    trailing_stop_pct: float = 7.0
    timeout_minutes: int = 15

    # Position sizing
    stake_amount: float = 50.0  # Dollar amount to stake per trade

    def get_shares(self, price: float) -> int:
        """Calculate number of shares based on stake amount and price."""
        if price <= 0:
            return 0
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
            take_profit_pct=float(params.get("tp", 10)),
            stop_loss_pct=float(params.get("sl", 5)),
            stop_loss_from_open=params.get("sl_open", "0") == "1",
            trailing_stop_pct=float(params.get("trail", 0)),
            timeout_minutes=int(params.get("hold", 60)),
            stake_amount=float(params.get("stake", 50)),
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
            },
            "entry": {
                "consec_green_candles": self.consec_green_candles,
                "min_candle_volume": self.min_candle_volume,
            },
            "exit": {
                "take_profit_pct": self.take_profit_pct,
                "stop_loss_pct": self.stop_loss_pct,
                "stop_loss_from_open": self.stop_loss_from_open,
                "trailing_stop_pct": self.trailing_stop_pct,
                "timeout_minutes": self.timeout_minutes,
            },
            "position": {
                "stake_amount": self.stake_amount,
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
    ticker: str
    announcement: Announcement
    alert_time: datetime
    first_price: Optional[float] = None
    candles: List[CandleBar] = field(default_factory=list)
    current_candle_start: Optional[datetime] = None
    current_candle_data: Optional[dict] = None  # Building current candle


@dataclass
class ActiveTrade:
    """Tracks an active trade position."""
    ticker: str
    announcement: Announcement
    entry_price: float
    entry_time: datetime
    first_candle_open: float
    shares: int
    highest_since_entry: float
    stop_loss_price: float
    take_profit_price: float


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
        on_subscribe: Optional[Callable[[str], None]] = None,
        on_unsubscribe: Optional[Callable[[str], None]] = None,
        paper: bool = True,
    ):
        self.config = config
        self.trader = trader
        self.on_subscribe = on_subscribe  # Called when we need quotes for a ticker
        self.on_unsubscribe = on_unsubscribe  # Called when done with a ticker
        self.paper = paper

        self.pending_entries: Dict[str, PendingEntry] = {}
        self.active_trades: Dict[str, ActiveTrade] = {}
        self.completed_trades: List[dict] = []

        # Trade history persistence
        self._trade_history = get_trade_history_client()

        # Recover any open positions from broker
        self._recover_positions()

    def _recover_positions(self):
        """Recover open positions from broker on startup."""
        logger.info("Checking broker for open positions...")
        try:
            positions = self.trader.get_positions()
            logger.info(f"Broker returned {len(positions)} positions")
            if not positions:
                return

            cfg = self.config
            for pos in positions:
                ticker = pos.ticker
                entry_price = pos.avg_entry_price
                shares = pos.shares

                # Calculate SL/TP based on current config
                stop_loss_price = entry_price * (1 - cfg.stop_loss_pct / 100)
                take_profit_price = entry_price * (1 + cfg.take_profit_pct / 100)

                # Estimate current price from market value
                current_price = pos.market_value / shares if shares > 0 else entry_price

                # Create active trade (we don't have original announcement)
                self.active_trades[ticker] = ActiveTrade(
                    ticker=ticker,
                    announcement=None,  # Lost on restart
                    entry_price=entry_price,
                    entry_time=datetime.now(),  # Approximate
                    first_candle_open=entry_price,
                    shares=shares,
                    highest_since_entry=current_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                )

                logger.info(f"[{ticker}] Recovered position: {shares} shares @ ${entry_price:.2f}, "
                           f"current=${current_price:.2f}, SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f}")

                # Subscribe to quotes
                if self.on_subscribe:
                    self.on_subscribe(ticker)

        except Exception as e:
            logger.error(f"Failed to recover positions: {e}", exc_info=True)

        # Also check for pending orders and subscribe to their quotes
        self._recover_pending_orders()

    def _recover_pending_orders(self):
        """Subscribe to quotes for any pending orders."""
        try:
            orders = self.trader.get_open_orders()
            logger.info(f"Broker returned {len(orders)} open orders")

            for order in orders:
                ticker = order.ticker
                if ticker not in self.active_trades and ticker not in self.pending_entries:
                    logger.info(f"[{ticker}] Found pending {order.side} order for {order.shares} shares ({order.status})")
                    # Subscribe to quotes so we can track when it fills
                    if self.on_subscribe:
                        self.on_subscribe(ticker)
        except Exception as e:
            logger.error(f"Failed to recover pending orders: {e}", exc_info=True)

    def on_alert(self, announcement: Announcement) -> bool:
        """
        Handle new alert from Discord.

        Returns True if alert passes filters and is being tracked.
        """
        ticker = announcement.ticker

        # Already tracking or trading this ticker
        if ticker in self.pending_entries or ticker in self.active_trades:
            logger.info(f"[{ticker}] Already tracking, ignoring duplicate alert")
            return False

        # Check filters
        if not self._passes_filters(announcement):
            return False

        logger.info(f"[{ticker}] Alert passed filters, starting to track")

        # Start tracking for entry
        self.pending_entries[ticker] = PendingEntry(
            ticker=ticker,
            announcement=announcement,
            alert_time=datetime.now(),
        )

        # Request quote subscription
        if self.on_subscribe:
            self.on_subscribe(ticker)

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
        # Check pending entries
        if ticker in self.pending_entries:
            self._check_entry(ticker, price, volume, timestamp)

        # Check active trades
        if ticker in self.active_trades:
            trade = self.active_trades[ticker]
            pnl_pct = ((price - trade.entry_price) / trade.entry_price) * 100
            logger.info(f"[{ticker}] ${price:.2f} ({pnl_pct:+.1f}%) | SL=${trade.stop_loss_price:.2f} TP=${trade.take_profit_price:.2f}")
            self._check_exit(ticker, price, timestamp)

    def _passes_filters(self, ann: Announcement) -> bool:
        """Check if announcement passes all filters."""
        cfg = self.config

        # Channel filter
        if cfg.channels and ann.channel not in cfg.channels:
            logger.debug(f"[{ann.ticker}] Filtered: channel {ann.channel} not in {cfg.channels}")
            return False

        # Direction filter
        if cfg.directions and ann.direction not in cfg.directions:
            logger.debug(f"[{ann.ticker}] Filtered: direction {ann.direction} not in {cfg.directions}")
            return False

        # Session filter
        if cfg.sessions and ann.market_session not in cfg.sessions:
            logger.debug(f"[{ann.ticker}] Filtered: session {ann.market_session} not in {cfg.sessions}")
            return False

        # Price filter (using price_threshold from announcement as proxy)
        # Note: Real price check happens at entry time
        if ann.price_threshold:
            if ann.price_threshold <= cfg.price_min or ann.price_threshold > cfg.price_max:
                logger.debug(f"[{ann.ticker}] Filtered: price ${ann.price_threshold} outside ${cfg.price_min}-${cfg.price_max}")
                return False

        return True

    def _check_entry(self, ticker: str, price: float, volume: int, timestamp: datetime):
        """Check if entry conditions are met for a pending entry."""
        pending = self.pending_entries[ticker]
        cfg = self.config

        # Check timeout - abandon if too long since alert
        time_since_alert = (timestamp - pending.alert_time).total_seconds() / 60
        if time_since_alert > cfg.timeout_minutes:
            logger.info(f"[{ticker}] Entry timeout ({time_since_alert:.1f}m > {cfg.timeout_minutes}m)")
            self._abandon_pending(ticker)
            return

        # Price filter at actual price
        if price <= cfg.price_min or price > cfg.price_max:
            logger.debug(f"[{ticker}] Price ${price:.2f} outside filter range")
            return

        # Record first price
        if pending.first_price is None:
            pending.first_price = price
            logger.info(f"[{ticker}] First price: ${price:.2f}")

        # If no consecutive candle requirement, enter immediately
        if cfg.consec_green_candles == 0:
            self._execute_entry(ticker, price, timestamp)
            return

        # Build candles from quote updates (minute candles)
        candle_start = timestamp.replace(second=0, microsecond=0)

        if pending.current_candle_start != candle_start:
            # New candle starting - finalize previous if exists
            if pending.current_candle_data:
                candle = CandleBar(
                    timestamp=pending.current_candle_start,
                    open=pending.current_candle_data["open"],
                    high=pending.current_candle_data["high"],
                    low=pending.current_candle_data["low"],
                    close=pending.current_candle_data["close"],
                    volume=pending.current_candle_data["volume"],
                )
                pending.candles.append(candle)
                logger.debug(f"[{ticker}] Candle closed: O={candle.open:.2f} H={candle.high:.2f} L={candle.low:.2f} C={candle.close:.2f} V={candle.volume} green={candle.is_green}")

            # Start new candle
            pending.current_candle_start = candle_start
            pending.current_candle_data = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
        else:
            # Update current candle
            if pending.current_candle_data:
                pending.current_candle_data["high"] = max(pending.current_candle_data["high"], price)
                pending.current_candle_data["low"] = min(pending.current_candle_data["low"], price)
                pending.current_candle_data["close"] = price
                pending.current_candle_data["volume"] = volume  # Use latest volume

        # Check for consecutive green candles with volume
        green_count = 0
        for candle in reversed(pending.candles):
            if candle.is_green and candle.volume >= cfg.min_candle_volume:
                green_count += 1
            else:
                break

        if green_count >= cfg.consec_green_candles:
            logger.info(f"[{ticker}] Entry condition met: {green_count} consecutive green candles")
            self._execute_entry(ticker, price, timestamp)

    def _execute_entry(self, ticker: str, price: float, timestamp: datetime):
        """Execute entry order."""
        pending = self.pending_entries.pop(ticker)
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

        # Calculate shares from stake amount
        shares = cfg.get_shares(price)
        if shares <= 0:
            logger.error(f"[{ticker}] Cannot calculate shares for price ${price:.2f}")
            return

        logger.info(f"[{ticker}] ENTRY @ ${price:.2f}, {shares} shares (${cfg.stake_amount}), SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f}")

        # Execute buy order
        try:
            order = self.trader.buy(ticker, shares)
            logger.info(f"[{ticker}] Buy order submitted: {order.order_id} ({order.status})")
        except Exception as e:
            logger.error(f"[{ticker}] Buy order failed: {e}")
            return

        # Track active trade
        self.active_trades[ticker] = ActiveTrade(
            ticker=ticker,
            announcement=pending.announcement,
            entry_price=price,
            entry_time=timestamp,
            first_candle_open=pending.first_price or price,
            shares=shares,
            highest_since_entry=price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )

    def _check_exit(self, ticker: str, price: float, timestamp: datetime):
        """Check exit conditions for an active trade."""
        trade = self.active_trades[ticker]
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
            self._execute_exit(ticker, exit_price, exit_reason, timestamp)

    def _execute_exit(self, ticker: str, price: float, reason: str, timestamp: datetime):
        """Execute exit order."""
        trade = self.active_trades.pop(ticker)

        return_pct = ((price - trade.entry_price) / trade.entry_price) * 100

        logger.info(f"[{ticker}] EXIT @ ${price:.2f} ({reason}) - Return: {return_pct:+.2f}%")

        # Execute sell order
        try:
            order = self.trader.sell(ticker, trade.shares)
            logger.info(f"[{ticker}] Sell order submitted: {order.order_id} ({order.status})")
        except Exception as e:
            logger.error(f"[{ticker}] Sell order failed: {e}")

        # Record completed trade with full details
        pnl = (price - trade.entry_price) * trade.shares
        completed = {
            "ticker": ticker,
            "entry_price": trade.entry_price,
            "entry_time": trade.entry_time.isoformat(),
            "exit_price": price,
            "exit_time": timestamp.isoformat(),
            "exit_reason": reason,
            "return_pct": return_pct,
            "shares": trade.shares,
            "pnl": pnl,
            "strategy_params": self.config.to_dict(),
        }
        self.completed_trades.append(completed)

        # Persist to database
        try:
            self._trade_history.save_trade(completed, paper=self.paper)
        except Exception as e:
            logger.error(f"[{ticker}] Failed to save trade to database: {e}")

        # Unsubscribe from quotes
        if self.on_unsubscribe:
            self.on_unsubscribe(ticker)

    def _abandon_pending(self, ticker: str):
        """Abandon a pending entry (timeout or other reason)."""
        if ticker in self.pending_entries:
            del self.pending_entries[ticker]
            logger.info(f"[{ticker}] Abandoned pending entry")
            if self.on_unsubscribe:
                self.on_unsubscribe(ticker)

    def get_status(self) -> dict:
        """Get current engine status."""
        return {
            "pending_entries": list(self.pending_entries.keys()),
            "active_trades": {
                ticker: {
                    "entry_price": t.entry_price,
                    "entry_time": t.entry_time.isoformat(),
                    "highest": t.highest_since_entry,
                    "stop_loss": t.stop_loss_price,
                    "take_profit": t.take_profit_price,
                    "shares": t.shares,
                }
                for ticker, t in self.active_trades.items()
            },
            "completed_trades": len(self.completed_trades),
        }
