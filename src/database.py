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
    source_message = Column(Text)  # Clean text of Discord message
    source_html = Column(Text)  # Raw HTML of Discord message (for re-parsing)

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


class TradeDB(Base):
    """Completed trade record for live/paper trading."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core trade data
    ticker = Column(String(10), nullable=False, index=True)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)  # Indexed via __table_args__
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
        Index('ix_trades_entry_time', 'entry_time'),
        Index('ix_trades_paper', 'paper'),
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
    priority = Column(Integer, default=0)  # Lower = higher priority (processed first)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('ix_strategies_enabled', 'enabled'),
        Index('ix_strategies_priority', 'priority'),
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
    trade_id = Column(String(36), nullable=False, unique=True, index=True)  # UUID
    ticker = Column(String(10), nullable=False, index=True)
    strategy_id = Column(String(36), ForeignKey('strategies.id'), nullable=True)  # Indexed via __table_args__
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
        # Allow multiple trades per ticker per strategy (keyed by trade_id)
        Index('ix_active_trades_strategy', 'strategy_id'),
        Index('ix_active_trades_ticker_strategy', 'ticker', 'strategy_id'),
    )


class PendingEntryDB(Base):
    """Pending entry - persisted for recovery across restarts."""
    __tablename__ = "pending_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Entry identification
    trade_id = Column(String(36), nullable=False, unique=True, index=True)  # UUID
    ticker = Column(String(10), nullable=False)  # Indexed via __table_args__
    strategy_id = Column(String(36), ForeignKey('strategies.id'), nullable=True)  # Indexed via __table_args__
    strategy_name = Column(String(100), nullable=True)

    # Alert details
    alert_time = Column(DateTime, nullable=False)
    first_price = Column(Float, nullable=True)

    # Announcement reference (for recovery)
    announcement_ticker = Column(String(10), nullable=False)
    announcement_timestamp = Column(DateTime, nullable=False)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('ix_pending_entries_strategy', 'strategy_id'),
        Index('ix_pending_entries_ticker', 'ticker'),
    )


class OrderDB(Base):
    """Order record - tracks every order submitted to the broker."""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Broker reference
    broker_order_id = Column(String(50), unique=True, nullable=True, index=True)

    # Order details
    ticker = Column(String(10), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # 'buy' or 'sell'
    order_type = Column(String(20), nullable=False)  # 'market', 'limit'
    limit_price = Column(Float, nullable=True)
    requested_shares = Column(Integer, nullable=False)

    # Fill tracking
    filled_shares = Column(Integer, default=0)
    avg_fill_price = Column(Float, nullable=True)

    # Status: 'pending', 'partial', 'filled', 'cancelled', 'rejected', 'expired'
    status = Column(String(20), nullable=False, default='pending', index=True)

    # Strategy association
    strategy_id = Column(String(36), ForeignKey('strategies.id'), nullable=True, index=True)
    strategy_name = Column(String(100), nullable=True)

    # Link sell orders to the position they're closing
    active_trade_id = Column(Integer, ForeignKey('active_trades.id'), nullable=True)

    # Trading mode
    paper = Column(Boolean, default=True, index=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('ix_orders_ticker_status', 'ticker', 'status'),
        Index('ix_orders_created', 'created_at'),
    )


class OrderEventDB(Base):
    """Order event - every event from the broker (fill, partial_fill, cancelled, etc)."""
    __tablename__ = "order_events"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # References
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=True)  # Indexed via __table_args__
    broker_order_id = Column(String(50), nullable=True, index=True)

    # Event details
    event_type = Column(String(20), nullable=False)  # 'new', 'partial_fill', 'fill', 'cancelled', 'rejected', 'expired'
    filled_shares = Column(Integer, nullable=True)  # shares filled in this event
    fill_price = Column(Float, nullable=True)  # price for this fill
    cumulative_filled = Column(Integer, nullable=True)  # total filled so far

    # Raw data for debugging
    raw_data = Column(Text, nullable=True)

    # Timestamps
    event_timestamp = Column(DateTime, nullable=False)  # when the event occurred at broker
    created_at = Column(DateTime, default=datetime.utcnow)  # when we recorded it

    __table_args__ = (
        Index('ix_order_events_order', 'order_id'),
        Index('ix_order_events_timestamp', 'event_timestamp'),
    )


def init_db():
    """Create all tables and run migrations."""
    Base.metadata.create_all(bind=engine)

    from sqlalchemy import inspect, text
    inspector = inspect(engine)

    # Migration: Add priority column to strategies if missing
    if 'strategies' in inspector.get_table_names():
        columns = [c['name'] for c in inspector.get_columns('strategies')]
        if 'priority' not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE strategies ADD COLUMN priority INTEGER DEFAULT 0"))
                # Set initial priorities based on creation order
                conn.execute(text("""
                    UPDATE strategies SET priority = (
                        SELECT COUNT(*) FROM strategies s2
                        WHERE s2.created_at < strategies.created_at
                    )
                """))
                conn.commit()

    # Migration: Add source_html column to announcements if missing
    if 'announcements' in inspector.get_table_names():
        columns = [c['name'] for c in inspector.get_columns('announcements')]
        if 'source_html' not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE announcements ADD COLUMN source_html TEXT"))
                conn.commit()

    # Migration: Add trade_id column to active_trades if missing
    if 'active_trades' in inspector.get_table_names():
        columns = [c['name'] for c in inspector.get_columns('active_trades')]
        if 'trade_id' not in columns:
            import uuid
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE active_trades ADD COLUMN trade_id VARCHAR(36)"))
                # Generate UUIDs for existing rows
                conn.execute(text("""
                    UPDATE active_trades SET trade_id = gen_random_uuid()::text
                    WHERE trade_id IS NULL
                """))
                conn.commit()

    # Migration: Drop old unique constraint on (ticker, strategy_id) if it exists
    # A strategy can have multiple active trades on the same ticker
    if 'active_trades' in inspector.get_table_names():
        constraints = inspector.get_unique_constraints('active_trades')
        constraint_names = [c['name'] for c in constraints]
        if 'uq_active_trade_ticker_strategy' in constraint_names:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE active_trades DROP CONSTRAINT uq_active_trade_ticker_strategy"))
                conn.commit()


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
