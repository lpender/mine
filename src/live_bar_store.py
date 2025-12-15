"""Store for persisting live 1-second bars to database."""

import logging
from datetime import datetime
from typing import List, Optional

from .database import SessionLocal, LiveBarDB

logger = logging.getLogger(__name__)


class LiveBarStore:
    """CRUD operations for live bars."""

    def save_bar(
        self,
        ticker: str,
        timestamp: datetime,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        strategy_id: Optional[str] = None,
    ) -> bool:
        """Save a 1-second bar (upsert on ticker+timestamp)."""
        session = SessionLocal()
        try:
            # Check if bar already exists
            existing = session.query(LiveBarDB).filter(
                LiveBarDB.ticker == ticker,
                LiveBarDB.timestamp == timestamp,
            ).first()

            if existing:
                # Update existing (shouldn't happen often but handle it)
                existing.open = open_price
                existing.high = high
                existing.low = low
                existing.close = close
                existing.volume = volume
                if strategy_id:
                    existing.strategy_id = strategy_id
            else:
                # Create new
                bar = LiveBarDB(
                    ticker=ticker,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    strategy_id=strategy_id,
                )
                session.add(bar)

            session.commit()
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save live bar: {e}")
            return False
        finally:
            session.close()

    def save_bars_batch(self, bars: List[dict]) -> int:
        """
        Save multiple bars in a single transaction.

        Args:
            bars: List of dicts with keys: ticker, timestamp, open, high, low, close, volume, strategy_id

        Returns:
            Number of bars saved
        """
        if not bars:
            return 0

        session = SessionLocal()
        try:
            count = 0
            for bar_data in bars:
                # Use merge for upsert behavior
                bar = LiveBarDB(
                    ticker=bar_data["ticker"],
                    timestamp=bar_data["timestamp"],
                    open=bar_data["open"],
                    high=bar_data["high"],
                    low=bar_data["low"],
                    close=bar_data["close"],
                    volume=bar_data["volume"],
                    strategy_id=bar_data.get("strategy_id"),
                )
                session.merge(bar)
                count += 1

            session.commit()
            return count

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save bar batch: {e}")
            return 0
        finally:
            session.close()

    def get_bars(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
        strategy_id: Optional[str] = None,
    ) -> List[LiveBarDB]:
        """
        Get bars for a ticker within a time range.

        Args:
            ticker: Stock symbol
            start_time: Start of time range
            end_time: End of time range
            strategy_id: Optional filter by strategy

        Returns:
            List of LiveBarDB records ordered by timestamp
        """
        session = SessionLocal()
        try:
            query = session.query(LiveBarDB).filter(
                LiveBarDB.ticker == ticker,
                LiveBarDB.timestamp >= start_time,
                LiveBarDB.timestamp <= end_time,
            )

            if strategy_id:
                query = query.filter(LiveBarDB.strategy_id == strategy_id)

            bars = query.order_by(LiveBarDB.timestamp).all()

            # Detach from session
            for bar in bars:
                session.expunge(bar)

            return bars

        finally:
            session.close()

    def delete_bars(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """Delete bars within a time range (for cleanup)."""
        session = SessionLocal()
        try:
            count = session.query(LiveBarDB).filter(
                LiveBarDB.ticker == ticker,
                LiveBarDB.timestamp >= start_time,
                LiveBarDB.timestamp <= end_time,
            ).delete()
            session.commit()
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete bars: {e}")
            return 0
        finally:
            session.close()

    def delete_old_bars(self, before: datetime) -> int:
        """Delete all bars older than a given time (for cleanup)."""
        session = SessionLocal()
        try:
            count = session.query(LiveBarDB).filter(
                LiveBarDB.timestamp < before
            ).delete()
            session.commit()
            logger.info(f"Deleted {count} old live bars")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete old bars: {e}")
            return 0
        finally:
            session.close()


# Global instance
_live_bar_store: Optional[LiveBarStore] = None


def get_live_bar_store() -> LiveBarStore:
    """Get the global live bar store instance."""
    global _live_bar_store
    if _live_bar_store is None:
        _live_bar_store = LiveBarStore()
    return _live_bar_store
