"""Store for persisting orphaned orders to database."""

import logging
from datetime import datetime
from typing import Optional

from .database import SessionLocal, OrphanedOrderDB

logger = logging.getLogger(__name__)


class OrphanedOrderStore:
    """CRUD operations for orphaned orders."""

    def record_orphaned_order(
        self,
        broker_order_id: str,
        ticker: str,
        side: str,
        shares: int,
        order_type: str,
        status: str,
        limit_price: Optional[float] = None,
        order_created_at: Optional[datetime] = None,
        strategy_name: Optional[str] = None,
        reason: Optional[str] = None,
        paper: bool = True,
    ) -> Optional[int]:
        """
        Record an orphaned order found in the broker.

        Returns:
            Record ID if successful, None otherwise.
        """
        session = SessionLocal()
        try:
            # Check if we already recorded this order
            existing = session.query(OrphanedOrderDB).filter(
                OrphanedOrderDB.broker_order_id == broker_order_id
            ).first()

            if existing:
                logger.debug(f"[{ticker}] Orphaned order {broker_order_id} already recorded")
                return existing.id

            orphaned_order = OrphanedOrderDB(
                broker_order_id=broker_order_id,
                ticker=ticker,
                side=side,
                shares=shares,
                order_type=order_type,
                status=status,
                limit_price=limit_price,
                order_created_at=order_created_at,
                strategy_name=strategy_name,
                reason=reason,
                paper=paper,
            )
            session.add(orphaned_order)
            session.commit()
            logger.info(f"[{ticker}] Recorded orphaned order {broker_order_id}: {side} {shares} shares @ ${limit_price}")
            return orphaned_order.id
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to record orphaned order: {e}")
            return None
        finally:
            session.close()

    def mark_as_cancelled(self, broker_order_id: str, reason: Optional[str] = None) -> bool:
        """Mark an orphaned order as cancelled."""
        session = SessionLocal()
        try:
            orphaned_order = session.query(OrphanedOrderDB).filter(
                OrphanedOrderDB.broker_order_id == broker_order_id
            ).first()

            if not orphaned_order:
                logger.warning(f"Orphaned order {broker_order_id} not found in database")
                return False

            orphaned_order.cancelled_at = datetime.utcnow()
            if reason:
                orphaned_order.reason = reason

            session.commit()
            logger.info(f"Marked orphaned order {broker_order_id} as cancelled")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to mark orphaned order as cancelled: {e}")
            return False
        finally:
            session.close()


# Singleton
_orphaned_order_store = None


def get_orphaned_order_store() -> OrphanedOrderStore:
    """Get the singleton orphaned order store."""
    global _orphaned_order_store
    if _orphaned_order_store is None:
        _orphaned_order_store = OrphanedOrderStore()
    return _orphaned_order_store

