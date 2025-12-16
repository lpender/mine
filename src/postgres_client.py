"""PostgreSQL-based data client for announcements and OHLCV data."""

import os
from datetime import datetime, timedelta
from typing import List, Optional
from dotenv import load_dotenv

from sqlalchemy.orm import Session
from sqlalchemy import and_

from .database import SessionLocal, AnnouncementDB, OHLCVBarDB, RawMessageDB
from .models import Announcement, OHLCVBar, get_market_session
from .data_providers import get_provider, OHLCVDataProvider

load_dotenv()


class PostgresClient:
    """Client for storing/retrieving announcements and OHLCV data in PostgreSQL.

    Uses the configured DATA_BACKEND (polygon, alpaca, ib) for fetching OHLCV data.
    """

    def __init__(self, backend: Optional[str] = None, provider: Optional[OHLCVDataProvider] = None):
        self._provider = provider or get_provider(backend)
        print(f"[PostgresClient] Using {self._provider.name} backend")

    def _get_db(self) -> Session:
        return SessionLocal()

    # ─────────────────────────────────────────────────────────────────────────────
    # Announcements
    # ─────────────────────────────────────────────────────────────────────────────

    def save_announcement(self, ann: Announcement) -> None:
        """Save a single announcement to the database."""
        db = self._get_db()
        try:
            existing = db.query(AnnouncementDB).filter(
                and_(
                    AnnouncementDB.ticker == ann.ticker,
                    AnnouncementDB.timestamp == ann.timestamp
                )
            ).first()

            if existing:
                # Update existing
                for key, value in self._announcement_to_dict(ann).items():
                    if key not in ('id', 'created_at'):
                        setattr(existing, key, value)
            else:
                # Insert new
                db_ann = AnnouncementDB(**self._announcement_to_dict(ann))
                db.add(db_ann)

            db.commit()
        finally:
            db.close()

    def save_announcements(self, announcements: List[Announcement]) -> int:
        """Save multiple announcements with upsert. Returns count of new records."""
        db = self._get_db()
        new_count = 0
        try:
            for ann in announcements:
                existing = db.query(AnnouncementDB).filter(
                    and_(
                        AnnouncementDB.ticker == ann.ticker,
                        AnnouncementDB.timestamp == ann.timestamp
                    )
                ).first()

                if existing:
                    # Update existing record's fields
                    for key, value in self._announcement_to_dict(ann).items():
                        if key not in ('id', 'created_at'):
                            setattr(existing, key, value)
                else:
                    db_ann = AnnouncementDB(**self._announcement_to_dict(ann))
                    db.add(db_ann)
                    new_count += 1

            db.commit()
            return new_count
        finally:
            db.close()

    def load_announcements(self) -> List[Announcement]:
        """Load all announcements from the database."""
        db = self._get_db()
        try:
            rows = db.query(AnnouncementDB).order_by(AnnouncementDB.timestamp.desc()).all()
            return [self._db_to_announcement(row) for row in rows]
        finally:
            db.close()

    def get_announcement(self, ticker: str, timestamp: datetime) -> Optional[Announcement]:
        """Get a specific announcement."""
        db = self._get_db()
        try:
            row = db.query(AnnouncementDB).filter(
                and_(
                    AnnouncementDB.ticker == ticker,
                    AnnouncementDB.timestamp == timestamp
                )
            ).first()
            return self._db_to_announcement(row) if row else None
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────────────────────────
    # OHLCV Data
    # ─────────────────────────────────────────────────────────────────────────────

    def save_ohlcv_bars(self, ticker: str, bars: List[OHLCVBar],
                        announcement_ticker: str = None,
                        announcement_timestamp: datetime = None) -> int:
        """Save OHLCV bars to the database. Returns count of new records."""
        db = self._get_db()
        new_count = 0
        try:
            for bar in bars:
                existing = db.query(OHLCVBarDB).filter(
                    and_(
                        OHLCVBarDB.ticker == ticker,
                        OHLCVBarDB.timestamp == bar.timestamp
                    )
                ).first()

                if not existing:
                    db_bar = OHLCVBarDB(
                        ticker=ticker,
                        timestamp=bar.timestamp,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        vwap=bar.vwap,
                        announcement_ticker=announcement_ticker,
                        announcement_timestamp=announcement_timestamp,
                    )
                    db.add(db_bar)
                    new_count += 1

            db.commit()
            return new_count
        finally:
            db.close()

    def get_ohlcv_bars(self, ticker: str, start: datetime, end: datetime) -> List[OHLCVBar]:
        """Get OHLCV bars for a ticker in a time range."""
        db = self._get_db()
        try:
            rows = db.query(OHLCVBarDB).filter(
                and_(
                    OHLCVBarDB.ticker == ticker,
                    OHLCVBarDB.timestamp >= start,
                    OHLCVBarDB.timestamp <= end
                )
            ).order_by(OHLCVBarDB.timestamp).all()

            return [
                OHLCVBar(
                    timestamp=row.timestamp,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    vwap=row.vwap,
                )
                for row in rows
            ]
        finally:
            db.close()

    def has_ohlcv_data(self, ticker: str, start: datetime, end: datetime, max_gap_minutes: int = 5) -> bool:
        """Check if we have complete OHLCV data for a ticker in a time range.

        Returns True only if we have a bar within max_gap_minutes of the start time.
        This prevents false positives when we have partial data with gaps.
        """
        db = self._get_db()
        try:
            # Find the first bar in the range
            first_bar = db.query(OHLCVBarDB).filter(
                and_(
                    OHLCVBarDB.ticker == ticker,
                    OHLCVBarDB.timestamp >= start,
                    OHLCVBarDB.timestamp <= end
                )
            ).order_by(OHLCVBarDB.timestamp).first()

            if not first_bar:
                return False

            # Check if first bar is within max_gap_minutes of start
            gap_seconds = (first_bar.timestamp - start).total_seconds()
            return gap_seconds <= max_gap_minutes * 60
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────────────────────────
    # Fetch OHLCV via Data Provider
    # ─────────────────────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, ticker: str, start: datetime, end: datetime,
                    use_cache: bool = True) -> Optional[List[OHLCVBar]]:
        """Fetch OHLCV data using the configured data provider, caching in PostgreSQL."""

        # Check cache first
        if use_cache and self.has_ohlcv_data(ticker, start, end):
            return self.get_ohlcv_bars(ticker, start, end)

        # Delegate to the configured provider
        bars = self._provider.fetch_ohlcv(ticker, start, end)

        # Cache in database
        if bars:
            self.save_ohlcv_bars(ticker, bars)

        return bars

    def fetch_after_announcement(self, ticker: str, announcement_time: datetime,
                                  window_minutes: int = 120,
                                  use_cache: bool = True) -> Optional[List[OHLCVBar]]:
        """Fetch OHLCV data starting from announcement time.

        Args:
            ticker: Stock ticker
            announcement_time: Naive datetime in UTC (from database)
            window_minutes: How many minutes of data to fetch
            use_cache: Whether to use cached data

        Returns:
            List of OHLCV bars (timestamps in ET) or empty list
        """
        from datetime import date
        from .massive_client import MassiveClient

        # Use MassiveClient's timezone-aware effective start calculation
        # This properly converts UTC announcement time to ET for OHLCV queries
        massive_client = MassiveClient()
        effective_start = massive_client.get_effective_start_time(announcement_time)

        # Skip fetching if effective trading window is today (data not yet available)
        if effective_start.date() >= date.today():
            print(f"Skipping {ticker}: trading window is today/future ({effective_start.date()})")
            return []

        end_time = effective_start + timedelta(minutes=window_minutes)

        return self.fetch_ohlcv(ticker, effective_start, end_time, use_cache=use_cache)

    # ─────────────────────────────────────────────────────────────────────────────
    # Raw Messages
    # ─────────────────────────────────────────────────────────────────────────────

    def save_raw_message(self, discord_id: str, channel: str, content: str,
                         message_timestamp: datetime) -> bool:
        """Save a raw Discord message. Returns True if new."""
        db = self._get_db()
        try:
            existing = db.query(RawMessageDB).filter(
                RawMessageDB.discord_message_id == discord_id
            ).first()

            if existing:
                return False

            msg = RawMessageDB(
                discord_message_id=discord_id,
                channel=channel,
                content=content,
                message_timestamp=message_timestamp,
            )
            db.add(msg)
            db.commit()
            return True
        finally:
            db.close()

    def get_raw_messages(self, channel: str = None,
                         start: datetime = None,
                         end: datetime = None) -> List[dict]:
        """Get raw messages for re-parsing."""
        db = self._get_db()
        try:
            query = db.query(RawMessageDB)

            if channel:
                query = query.filter(RawMessageDB.channel == channel)
            if start:
                query = query.filter(RawMessageDB.message_timestamp >= start)
            if end:
                query = query.filter(RawMessageDB.message_timestamp <= end)

            rows = query.order_by(RawMessageDB.message_timestamp).all()

            return [
                {
                    "id": row.discord_message_id,
                    "channel": row.channel,
                    "content": row.content,
                    "timestamp": row.message_timestamp.isoformat(),
                }
                for row in rows
            ]
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────────

    def _announcement_to_dict(self, ann: Announcement) -> dict:
        """Convert Announcement dataclass to dict for database."""
        return {
            "ticker": ann.ticker,
            "timestamp": ann.timestamp,
            "price_threshold": ann.price_threshold,
            "headline": ann.headline,
            "country": ann.country,
            "channel": ann.channel,
            "author": ann.author,
            "float_shares": ann.float_shares,
            "io_percent": ann.io_percent,
            "market_cap": ann.market_cap,
            "short_interest": ann.short_interest,
            "reg_sho": ann.reg_sho,
            "high_ctb": ann.high_ctb,
            "direction": ann.direction,
            "headline_is_financing": ann.headline_is_financing,
            "headline_financing_type": ann.headline_financing_type,
            "headline_financing_tags": ann.headline_financing_tags,
            "prev_close": ann.prev_close,
            "regular_open": ann.regular_open,
            "premarket_gap_pct": ann.premarket_gap_pct,
            "premarket_volume": ann.premarket_volume,
            "premarket_dollar_volume": ann.premarket_dollar_volume,
            "scanner_gain_pct": ann.scanner_gain_pct,
            "is_nhod": ann.is_nhod,
            "is_nsh": ann.is_nsh,
            "rvol": ann.rvol,
            "mention_count": ann.mention_count,
            "has_news": ann.has_news,
            "green_bars": ann.green_bars,
            "bar_minutes": ann.bar_minutes,
            "scanner_test": ann.scanner_test,
            "scanner_after_lull": ann.scanner_after_lull,
            "source_message": ann.source_message,
            "source_html": ann.source_html,
        }

    def _db_to_announcement(self, row: AnnouncementDB) -> Announcement:
        """Convert database row to Announcement dataclass."""
        return Announcement(
            ticker=row.ticker,
            timestamp=row.timestamp,
            price_threshold=row.price_threshold,
            headline=row.headline or "",
            country=row.country or "",
            channel=row.channel,
            author=row.author,
            float_shares=row.float_shares,
            io_percent=row.io_percent,
            market_cap=row.market_cap,
            short_interest=row.short_interest,
            reg_sho=row.reg_sho or False,
            high_ctb=row.high_ctb or False,
            direction=row.direction,
            headline_is_financing=row.headline_is_financing,
            headline_financing_type=row.headline_financing_type,
            headline_financing_tags=row.headline_financing_tags,
            prev_close=row.prev_close,
            regular_open=row.regular_open,
            premarket_gap_pct=row.premarket_gap_pct,
            premarket_volume=row.premarket_volume,
            premarket_dollar_volume=row.premarket_dollar_volume,
            scanner_gain_pct=row.scanner_gain_pct,
            is_nhod=row.is_nhod or False,
            is_nsh=row.is_nsh or False,
            rvol=row.rvol,
            mention_count=row.mention_count,
            has_news=row.has_news if row.has_news is not None else True,
            green_bars=row.green_bars,
            bar_minutes=row.bar_minutes,
            scanner_test=row.scanner_test or False,
            scanner_after_lull=row.scanner_after_lull or False,
            source_message=row.source_message,
            source_html=row.source_html,
        )
