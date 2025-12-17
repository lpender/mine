"""Store for persisting pending entries to database."""

import logging
from datetime import datetime
from typing import List, Optional

from .database import SessionLocal, PendingEntryDB

logger = logging.getLogger(__name__)


class PendingEntryStore:
    """CRUD operations for pending entries."""

    def save_entry(
        self,
        trade_id: str,
        ticker: str,
        strategy_id: Optional[str],
        strategy_name: Optional[str],
        alert_time: datetime,
        first_price: Optional[float],
        announcement_ticker: str,
        announcement_timestamp: datetime,
    ) -> bool:
        """Save a pending entry."""
        session = SessionLocal()
        try:
            # Check if entry already exists by trade_id
            existing = session.query(PendingEntryDB).filter(
                PendingEntryDB.trade_id == trade_id,
            ).first()

            if existing:
                # Update existing
                existing.first_price = first_price
            else:
                # Create new
                entry = PendingEntryDB(
                    trade_id=trade_id,
                    ticker=ticker,
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    alert_time=alert_time,
                    first_price=first_price,
                    announcement_ticker=announcement_ticker,
                    announcement_timestamp=announcement_timestamp,
                )
                session.add(entry)

            session.commit()
            logger.info(f"[{ticker}] Saved pending entry to database (trade_id={trade_id[:8]})")
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save pending entry: {e}")
            return False
        finally:
            session.close()

    def delete_entry(self, trade_id: str) -> bool:
        """Delete a pending entry by trade_id (when entry is filled or abandoned)."""
        session = SessionLocal()
        try:
            entry = session.query(PendingEntryDB).filter(
                PendingEntryDB.trade_id == trade_id,
            ).first()

            if entry:
                ticker = entry.ticker
                session.delete(entry)
                session.commit()
                logger.info(f"[{ticker}] Deleted pending entry from database (trade_id={trade_id[:8]})")
                return True
            return False

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete pending entry: {e}")
            return False
        finally:
            session.close()

    def get_entry(self, trade_id: str) -> Optional[PendingEntryDB]:
        """Get a specific pending entry by trade_id."""
        session = SessionLocal()
        try:
            entry = session.query(PendingEntryDB).filter(
                PendingEntryDB.trade_id == trade_id,
            ).first()
            if entry:
                session.expunge(entry)
            return entry
        finally:
            session.close()

    def get_entries_for_strategy(self, strategy_id: str) -> List[PendingEntryDB]:
        """Get all pending entries for a strategy."""
        session = SessionLocal()
        try:
            entries = session.query(PendingEntryDB).filter(
                PendingEntryDB.strategy_id == strategy_id
            ).all()
            # Detach from session
            for e in entries:
                session.expunge(e)
            return entries
        finally:
            session.close()

    def get_all_entries(self) -> List[PendingEntryDB]:
        """Get all pending entries."""
        session = SessionLocal()
        try:
            entries = session.query(PendingEntryDB).all()
            for e in entries:
                session.expunge(e)
            return entries
        finally:
            session.close()

    def clear_strategy_entries(self, strategy_id: str) -> int:
        """Delete all pending entries for a strategy."""
        session = SessionLocal()
        try:
            count = session.query(PendingEntryDB).filter(
                PendingEntryDB.strategy_id == strategy_id
            ).delete()
            session.commit()
            logger.info(f"Cleared {count} pending entries for strategy {strategy_id}")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to clear strategy entries: {e}")
            return 0
        finally:
            session.close()


# Global instance
_pending_entry_store: Optional[PendingEntryStore] = None


def get_pending_entry_store() -> PendingEntryStore:
    """Get the global pending entry store instance."""
    global _pending_entry_store
    if _pending_entry_store is None:
        _pending_entry_store = PendingEntryStore()
    return _pending_entry_store
