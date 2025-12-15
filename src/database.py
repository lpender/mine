"""PostgreSQL database models and connection."""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, DateTime, Text, Index, UniqueConstraint, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/backtest")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class AnnouncementDB(Base):
    """Announcement record in PostgreSQL."""
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core fields
    ticker = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    price_threshold = Column(Float, nullable=False)
    headline = Column(Text, default="")
    country = Column(String(10))
    channel = Column(String(100))
    author = Column(String(100))  # Discord display name (e.g. "PR - Spike", "Nuntiobot")

    # Fundamental data
    float_shares = Column(Float)
    io_percent = Column(Float)
    market_cap = Column(Float)
    short_interest = Column(Float)

    # Flags
    reg_sho = Column(Boolean, default=False)
    high_ctb = Column(Boolean, default=False)

    # Direction arrow (↑ = "up", ↗ = "up_right")
    direction = Column(String(20))

    # Headline classification
    headline_is_financing = Column(Boolean)
    headline_financing_type = Column(String(50))
    headline_financing_tags = Column(String(200))

    # Premarket context
    prev_close = Column(Float)
    regular_open = Column(Float)
    premarket_gap_pct = Column(Float)
    premarket_volume = Column(Integer)
    premarket_dollar_volume = Column(Float)

    # Scanner-specific fields
    scanner_gain_pct = Column(Float)
    is_nhod = Column(Boolean, default=False)
    is_nsh = Column(Boolean, default=False)
    rvol = Column(Float)
    mention_count = Column(Integer)
    has_news = Column(Boolean, default=True)
    green_bars = Column(Integer)
    bar_minutes = Column(Integer)
    scanner_test = Column(Boolean, default=False)
    scanner_after_lull = Column(Boolean, default=False)

    # Source data
    source_message = Column(Text)  # Raw Discord message that generated this announcement

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uq_ticker_timestamp'),
        Index('ix_announcements_ticker_timestamp', 'ticker', 'timestamp'),
    )


class OHLCVBarDB(Base):
    """OHLCV bar record in PostgreSQL."""
    __tablename__ = "ohlcv_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)

    ticker = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)
    vwap = Column(Float)

    # Link to announcement for context
    announcement_ticker = Column(String(10))
    announcement_timestamp = Column(DateTime)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uq_ohlcv_ticker_timestamp'),
        Index('ix_ohlcv_ticker_timestamp', 'ticker', 'timestamp'),
        Index('ix_ohlcv_announcement', 'announcement_ticker', 'announcement_timestamp'),
    )


class TradeHistoryDB(Base):
    """Completed trade record for live/paper trading."""
    __tablename__ = "trade_history"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core trade data
    ticker = Column(String(10), nullable=False, index=True)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False, index=True)
    exit_price = Column(Float, nullable=False)
    exit_time = Column(DateTime, nullable=False)
    exit_reason = Column(String(50))  # take_profit, stop_loss, trailing_stop, timeout
    shares = Column(Integer, nullable=False)
    return_pct = Column(Float)
    pnl = Column(Float)

    # Trading mode
    paper = Column(Boolean, default=True)  # True=paper, False=live

    # Strategy reference
    strategy_id = Column(String(36), ForeignKey('strategies.id'), nullable=True, index=True)
    strategy_name = Column(String(100), nullable=True)  # Denormalized for quick display

    # Strategy params (JSON) - kept for historical record
    strategy_params = Column(Text)  # JSON serialized StrategyConfig

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('ix_trade_history_entry_time', 'entry_time'),
        Index('ix_trade_history_paper', 'paper'),
    )


class RawMessageDB(Base):
    """Raw Discord message for re-parsing."""
    __tablename__ = "raw_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)

    discord_message_id = Column(String(50), unique=True)
    channel = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    message_timestamp = Column(DateTime, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('ix_raw_messages_channel_timestamp', 'channel', 'message_timestamp'),
    )


class StrategyDB(Base):
    """Named trading strategy configuration."""
    __tablename__ = "strategies"

    id = Column(String(36), primary_key=True)  # UUID
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    config = Column(Text, nullable=False)  # JSON serialized StrategyConfig
    enabled = Column(Boolean, default=False)  # Live trading on/off
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('ix_strategies_enabled', 'enabled'),
    )


class LiveBarDB(Base):
    """1-second bars captured during live trading for visualization."""
    __tablename__ = "live_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)

    ticker = Column(String(10), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False)

    # Optional strategy reference for filtering
    strategy_id = Column(String(36), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint('ticker', 'timestamp', name='uq_live_bar_ticker_timestamp'),
        Index('ix_live_bars_ticker_timestamp', 'ticker', 'timestamp'),
    )


class ActiveTradeDB(Base):
    """Active trade position - persisted for recovery across restarts."""
    __tablename__ = "active_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Position identification
    ticker = Column(String(10), nullable=False, index=True)
    strategy_id = Column(String(36), ForeignKey('strategies.id'), nullable=True, index=True)
    strategy_name = Column(String(100), nullable=True)

    # Entry details
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    first_candle_open = Column(Float, nullable=False)
    shares = Column(Integer, nullable=False)

    # Exit levels
    stop_loss_price = Column(Float, nullable=False)
    take_profit_price = Column(Float, nullable=False)

    # Tracking
    highest_since_entry = Column(Float, nullable=False)
    last_price = Column(Float, default=0.0)
    last_quote_time = Column(DateTime, nullable=True)

    # Trading mode
    paper = Column(Boolean, default=True)

    # Announcement reference (optional - may be lost on restart)
    announcement_ticker = Column(String(10), nullable=True)
    announcement_timestamp = Column(DateTime, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Only one active trade per ticker per strategy
        UniqueConstraint('ticker', 'strategy_id', name='uq_active_trade_ticker_strategy'),
        Index('ix_active_trades_strategy', 'strategy_id'),
    )


def init_db():
    """Create all tables."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
