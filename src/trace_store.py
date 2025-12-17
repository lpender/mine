"""Trace persistence for alert lifecycle tracking."""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from .database import SessionLocal, TraceDB, TraceEventDB

logger = logging.getLogger(__name__)


class TraceStore:
    """CRUD operations for traces and trace events."""

    def create_trace(
        self,
        trace_id: str,
        ticker: str,
        alert_timestamp: datetime,
        alert_key: Optional[str] = None,
        channel: Optional[str] = None,
        author: Optional[str] = None,
        price_threshold: Optional[float] = None,
        headline: Optional[str] = None,
        raw_content: Optional[str] = None,
        announcement_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Create a new trace record.

        Returns:
            Trace DB ID if successful, None otherwise.
        """
        session = SessionLocal()
        try:
            trace = TraceDB(
                trace_id=trace_id,
                ticker=ticker,
                alert_timestamp=alert_timestamp,
                alert_key=alert_key,
                channel=channel,
                author=author,
                price_threshold=price_threshold,
                headline=headline,
                raw_content=raw_content,
                announcement_id=announcement_id,
                status='received',
            )
            session.add(trace)
            session.commit()
            logger.info(f"[{ticker}] Created trace {trace_id}")
            return trace.id
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to create trace: {e}")
            return None
        finally:
            session.close()

    def update_trace_status(
        self,
        trace_id: str,
        status: str,
        pending_entry_trade_id: Optional[str] = None,
        active_trade_id: Optional[str] = None,
        completed_trade_id: Optional[int] = None,
        exit_reason: Optional[str] = None,
        pnl: Optional[float] = None,
        return_pct: Optional[float] = None,
        completed_at: Optional[datetime] = None,
    ) -> bool:
        """Update trace status and outcome fields."""
        session = SessionLocal()
        try:
            trace = session.query(TraceDB).filter(TraceDB.trace_id == trace_id).first()
            if not trace:
                logger.warning(f"Trace not found: {trace_id}")
                return False

            trace.status = status
            if pending_entry_trade_id is not None:
                trace.pending_entry_trade_id = pending_entry_trade_id
            if active_trade_id is not None:
                trace.active_trade_id = active_trade_id
            if completed_trade_id is not None:
                trace.completed_trade_id = completed_trade_id
            if exit_reason is not None:
                trace.exit_reason = exit_reason
            if pnl is not None:
                trace.pnl = pnl
            if return_pct is not None:
                trace.return_pct = return_pct
            if completed_at is not None:
                trace.completed_at = completed_at

            session.commit()
            logger.debug(f"Updated trace {trace_id}: status={status}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update trace status: {e}")
            return False
        finally:
            session.close()

    def add_event(
        self,
        trace_id: str,
        event_type: str,
        event_timestamp: datetime,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        reason: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        Add an event to a trace.

        Returns:
            Event ID if successful, None otherwise.
        """
        session = SessionLocal()
        try:
            event = TraceEventDB(
                trace_id=trace_id,
                event_type=event_type,
                event_timestamp=event_timestamp,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                reason=reason,
                details=json.dumps(details) if details else None,
            )
            session.add(event)
            session.commit()
            logger.debug(f"Recorded trace event: {event_type} for trace {trace_id}")
            return event.id
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to record trace event: {e}")
            return None
        finally:
            session.close()

    def get_trace(self, trace_id: str) -> Optional[TraceDB]:
        """Get a trace by ID."""
        session = SessionLocal()
        try:
            trace = session.query(TraceDB).filter(TraceDB.trace_id == trace_id).first()
            if trace:
                session.expunge(trace)
            return trace
        finally:
            session.close()

    def get_trace_by_alert_key(self, alert_key: str) -> Optional[TraceDB]:
        """Get a trace by its alert key (for deduplication lookup)."""
        session = SessionLocal()
        try:
            trace = session.query(TraceDB).filter(TraceDB.alert_key == alert_key).first()
            if trace:
                session.expunge(trace)
            return trace
        finally:
            session.close()

    def get_events_for_trace(self, trace_id: str) -> List[TraceEventDB]:
        """Get all events for a trace, ordered by timestamp."""
        session = SessionLocal()
        try:
            events = session.query(TraceEventDB).filter(
                TraceEventDB.trace_id == trace_id
            ).order_by(TraceEventDB.event_timestamp.asc()).all()
            for e in events:
                session.expunge(e)
            return events
        finally:
            session.close()

    def get_recent_traces(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        ticker: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> List[TraceDB]:
        """Get recent traces with optional filters."""
        session = SessionLocal()
        try:
            query = session.query(TraceDB)

            if status:
                query = query.filter(TraceDB.status == status)
            if ticker:
                query = query.filter(TraceDB.ticker == ticker)
            if strategy_id:
                # Need to join with trace_events to filter by strategy
                query = query.join(TraceEventDB).filter(
                    TraceEventDB.strategy_id == strategy_id
                ).distinct()

            traces = query.order_by(TraceDB.created_at.desc()).limit(limit).all()
            for t in traces:
                session.expunge(t)
            return traces
        finally:
            session.close()

    def get_filter_rejections_by_strategy(
        self,
        strategy_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[TraceEventDB]:
        """Get all filter rejection events for a strategy."""
        session = SessionLocal()
        try:
            query = session.query(TraceEventDB).filter(
                TraceEventDB.strategy_id == strategy_id,
                TraceEventDB.event_type == 'filter_rejected',
            )

            if start:
                query = query.filter(TraceEventDB.event_timestamp >= start)
            if end:
                query = query.filter(TraceEventDB.event_timestamp <= end)

            events = query.order_by(TraceEventDB.event_timestamp.desc()).all()
            for e in events:
                session.expunge(e)
            return events
        finally:
            session.close()


# Global instance
_trace_store: Optional[TraceStore] = None


def get_trace_store() -> TraceStore:
    """Get the global trace store instance."""
    global _trace_store
    if _trace_store is None:
        _trace_store = TraceStore()
    return _trace_store
