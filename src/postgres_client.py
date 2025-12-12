"""PostgreSQL-based data client for announcements and OHLCV data."""

import os
import time
from datetime import datetime, timedelta
from typing import List, Optional
from dotenv import load_dotenv

from sqlalchemy.orm import Session
from sqlalchemy import and_

from .database import SessionLocal, AnnouncementDB, OHLCVBarDB, RawMessageDB
from .models import Announcement, OHLCVBar, get_market_session

load_dotenv()

# Reuse Massive API client for fetching new data
import requests

MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")
MASSIVE_BASE_URL = "https://api.polygon.io"


class PostgresClient:
    """Client for storing/retrieving announcements and OHLCV data in PostgreSQL."""

    def __init__(self):
        self.api_key = MASSIVE_API_KEY
        self._session = requests.Session()

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

    def has_ohlcv_data(self, ticker: str, start: datetime, end: datetime) -> bool:
        """Check if we have OHLCV data for a ticker in a time range."""
        db = self._get_db()
        try:
            count = db.query(OHLCVBarDB).filter(
                and_(
                    OHLCVBarDB.ticker == ticker,
                    OHLCVBarDB.timestamp >= start,
                    OHLCVBarDB.timestamp <= end
                )
            ).count()
            return count > 0
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────────────────────────
    # Fetch from Polygon API
    # ─────────────────────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, ticker: str, start: datetime, end: datetime,
                    use_cache: bool = True) -> List[OHLCVBar]:
        """Fetch OHLCV data from Polygon API, caching in PostgreSQL."""

        # Check cache first
        if use_cache and self.has_ohlcv_data(ticker, start, end):
            return self.get_ohlcv_bars(ticker, start, end)

        # Fetch from API with retry logic for rate limits
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        url = f"{MASSIVE_BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{start_ms}/{end_ms}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": self.api_key,
        }

        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = self._session.get(url, params=params, timeout=30)

                # Handle rate limiting with exponential backoff
                if response.status_code == 429:
                    wait_time = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                    print(f"  Rate limited on {ticker}, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK" or not data.get("results"):
                    return []

                bars = []
                for r in data["results"]:
                    bar = OHLCVBar(
                        timestamp=datetime.fromtimestamp(r["t"] / 1000),
                        open=r["o"],
                        high=r["h"],
                        low=r["l"],
                        close=r["c"],
                        volume=r["v"],
                        vwap=r.get("vw"),
                    )
                    bars.append(bar)

                # Cache in database
                if bars:
                    self.save_ohlcv_bars(ticker, bars)

                # Small delay to avoid hitting rate limits
                time.sleep(0.15)

                return bars

            except requests.exceptions.HTTPError as e:
                if e.response and e.response.status_code == 429:
                    wait_time = 2 ** attempt
                    print(f"  Rate limited on {ticker}, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                print(f"Error fetching OHLCV for {ticker}: {e}")
                return []
            except Exception as e:
                print(f"Error fetching OHLCV for {ticker}: {e}")
                return []

        print(f"Failed to fetch OHLCV for {ticker} after {max_retries} retries")
        return []

    def fetch_after_announcement(self, ticker: str, announcement_time: datetime,
                                  window_minutes: int = 120,
                                  use_cache: bool = True) -> List[OHLCVBar]:
        """Fetch OHLCV data starting from announcement time."""

        # Determine effective start time based on market session
        session = get_market_session(announcement_time)

        if session == "closed":
            # Start at next market open (9:30 AM next trading day)
            effective_start = announcement_time.replace(hour=9, minute=30, second=0, microsecond=0)
            if announcement_time.hour >= 20:
                effective_start += timedelta(days=1)
            # Skip weekends
            while effective_start.weekday() >= 5:
                effective_start += timedelta(days=1)
        elif session == "postmarket":
            # Start at next market open
            effective_start = announcement_time.replace(hour=9, minute=30, second=0, microsecond=0)
            effective_start += timedelta(days=1)
            while effective_start.weekday() >= 5:
                effective_start += timedelta(days=1)
        else:
            effective_start = announcement_time

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
        )
