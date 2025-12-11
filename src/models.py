from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


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
