"""Strategy engine for live trading."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, parse_qs

from .models import Announcement
from .trading import TradingClient, Position
from .trade_history import get_trade_history_client
from .active_trade_store import get_active_trade_store

logger = logging.getLogger(__name__)
# Separate logger for verbose quote/candle logs - writes to logs/quotes.log
quotes_logger = logging.getLogger(__name__ + '.quotes')


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
    last_price: float = 0.0  # Updated by quotes
    last_quote_time: Optional[datetime] = None


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
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ):
        self.config = config
        self.trader = trader
        self.on_subscribe = on_subscribe  # Called when we need quotes for a ticker
        self.on_unsubscribe = on_unsubscribe  # Called when done with a ticker
        self.paper = paper
        self.strategy_id = strategy_id
        self.strategy_name = strategy_name or "default"

        self.pending_entries: Dict[str, PendingEntry] = {}
        self.active_trades: Dict[str, ActiveTrade] = {}
        self.completed_trades: List[dict] = []

        # Trade history persistence
        self._trade_history = get_trade_history_client()
        self._active_trade_store = get_active_trade_store()

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
                self.active_trades[t.ticker] = ActiveTrade(
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
                           f"SL=${t.stop_loss_price:.2f}, TP=${t.take_profit_price:.2f}")

                # Subscribe to quotes
                if self.on_subscribe:
                    self.on_subscribe(t.ticker)

        except Exception as e:
            logger.error(f"Failed to load from database: {e}", exc_info=True)

        # Then reconcile with broker - add any positions we don't know about
        logger.info("Checking broker for additional positions...")
        try:
            positions = self.trader.get_positions()
            logger.info(f"Broker returned {len(positions)} positions")

            cfg = self.config
            for pos in positions:
                ticker = pos.ticker
                if ticker in self.active_trades:
                    continue  # Already recovered from DB

                entry_price = pos.avg_entry_price
                shares = pos.shares

                # Calculate SL/TP based on current config
                stop_loss_price = entry_price * (1 - cfg.stop_loss_pct / 100)
                take_profit_price = entry_price * (1 + cfg.take_profit_pct / 100)

                # Estimate current price from market value
                current_price = pos.market_value / shares if shares > 0 else entry_price

                # Create active trade
                self.active_trades[ticker] = ActiveTrade(
                    ticker=ticker,
                    announcement=None,
                    entry_price=entry_price,
                    entry_time=datetime.now(),  # Approximate
                    first_candle_open=entry_price,
                    shares=shares,
                    highest_since_entry=current_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                )

                logger.info(f"[{ticker}] Recovered from broker: {shares} shares @ ${entry_price:.2f}, "
                           f"SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f}")

                # Save to our DB for next time
                self._active_trade_store.save_trade(
                    ticker=ticker,
                    strategy_id=self.strategy_id,
                    strategy_name=self.strategy_name,
                    entry_price=entry_price,
                    entry_time=datetime.now(),
                    first_candle_open=entry_price,
                    shares=shares,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    highest_since_entry=current_price,
                    paper=self.paper,
                )

                # Subscribe to quotes
                if self.on_subscribe:
                    self.on_subscribe(ticker)

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

        # Check if tradeable on broker before tracking
        tradeable, reason = self.trader.is_tradeable(ticker)
        if not tradeable:
            logger.warning(f"[{ticker}] Not tradeable: {reason}")
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
            # Update last price for display
            trade.last_price = price
            trade.last_quote_time = timestamp
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

        # Log every quote for debugging (to quotes.log)
        quotes_logger.info(f"[{ticker}] QUOTE: ${price:.4f} vol={volume:,} | filter: ${cfg.price_min:.2f}-${cfg.price_max:.2f}")

        # Check timeout - abandon if too long since alert
        time_since_alert = (timestamp - pending.alert_time).total_seconds() / 60
        if time_since_alert > cfg.timeout_minutes:
            logger.info(f"[{ticker}] Entry timeout ({time_since_alert:.1f}m > {cfg.timeout_minutes}m)")
            self._abandon_pending(ticker)
            return

        # Price filter at actual price
        if price <= cfg.price_min or price > cfg.price_max:
            quotes_logger.info(f"[{ticker}] FILTERED: ${price:.4f} outside ${cfg.price_min:.2f}-${cfg.price_max:.2f}")
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
                pending.current_candle_data["volume"] += volume  # Sum volume from all 1-second bars

        # Log current candle volume progress (to quotes.log file)
        if pending.current_candle_data:
            curr_vol = pending.current_candle_data["volume"]
            curr_open = pending.current_candle_data["open"]
            curr_close = pending.current_candle_data["close"]
            is_green = curr_close > curr_open
            pct_of_threshold = (curr_vol / cfg.min_candle_volume * 100) if cfg.min_candle_volume > 0 else 0
            color = "GREEN" if is_green else "RED"
            quotes_logger.info(
                f"[{ticker}] CANDLE BUILDING: {color} | Vol: {curr_vol:,} / {cfg.min_candle_volume:,} ({pct_of_threshold:.0f}%) | "
                f"O={curr_open:.2f} C={curr_close:.2f}"
            )

        # Check for consecutive green candles with volume
        green_count = 0
        for candle in reversed(pending.candles):
            if candle.is_green and candle.volume >= cfg.min_candle_volume:
                green_count += 1
            else:
                break

        # Log completed candles status (to quotes.log file)
        if pending.candles:
            last_candle = pending.candles[-1]
            meets_vol = last_candle.volume >= cfg.min_candle_volume
            quotes_logger.info(
                f"[{ticker}] LAST COMPLETED CANDLE: {'GREEN' if last_candle.is_green else 'RED'} | "
                f"Vol: {last_candle.volume:,} {'>=✓' if meets_vol else '<✗'} {cfg.min_candle_volume:,} | "
                f"Green candles with vol: {green_count}/{cfg.consec_green_candles} needed"
            )

        if green_count >= cfg.consec_green_candles:
            logger.info(f"")
            logger.info(f"{'='*60}")
            logger.info(f"[{ticker}] 🚀🚀🚀 ENTRY CONDITION MET! {green_count} consecutive green candles with volume!")
            logger.info(f"{'='*60}")
            logger.info(f"")
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

        # Get previous candle volume for volume-based sizing
        prev_candle_volume = None
        if pending.candles:
            prev_candle_volume = pending.candles[-1].volume

        # Calculate shares based on position sizing mode
        shares = cfg.get_shares(price, prev_candle_volume)
        if shares <= 0:
            logger.error(f"[{ticker}] Cannot calculate shares for price ${price:.2f}")
            # Unsubscribe since we're no longer tracking this ticker
            if self.on_unsubscribe:
                self.on_unsubscribe(ticker)
            return

        # Log entry with sizing details
        position_cost = shares * price
        if cfg.stake_mode == "volume_pct" and prev_candle_volume:
            sizing_info = f"{cfg.volume_pct}% of {prev_candle_volume:,} vol = {shares} shares (${position_cost:.0f})"
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

        # Execute buy order (limit order at current price + slippage)
        try:
            order = self.trader.buy(ticker, shares, limit_price=price)
            logger.info(f"[{ticker}] ✅ Buy order submitted: {order.order_id} ({order.status})")
        except Exception as e:
            logger.error(f"[{ticker}] Buy order failed: {e}")
            # Unsubscribe since we're no longer tracking this ticker
            if self.on_unsubscribe:
                self.on_unsubscribe(ticker)
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

        # Persist to database
        self._active_trade_store.save_trade(
            ticker=ticker,
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            entry_price=price,
            entry_time=timestamp,
            first_candle_open=pending.first_price or price,
            shares=shares,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            highest_since_entry=price,
            paper=self.paper,
            announcement_ticker=pending.announcement.ticker if pending.announcement else None,
            announcement_timestamp=pending.announcement.timestamp if pending.announcement else None,
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
        trade = self.active_trades.get(ticker)
        if not trade:
            logger.warning(f"[{ticker}] No active trade found for exit")
            return

        return_pct = ((price - trade.entry_price) / trade.entry_price) * 100

        logger.info(f"[{ticker}] EXIT @ ${price:.2f} ({reason}) - Return: {return_pct:+.2f}%")

        # Execute sell order (limit order at current price - slippage)
        try:
            order = self.trader.sell(ticker, trade.shares, limit_price=price)
            logger.info(f"[{ticker}] Sell order submitted: {order.order_id} ({order.status})")
        except Exception as e:
            logger.error(f"[{ticker}] Sell order failed: {e}")
            logger.warning(f"[{ticker}] Keeping position in active_trades - will retry on next exit signal")
            return  # Don't remove from tracking, will retry later

        # Sell succeeded - remove from active trades
        self.active_trades.pop(ticker, None)

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
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
        }
        self.completed_trades.append(completed)

        # Persist to trade history database
        try:
            self._trade_history.save_trade(
                completed,
                paper=self.paper,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_name,
            )
        except Exception as e:
            logger.error(f"[{ticker}] Failed to save trade to database: {e}")

        # Remove from active trades database
        self._active_trade_store.delete_trade(ticker, self.strategy_id)

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
            stale_tickers = []
            for ticker in self.active_trades:
                if ticker not in broker_positions:
                    logger.warning(f"[{ticker}] Position no longer exists at broker - removing from tracking")
                    stale_tickers.append(ticker)

            # Remove stale trades and unsubscribe
            for ticker in stale_tickers:
                del self.active_trades[ticker]
                # Also remove from database
                self._active_trade_store.delete_trade(ticker, self.strategy_id)
                if self.on_unsubscribe:
                    self.on_unsubscribe(ticker)

            if stale_tickers:
                logger.info(f"Reconciliation removed {len(stale_tickers)} stale positions: {stale_tickers}")

        except Exception as e:
            logger.error(f"Position reconciliation failed: {e}", exc_info=True)

    def get_status(self) -> dict:
        """Get current engine status."""
        active_trades = {}
        for ticker, t in self.active_trades.items():
            current_price = t.last_price if t.last_price > 0 else t.entry_price
            pnl_pct = ((current_price - t.entry_price) / t.entry_price) * 100 if t.entry_price > 0 else 0
            pnl_dollars = (current_price - t.entry_price) * t.shares

            # Calculate timeout time
            timeout_at = t.entry_time + timedelta(minutes=self.config.timeout_minutes)

            active_trades[ticker] = {
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
            }

        return {
            "pending_entries": list(self.pending_entries.keys()),
            "active_trades": active_trades,
            "completed_trades": len(self.completed_trades),
        }
