from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional, List
from zoneinfo import ZoneInfo


# Market session time boundaries (Eastern Time)
PREMARKET_START = time(4, 0)    # 04:00 ET
MARKET_OPEN = time(9, 30)       # 09:30 ET
MARKET_CLOSE = time(16, 0)      # 16:00 ET
POSTMARKET_END = time(20, 0)    # 20:00 ET (some use 21:00)

ET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def get_market_session(timestamp: datetime) -> str:
    """
    Determine the market session for a given timestamp.

    Args:
        timestamp: datetime (naive assumed UTC, or timezone-aware)

    Returns:
        "premarket", "market", "postmarket", or "closed"
    """
    # Convert to Eastern Time
    if timestamp.tzinfo is None:
        # Naive datetimes from database are stored in UTC
        utc_time = timestamp.replace(tzinfo=UTC_TZ)
        et_time = utc_time.astimezone(ET_TZ)
    else:
        et_time = timestamp.astimezone(ET_TZ)
    t = et_time.time()

    if PREMARKET_START <= t < MARKET_OPEN:
        return "premarket"
    elif MARKET_OPEN <= t < MARKET_CLOSE:
        return "market"
    elif MARKET_CLOSE <= t < POSTMARKET_END:
        return "postmarket"
    else:
        return "closed"


@dataclass
class Announcement:
    """Represents a press release announcement from Discord."""
    ticker: str
    timestamp: datetime
    price_threshold: float  # e.g., 0.50 from "< $.50c"
    headline: str
    country: str  # e.g., "US", "CN", "IL"
    float_shares: Optional[float] = None  # e.g., 139_000_000
    io_percent: Optional[float] = None  # e.g., 6.04
    market_cap: Optional[float] = None  # e.g., 26_800_000
    reg_sho: bool = False  # Regulation SHO flag
    high_ctb: bool = False  # High Cost to Borrow flag
    short_interest: Optional[float] = None  # e.g., 23.9 (percent)
    channel: Optional[str] = None  # Discord channel name
    author: Optional[str] = None  # Discord display name (e.g. "PR - Spike", "Nuntiobot")
    direction: Optional[str] = None  # Arrow direction: "up" (↑) or "up_right" (↗)

    # Headline keyword flags (cheap PR-type classifier)
    headline_is_financing: Optional[bool] = None
    headline_financing_type: Optional[str] = None  # e.g. offering/atm/warrants/convertible/shelf/reverse_split/compliance
    headline_financing_tags: Optional[str] = None  # comma-separated tags for display/debug

    # Premarket context features (computed from OHLCV, cached)
    prev_close: Optional[float] = None  # prior regular-session close
    regular_open: Optional[float] = None  # regular-session open (9:30 bar open)
    premarket_gap_pct: Optional[float] = None  # (regular_open - prev_close) / prev_close * 100
    premarket_volume: Optional[int] = None  # sum volume 04:00-09:30
    premarket_dollar_volume: Optional[float] = None  # sum(volume * vwap/close) 04:00-09:30

    # Scanner-specific fields (from select-news channel)
    scanner_gain_pct: Optional[float] = None  # e.g., 42% = stock already moved this much
    is_nhod: bool = False  # New High of Day
    is_nsh: bool = False  # New Session High
    rvol: Optional[float] = None  # Relative volume ratio
    mention_count: Optional[int] = None  # Number of times mentioned by scanner (• 3)
    has_news: bool = True  # False if scanner-only detection with no PR/AR/SEC
    green_bars: Optional[int] = None  # Number of green bars (e.g., 3 from "3 green bars 2m")
    bar_minutes: Optional[int] = None  # Candle timeframe (e.g., 2 from "3 green bars 2m")
    scanner_test: bool = False  # Detected by "test" scanner
    scanner_after_lull: bool = False  # Detected by "after-lull" scanner

    # Source data
    source_message: Optional[str] = None  # Clean text of Discord message
    source_html: Optional[str] = None  # Raw HTML of Discord message (for re-parsing)

    # OHLCV fetch status: 'pending' | 'fetched' | 'no_data' | 'error'
    ohlcv_status: Optional[str] = 'pending'

    @property
    def market_session(self) -> str:
        """Returns the market session: premarket, market, postmarket, or closed."""
        return get_market_session(self.timestamp)


