"""PostgreSQL-based data client for announcements and OHLCV data."""

import logging
import os
from datetime import datetime, timedelta
from functools import lru_cache
from typing import List, Optional
from dotenv import load_dotenv

from sqlalchemy.orm import Session
from sqlalchemy.orm import aliased
from sqlalchemy import and_, or_, tuple_
from sqlalchemy import String, Time, cast, case, func, literal
from sqlalchemy import select

from .database import SessionLocal, AnnouncementDB, OHLCVBarDB, RawMessageDB
from .models import Announcement, OHLCVBar, get_market_session
from .data_providers import get_provider, OHLCVDataProvider

load_dotenv()

logger = logging.getLogger(__name__)


class PostgresClient:
    """Client for storing/retrieving announcements and OHLCV data in PostgreSQL.

    Uses the configured DATA_BACKEND (polygon, alpaca, ib) for fetching OHLCV data.
    """

    def __init__(self, backend: Optional[str] = None, provider: Optional[OHLCVDataProvider] = None):
        self._provider = provider or get_provider(backend)
        # Avoid noisy stdout prints (Streamlit reruns can instantiate this often).
        # Use debug by default; enable info-level logging with POSTGRESCLIENT_LOG_BACKEND=1.
        msg = f"Using {self._provider.name} backend"
        if os.getenv("POSTGRESCLIENT_LOG_BACKEND", "0") == "1":
            logger.info(msg)
        else:
            logger.debug(msg)

    def _get_db(self) -> Session:
        return SessionLocal()

    # ─────────────────────────────────────────────────────────────────────────────
    # Announcements
    # ─────────────────────────────────────────────────────────────────────────────

    def save_announcement(self, ann: Announcement, source: str = 'backfill') -> Optional[int]:
        """Save a single announcement to the database. Returns the announcement ID."""
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
                for key, value in self._announcement_to_dict(ann, source=source).items():
                    if key not in ('id', 'created_at'):
                        setattr(existing, key, value)
                db.commit()
                return existing.id
            else:
                # Insert new
                db_ann = AnnouncementDB(**self._announcement_to_dict(ann, source=source))
                db.add(db_ann)
                db.commit()
                return db_ann.id
        finally:
            db.close()

    def save_announcements(self, announcements: List[Announcement], source: str = 'backfill') -> int:
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
                    for key, value in self._announcement_to_dict(ann, source=source).items():
                        if key not in ('id', 'created_at'):
                            setattr(existing, key, value)
                else:
                    db_ann = AnnouncementDB(**self._announcement_to_dict(ann, source=source))
                    db.add(db_ann)
                    new_count += 1

            db.commit()
            return new_count
        finally:
            db.close()

    def load_announcements(self, source: Optional[str] = 'backfill') -> List[Announcement]:
        """Load announcements from the database.

        Args:
            source: Filter by source ('backfill', 'live', or None for all).
                    Defaults to 'backfill' to exclude live alerts from backtest data.
        """
        db = self._get_db()
        try:
            query = db.query(AnnouncementDB)
            if source:
                query = query.filter(AnnouncementDB.source == source)
            rows = query.order_by(AnnouncementDB.timestamp.desc()).all()
            return [self._db_to_announcement(row) for row in rows]
        finally:
            db.close()

    def get_announcement_filter_options(self, source: str = "backfill") -> dict:
        """
        Get distinct values for dashboard filter widgets without loading all announcements.
        Returns dict with keys: countries, authors, channels, directions.
        """
        db = self._get_db()
        try:
            def distinct_nonempty(col):
                rows = (
                    db.query(col)
                    .filter(AnnouncementDB.source == source)
                    .filter(col.isnot(None))
                    .filter(func.trim(col) != "")
                    .distinct()
                    .order_by(col)
                    .all()
                )
                return [r[0] for r in rows if r and r[0]]

            return {
                "countries": distinct_nonempty(AnnouncementDB.country),
                "authors": distinct_nonempty(AnnouncementDB.author),
                "channels": distinct_nonempty(AnnouncementDB.channel),
                "directions": distinct_nonempty(AnnouncementDB.direction),
            }
        finally:
            db.close()

    def load_announcements_sampled_and_filtered(
        self,
        *,
        source: str = "backfill",
        sample_pct: int = 100,
        sample_seed: int = 0,
        sessions: Optional[List[str]] = None,  # premarket|market|postmarket|closed (computed from timestamp)
        # Column filters (all optional)
        countries: Optional[List[str]] = None,
        country_blacklist: Optional[List[str]] = None,
        authors: Optional[List[str]] = None,
        channels: Optional[List[str]] = None,
        directions: Optional[List[str]] = None,
        scanner_test: bool = False,
        scanner_after_lull: bool = False,
        max_mentions: Optional[int] = None,
        exclude_financing_headlines: bool = False,
        require_headline: bool = False,
        exclude_headline: bool = False,
        float_min_m: float = 0.0,
        float_max_m: float = 1000.0,
        mc_min_m: float = 0.0,
        mc_max_m: float = 10000.0,
        prior_move_min: float = 0.0,
        prior_move_max: float = 0.0,
        nhod_filter: str = "Any",  # Any|Yes|No
        nsh_filter: str = "Any",   # Any|Yes|No
        rvol_min: float = 0.0,
        rvol_max: float = 0.0,
        exclude_financing_types: Optional[List[str]] = None,
        exclude_biotech: bool = False,
    ) -> tuple[int, List[Announcement]]:
        """
        Load announcements using SQL for speed.

        Preserves the dashboard's semantics: sampling is applied FIRST, then filters.

        Returns:
            (total_before_sampling, announcements)
        """
        db = self._get_db()
        try:
            base = db.query(AnnouncementDB).filter(AnnouncementDB.source == source)

            # Count before sampling (matches previous behavior)
            total_before_sampling = base.count()

            # Sampling (subquery)
            pct = int(sample_pct or 100)
            pct = 1 if pct < 1 else 100 if pct > 100 else pct
            if total_before_sampling <= 0:
                return 0, []

            sample_size = max(1, int(total_before_sampling * pct / 100))
            if pct < 100:
                if sample_seed and int(sample_seed) > 0:
                    # Deterministic pseudo-random ordering based on (ticker, timestamp, seed)
                    seed_str = str(int(sample_seed))
                    order_expr = func.md5(
                        func.concat(
                            AnnouncementDB.ticker,
                            cast(AnnouncementDB.timestamp, String),
                            literal(seed_str),
                        )
                    )
                else:
                    order_expr = func.random()

                sampled_subq = base.order_by(order_expr).limit(sample_size).subquery()
                A = aliased(AnnouncementDB, sampled_subq)
                q = db.query(A)
            else:
                A = AnnouncementDB
                q = base

            # Apply filters to sampled rows
            # Session filter: compute ET time-of-day from UTC timestamp (matches src.models.get_market_session).
            if sessions:
                # timestamp column is stored as naive UTC
                et_ts = func.timezone("America/New_York", func.timezone("UTC", A.timestamp))
                et_time = cast(et_ts, Time)
                session_case = case(
                    (
                        and_(et_time >= literal("04:00:00"), et_time < literal("09:30:00")),
                        literal("premarket"),
                    ),
                    (
                        and_(et_time >= literal("09:30:00"), et_time < literal("16:00:00")),
                        literal("market"),
                    ),
                    (
                        and_(et_time >= literal("16:00:00"), et_time < literal("20:00:00")),
                        literal("postmarket"),
                    ),
                    else_=literal("closed"),
                )
                q = q.filter(session_case.in_(sessions))

            if countries:
                q = q.filter(A.country.in_(countries))
            if country_blacklist:
                q = q.filter(or_(A.country.is_(None), ~A.country.in_(country_blacklist)))
            if authors:
                q = q.filter(A.author.in_(authors))
            if channels:
                q = q.filter(A.channel.in_(channels))
            if directions:
                q = q.filter(A.direction.in_(directions))

            if scanner_test:
                q = q.filter(A.scanner_test.is_(True))
            if scanner_after_lull:
                q = q.filter(A.scanner_after_lull.is_(True))

            if max_mentions is not None and int(max_mentions) > 0:
                q = q.filter(A.mention_count.isnot(None)).filter(A.mention_count <= int(max_mentions))

            # Financing/headline filters
            if exclude_financing_headlines:
                q = q.filter(or_(A.headline_is_financing.is_(None), A.headline_is_financing.is_(False)))

            if require_headline:
                q = q.filter(A.headline.isnot(None)).filter(func.trim(A.headline) != "")
            if exclude_headline:
                q = q.filter(or_(A.headline.is_(None), func.trim(A.headline) == ""))

            # Float (shares) / Market cap (dollars)
            float_min = float(float_min_m or 0.0) * 1e6
            float_max = float(float_max_m or 0.0) * 1e6
            q = q.filter(or_(A.float_shares.is_(None), and_(A.float_shares >= float_min, A.float_shares <= float_max)))

            mc_min = float(mc_min_m or 0.0) * 1e6
            mc_max = float(mc_max_m or 0.0) * 1e6
            q = q.filter(or_(A.market_cap.is_(None), and_(A.market_cap >= mc_min, A.market_cap <= mc_max)))

            # Prior move (scanner_gain_pct)
            if prior_move_min and float(prior_move_min) > 0:
                q = q.filter(A.scanner_gain_pct.isnot(None)).filter(A.scanner_gain_pct >= float(prior_move_min))
            if prior_move_max and float(prior_move_max) > 0:
                q = q.filter(or_(A.scanner_gain_pct.is_(None), A.scanner_gain_pct <= float(prior_move_max)))

            # NHOD / NSH
            if nhod_filter == "Yes":
                q = q.filter(A.is_nhod.is_(True))
            elif nhod_filter == "No":
                q = q.filter(or_(A.is_nhod.is_(False), A.is_nhod.is_(None)))

            if nsh_filter == "Yes":
                q = q.filter(A.is_nsh.is_(True))
            elif nsh_filter == "No":
                q = q.filter(or_(A.is_nsh.is_(False), A.is_nsh.is_(None)))

            # RVol
            if rvol_min and float(rvol_min) > 0:
                q = q.filter(A.rvol.isnot(None)).filter(A.rvol >= float(rvol_min))
            if rvol_max and float(rvol_max) > 0:
                q = q.filter(or_(A.rvol.is_(None), A.rvol <= float(rvol_max)))

            # Headline financing type exclusions (list)
            if exclude_financing_types:
                q = q.filter(or_(A.headline_financing_type.is_(None), ~A.headline_financing_type.in_(exclude_financing_types)))

            # Biotech/pharma keyword exclusions
            if exclude_biotech:
                kws = [
                    "therapeutics", "clinical", "trial", "phase", "fda", "drug", "treatment",
                ]
                # Keep rows with no headline, otherwise require that none of the keywords match
                q = q.filter(
                    or_(
                        A.headline.is_(None),
                        ~or_(*[A.headline.ilike(f"%{kw}%") for kw in kws]),
                    )
                )

            # Keep a stable sort for UI
            q = q.order_by(A.timestamp.desc())

            rows = q.all()
            return total_before_sampling, [self._db_to_announcement(r) for r in rows]
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

    def get_ohlcv_bars_bulk(self, announcement_keys: List[tuple]) -> dict:
        """Get OHLCV bars for multiple announcements in a single query.

        Uses the announcement_ticker/announcement_timestamp columns to fetch
        all bars efficiently (composite IN query, chunked), which is much faster
        than N separate queries or a giant OR(...) chain.

        Args:
            announcement_keys: List of (ticker, timestamp) tuples

        Returns:
            Dict mapping (ticker, timestamp) to list of OHLCVBar
        """
        if not announcement_keys:
            return {}

        # Note: large key lists can be slow/large in a single SQL statement.
        # Chunk into batches for more predictable performance and fewer bind params.
        chunk_size = int(os.getenv("POSTGRESCLIENT_BULK_CHUNK_SIZE", "2000") or 2000)
        if chunk_size <= 0:
            chunk_size = 2000

        log_timing = os.getenv("POSTGRESCLIENT_LOG_TIMING", "0") == "1"
        t0 = datetime.now() if log_timing else None

        # Pre-create result map with all keys (even those with no bars)
        result = {key: [] for key in announcement_keys}

        db = self._get_db()
        try:
            total_rows = 0
            key_col = tuple_(OHLCVBarDB.announcement_ticker, OHLCVBarDB.announcement_timestamp)

            for start_idx in range(0, len(announcement_keys), chunk_size):
                batch = announcement_keys[start_idx:start_idx + chunk_size]

                # Use a Core select returning raw tuples to avoid ORM object materialization overhead.
                stmt = (
                    select(
                        OHLCVBarDB.announcement_ticker,
                        OHLCVBarDB.announcement_timestamp,
                        OHLCVBarDB.timestamp,
                        OHLCVBarDB.open,
                        OHLCVBarDB.high,
                        OHLCVBarDB.low,
                        OHLCVBarDB.close,
                        OHLCVBarDB.volume,
                        OHLCVBarDB.vwap,
                    )
                    .where(key_col.in_(batch))
                    .order_by(
                        OHLCVBarDB.announcement_ticker,
                        OHLCVBarDB.announcement_timestamp,
                        OHLCVBarDB.timestamp,
                    )
                )
                rows = db.execute(stmt).all()

                total_rows += len(rows)
                for ann_ticker, ann_ts, ts, o, h, l, c, vol, vwap in rows:
                    key = (ann_ticker, ann_ts)
                    bucket = result.get(key)
                    if bucket is not None:
                        bucket.append(
                            OHLCVBar(
                                timestamp=ts,
                                open=o,
                                high=h,
                                low=l,
                                close=c,
                                volume=vol,
                                vwap=vwap,
                            )
                        )

            if log_timing:
                dt = (datetime.now() - t0).total_seconds() if t0 else 0.0
                logger.info(
                    "get_ohlcv_bars_bulk: keys=%d rows=%d chunk=%d took=%.2fs",
                    len(announcement_keys),
                    total_rows,
                    chunk_size,
                    dt,
                )

            return result
        finally:
            db.close()

    def has_ohlcv_data(self, ticker: str, start: datetime, end: datetime, max_gap_minutes: int = 5) -> bool:
        """Check if we have complete OHLCV data for a ticker in a time range.

        Returns True only if:
        1. We have a bar within max_gap_minutes of the start time
        2. We have a bar within max_gap_minutes of the end time

        This prevents gaps when overlapping announcement windows have different end times.
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
            start_gap = (first_bar.timestamp - start).total_seconds()
            if start_gap > max_gap_minutes * 60:
                return False

            # Find the last bar in the range
            last_bar = db.query(OHLCVBarDB).filter(
                and_(
                    OHLCVBarDB.ticker == ticker,
                    OHLCVBarDB.timestamp >= start,
                    OHLCVBarDB.timestamp <= end
                )
            ).order_by(OHLCVBarDB.timestamp.desc()).first()

            # Check if last bar is within max_gap_minutes of end
            end_gap = (end - last_bar.timestamp).total_seconds()
            return end_gap <= max_gap_minutes * 60
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────────────────────────
    # Fetch OHLCV via Data Provider
    # ─────────────────────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, ticker: str, start: datetime, end: datetime,
                    use_cache: bool = True,
                    announcement_ticker: str = None,
                    announcement_timestamp: datetime = None) -> Optional[List[OHLCVBar]]:
        """Fetch OHLCV data using the configured data provider, caching in PostgreSQL.

        Args:
            ticker: Stock ticker
            start: Start datetime
            end: End datetime
            use_cache: Whether to use cached data
            announcement_ticker: Optional announcement ticker to link bars to
            announcement_timestamp: Optional announcement timestamp to link bars to
        """

        # Check cache first
        if use_cache and self.has_ohlcv_data(ticker, start, end):
            return self.get_ohlcv_bars(ticker, start, end)

        # Delegate to the configured provider
        bars = self._provider.fetch_ohlcv(ticker, start, end)

        # Cache in database with announcement link
        if bars:
            self.save_ohlcv_bars(ticker, bars,
                                 announcement_ticker=announcement_ticker,
                                 announcement_timestamp=announcement_timestamp)

        return bars

    def update_ohlcv_status(self, ticker: str, timestamp: datetime, status: str) -> bool:
        """Update the OHLCV fetch status for an announcement.

        Args:
            ticker: Stock ticker
            timestamp: Announcement timestamp
            status: 'pending' | 'fetched' | 'no_data' | 'error'

        Returns:
            True if updated, False if announcement not found
        """
        db = self._get_db()
        try:
            ann = db.query(AnnouncementDB).filter(
                and_(
                    AnnouncementDB.ticker == ticker,
                    AnnouncementDB.timestamp == timestamp
                )
            ).first()

            if ann:
                ann.ohlcv_status = status
                db.commit()
                return True
            return False
        finally:
            db.close()

    def fetch_after_announcement(self, ticker: str, announcement_time: datetime,
                                  window_minutes: int = 120,
                                  use_cache: bool = True,
                                  update_status: bool = True) -> Optional[List[OHLCVBar]]:
        """Fetch OHLCV data starting from announcement time.

        Args:
            ticker: Stock ticker
            announcement_time: Naive datetime in UTC (from database)
            window_minutes: How many minutes of data to fetch
            use_cache: Whether to use cached data
            update_status: Whether to update the announcement's ohlcv_status

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
            logger.debug(f"Skipping {ticker}: trading window is today/future ({effective_start.date()})")
            return []

        end_time = effective_start + timedelta(minutes=window_minutes)

        try:
            bars = self.fetch_ohlcv(
                ticker, effective_start, end_time,
                use_cache=use_cache,
                announcement_ticker=ticker,
                announcement_timestamp=announcement_time
            )

            # Update status based on result
            # Provider returns: List with bars = data, [] = confirmed no data, None = error
            if update_status:
                if bars is None:
                    self.update_ohlcv_status(ticker, announcement_time, 'error')
                elif bars:
                    self.update_ohlcv_status(ticker, announcement_time, 'fetched')
                else:  # empty list []
                    self.update_ohlcv_status(ticker, announcement_time, 'no_data')

            return bars if bars is not None else []
        except Exception as e:
            if update_status:
                self.update_ohlcv_status(ticker, announcement_time, 'error')
            raise

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

    def _announcement_to_dict(self, ann: Announcement, source: str = 'backfill') -> dict:
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
            "source": source,
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
            ohlcv_status=row.ohlcv_status or 'pending',
        )


@lru_cache(maxsize=8)
def get_postgres_client(backend: Optional[str] = None) -> PostgresClient:
    """
    Get a cached PostgresClient instance.

    This avoids repeated provider initialization and log spam (especially in Streamlit reruns).
    Safe because DB sessions are created per call via SessionLocal().
    """
    return PostgresClient(backend=backend)
