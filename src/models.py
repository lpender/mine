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


def get_market_session(timestamp: datetime) -> str:
    """
    Determine the market session for a given timestamp.

    Args:
        timestamp: datetime (should be in ET or will be converted)

    Returns:
        "premarket", "market", "postmarket", or "closed"
    """
    # Convert to Eastern Time if needed
    if timestamp.tzinfo is None:
        # Assume naive datetimes are already in ET
        t = timestamp.time()
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

    @property
    def is_winner(self) -> bool:
        """Returns True if the trade was profitable."""
        return self.return_pct is not None and self.return_pct > 0

    @property
    def entered(self) -> bool:
        """Returns True if entry was triggered."""
        return self.entry_price is not None


@dataclass
class BacktestConfig:
    """Configuration for backtesting parameters."""
    entry_trigger_pct: float = 5.0  # Buy when price moves +X%
    take_profit_pct: float = 10.0  # Sell when +X% from entry
    stop_loss_pct: float = 3.0  # Sell when -X% from entry
    volume_threshold: int = 0  # Minimum volume to trigger entry
    window_minutes: int = 120  # How long to track after announcement
    entry_at_candle_close: bool = False  # If True, enter at end of first candle (more realistic)
    entry_by_message_second: bool = False  # If True, enter within first candle based on announcement second (more realistic)


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