@dataclass
class OHLCVBar:
    """Represents a single OHLCV bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None  # Volume-weighted average price


@dataclass
class TradeResult:
    """Represents the result of a simulated trade."""
    announcement: Announcement
    entry_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    return_pct: Optional[float] = None
    trigger_type: str = "no_entry"  # "take_profit", "stop_loss", "timeout", "no_entry"
    pre_entry_volume: Optional[int] = None  # Volume of the candle before entry (for position sizing)

    @property
    def is_winner(self) -> bool:
        """Returns True if the trade was profitable."""
        return self.return_pct is not None and self.return_pct > 0

    @property
    def entered(self) -> bool:
        """Returns True if entry was triggered."""
        return self.entry_price is not None

    @property
    def pnl_at_1pct_volume(self) -> Optional[float]:
        """Calculate P&L assuming position size is 1% of pre-entry candle volume."""
        if not self.entered or self.pre_entry_volume is None or self.pre_entry_volume <= 0:
            return None
        if self.return_pct is None or self.entry_price is None:
            return None
        shares = int(self.pre_entry_volume * 0.01)
        if shares <= 0:
            return None
        position_value = shares * self.entry_price
        return position_value * (self.return_pct / 100)

    def pnl_with_sizing(
        self,
        stake_mode: str = "fixed",
        stake_amount: float = 1000.0,
        volume_pct: float = 1.0,
        max_stake: float = 10000.0,
    ) -> Optional[float]:
        """
        Calculate P&L based on position sizing settings.

        Args:
            stake_mode: "fixed" for fixed dollar amount, "volume_pct" for % of pre-entry volume
            stake_amount: Dollar amount for fixed stake mode
            volume_pct: Percentage of pre-entry candle volume to buy
            max_stake: Maximum position cost (for volume_pct mode)

        Returns:
            Dollar P&L for the trade, or None if not entered
        """
        if not self.entered or self.return_pct is None or self.entry_price is None:
            return None
        if self.entry_price <= 0:
            return None

        if stake_mode == "volume_pct":
            # Volume-based sizing: buy volume_pct% of pre-entry candle volume
            if self.pre_entry_volume is None or self.pre_entry_volume <= 0:
                return None
            shares_from_volume = int(self.pre_entry_volume * volume_pct / 100)
            max_shares = int(max_stake / self.entry_price)
            shares = min(shares_from_volume, max_shares)
            if shares <= 0:
                return None
        else:
            # Fixed stake mode (default)
            shares = max(1, int(stake_amount / self.entry_price))

        position_value = shares * self.entry_price
        return position_value * (self.return_pct / 100)


@dataclass
class BacktestConfig:
    """Configuration for backtesting parameters."""
    entry_trigger_pct: float = 5.0  # Buy when price moves +X%
    take_profit_pct: float = 10.0  # Sell when +X% from entry
    stop_loss_pct: float = 3.0  # Sell when -X% from entry
    stop_loss_from_open: bool = False  # If True, calculate SL from first candle's open instead of entry price
    volume_threshold: int = 0  # Minimum volume to trigger entry
    window_minutes: int = 120  # How long to hold position before timeout exit
    entry_window_minutes: int = 0  # How long to look for entry (0 = same as window_minutes)
    entry_at_candle_close: bool = False  # If True, enter at end of first candle (more realistic)
    entry_by_message_second: bool = False  # If True, enter within first candle based on announcement second (more realistic)
    entry_at_open: bool = False  # If True, enter at first candle's open (most optimistic)
    entry_after_consecutive_candles: int = 0  # Wait for X consecutive candles with low > first candle open
    min_candle_volume: int = 0  # Minimum volume per candle for consecutive candles entry
    trailing_stop_pct: float = 0.0  # Exit if price drops X% from highest point since entry (0 = disabled)
    # Lookback filter - skip stocks that have already moved too much
    max_prior_move_pct: float = 0.0  # Skip if stock moved more than X% in lookback period (0 = disabled)
    lookback_minutes: int = 30  # How far back to look for prior move calculation


@dataclass
class BacktestSummary:
    """Summary statistics for a backtest run."""
    total_announcements: int = 0
    total_trades: int = 0  # Trades that entered
    winners: int = 0
    losers: int = 0
    no_entry: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    total_return: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    results: List[TradeResult] = field(default_factory=list)
