"""Order persistence for tracking orders and events."""

import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

from .base_store import BaseStore
from .database import OrderDB, OrderEventDB

logger = logging.getLogger(__name__)


class OrderStore(BaseStore):
    """CRUD operations for orders and order events."""

    def create_order(
        self,
        ticker: str,
        side: str,
        order_type: str,
        requested_shares: int,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        limit_price: Optional[float] = None,
        broker_order_id: Optional[str] = None,
        active_trade_id: Optional[int] = None,
        trade_id: Optional[str] = None,
        paper: bool = True,
    ) -> Optional[int]:
        """
        Create a new order record.

        Returns:
            Order ID if successful, None otherwise.
        """
        try:
            with self._db_session() as session:
                order = OrderDB(
                    ticker=ticker,
                    side=side,
                    order_type=order_type,
                    requested_shares=requested_shares,
                    limit_price=limit_price,
                    broker_order_id=broker_order_id,
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    active_trade_id=active_trade_id,
                    trade_id=trade_id,
                    paper=paper,
                    status='pending',
                )
                session.add(order)
                session.flush()  # Get the ID before commit
                order_id = order.id
                logger.info(f"[{ticker}] Created order {order_id}: {side} {requested_shares} shares")
                return order_id
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            return None

    def update_broker_order_id(self, order_id: int, broker_order_id: str) -> bool:
        """Update the broker order ID after order submission."""
        try:
            with self._db_session() as session:
                order = session.query(OrderDB).filter(OrderDB.id == order_id).first()
                if order:
                    order.broker_order_id = broker_order_id
                    order.updated_at = datetime.utcnow()
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to update broker order ID: {e}")
            return False

    def update_order_status(
        self,
        order_id: Optional[int] = None,
        broker_order_id: Optional[str] = None,
        status: Optional[str] = None,
        filled_shares: Optional[int] = None,
        avg_fill_price: Optional[float] = None,
    ) -> bool:
        """Update order status and fill info."""
        try:
            with self._db_session() as session:
                if order_id:
                    order = session.query(OrderDB).filter(OrderDB.id == order_id).first()
                elif broker_order_id:
                    order = session.query(OrderDB).filter(OrderDB.broker_order_id == broker_order_id).first()
                else:
                    return False

                if not order:
                    return False

                if status:
                    order.status = status
                if filled_shares is not None:
                    order.filled_shares = filled_shares
                if avg_fill_price is not None:
                    order.avg_fill_price = avg_fill_price
                order.updated_at = datetime.utcnow()

                logger.info(f"[{order.ticker}] Updated order {order.id}: status={status}, filled={filled_shares}")
                return True
        except Exception as e:
            logger.error(f"Failed to update order status: {e}")
            return False

    def record_event(
        self,
        event_type: str,
        event_timestamp: datetime,
        order_id: Optional[int] = None,
        broker_order_id: Optional[str] = None,
        filled_shares: Optional[int] = None,
        fill_price: Optional[float] = None,
        cumulative_filled: Optional[int] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        Record an order event.

        Returns:
            Event ID if successful, None otherwise.
        """
        try:
            with self._db_session() as session:
                # If we only have broker_order_id, try to find our order_id
                if order_id is None and broker_order_id:
                    order = session.query(OrderDB).filter(
                        OrderDB.broker_order_id == broker_order_id
                    ).first()
                    if order:
                        order_id = order.id

                event = OrderEventDB(
                    order_id=order_id,
                    broker_order_id=broker_order_id,
                    event_type=event_type,
                    filled_shares=filled_shares,
                    fill_price=fill_price,
                    cumulative_filled=cumulative_filled,
                    raw_data=json.dumps(raw_data) if raw_data else None,
                    event_timestamp=event_timestamp,
                )
                session.add(event)
                session.flush()
                event_id = event.id
                logger.debug(f"Recorded order event: {event_type} for order {order_id or broker_order_id}")
                return event_id
        except Exception as e:
            logger.error(f"Failed to record order event: {e}")
            return None

    def get_order(self, order_id: Optional[int] = None, broker_order_id: Optional[str] = None) -> Optional[OrderDB]:
        """Get an order by ID or broker order ID."""
        with self._db_session() as session:
            if order_id:
                order = session.query(OrderDB).filter(OrderDB.id == order_id).first()
            elif broker_order_id:
                order = session.query(OrderDB).filter(OrderDB.broker_order_id == broker_order_id).first()
            else:
                return None

            if order:
                session.expunge(order)
            return order

    def get_pending_orders(self, ticker: Optional[str] = None, strategy_id: Optional[str] = None) -> List[OrderDB]:
        """Get all pending orders, optionally filtered by ticker or strategy."""
        with self._db_session() as session:
            query = session.query(OrderDB).filter(OrderDB.status.in_(['pending', 'partial']))

            if ticker:
                query = query.filter(OrderDB.ticker == ticker)
            if strategy_id:
                query = query.filter(OrderDB.strategy_id == strategy_id)

            orders = query.order_by(OrderDB.created_at.desc()).all()
            for o in orders:
                session.expunge(o)
            return orders

    def get_orders_for_strategy(self, strategy_id: str, limit: int = 100) -> List[OrderDB]:
        """Get recent orders for a strategy."""
        with self._db_session() as session:
            orders = session.query(OrderDB).filter(
                OrderDB.strategy_id == strategy_id
            ).order_by(OrderDB.created_at.desc()).limit(limit).all()
            for o in orders:
                session.expunge(o)
            return orders

    def get_events_for_order(self, order_id: int) -> List[OrderEventDB]:
        """Get all events for an order."""
        with self._db_session() as session:
            events = session.query(OrderEventDB).filter(
                OrderEventDB.order_id == order_id
            ).order_by(OrderEventDB.event_timestamp.asc()).all()
            for e in events:
                session.expunge(e)
            return events


# Global instance
_order_store: Optional[OrderStore] = None


def get_order_store() -> OrderStore:
    """Get the global order store instance."""
    global _order_store
    if _order_store is None:
        _order_store = OrderStore()
    return _order_store
