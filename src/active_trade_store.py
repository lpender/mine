"""Store for persisting active trades to database."""

import logging
from datetime import datetime
from typing import List, Optional

from .database import SessionLocal, ActiveTradeDB

logger = logging.getLogger(__name__)


class ActiveTradeStore:
    """CRUD operations for active trades."""

    def save_trade(
        self,
        ticker: str,
        strategy_id: Optional[str],
        strategy_name: Optional[str],
        entry_price: float,
        entry_time: datetime,
        first_candle_open: float,
        shares: int,
        stop_loss_price: float,
        take_profit_price: float,
        highest_since_entry: float,
        paper: bool = True,
        announcement_ticker: Optional[str] = None,
        announcement_timestamp: Optional[datetime] = None,
    ) -> bool:
        """Save or update an active trade."""
        session = SessionLocal()
        try:
            # Check if trade already exists
            existing = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.ticker == ticker,
                ActiveTradeDB.strategy_id == strategy_id,
            ).first()

            if existing:
                # Update existing
                existing.entry_price = entry_price
                existing.entry_time = entry_time
                existing.first_candle_open = first_candle_open
                existing.shares = shares
                existing.stop_loss_price = stop_loss_price
                existing.take_profit_price = take_profit_price
                existing.highest_since_entry = highest_since_entry
                existing.paper = paper
                existing.updated_at = datetime.utcnow()
            else:
                # Create new
                trade = ActiveTradeDB(
                    ticker=ticker,
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    entry_price=entry_price,
                    entry_time=entry_time,
                    first_candle_open=first_candle_open,
                    shares=shares,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    highest_since_entry=highest_since_entry,
                    paper=paper,
                    announcement_ticker=announcement_ticker,
                    announcement_timestamp=announcement_timestamp,
                )
                session.add(trade)

            session.commit()
            logger.info(f"[{ticker}] Saved active trade to database")
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save active trade: {e}")
            return False
        finally:
            session.close()

    def update_price(
        self,
        ticker: str,
        strategy_id: Optional[str],
        last_price: float,
        highest_since_entry: float,
        last_quote_time: datetime,
    ) -> bool:
        """Update price tracking for an active trade."""
        session = SessionLocal()
        try:
            trade = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.ticker == ticker,
                ActiveTradeDB.strategy_id == strategy_id,
            ).first()

            if trade:
                trade.last_price = last_price
                trade.highest_since_entry = highest_since_entry
                trade.last_quote_time = last_quote_time
                trade.updated_at = datetime.utcnow()
                session.commit()
                return True
            return False

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update trade price: {e}")
            return False
        finally:
            session.close()

    def delete_trade(self, ticker: str, strategy_id: Optional[str]) -> bool:
        """Delete an active trade (when position is closed)."""
        session = SessionLocal()
        try:
            trade = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.ticker == ticker,
                ActiveTradeDB.strategy_id == strategy_id,
            ).first()

            if trade:
                session.delete(trade)
                session.commit()
                logger.info(f"[{ticker}] Deleted active trade from database")
                return True
            return False

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete active trade: {e}")
            return False
        finally:
            session.close()

    def get_trades_for_strategy(self, strategy_id: str) -> List[ActiveTradeDB]:
        """Get all active trades for a strategy."""
        session = SessionLocal()
        try:
            trades = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.strategy_id == strategy_id
            ).all()
            # Detach from session
            for t in trades:
                session.expunge(t)
            return trades
        finally:
            session.close()

    def get_all_trades(self) -> List[ActiveTradeDB]:
        """Get all active trades."""
        session = SessionLocal()
        try:
            trades = session.query(ActiveTradeDB).all()
            for t in trades:
                session.expunge(t)
            return trades
        finally:
            session.close()

    def clear_strategy_trades(self, strategy_id: str) -> int:
        """Delete all active trades for a strategy."""
        session = SessionLocal()
        try:
            count = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.strategy_id == strategy_id
            ).delete()
            session.commit()
            logger.info(f"Cleared {count} active trades for strategy {strategy_id}")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to clear strategy trades: {e}")
            return 0
        finally:
            session.close()


# Global instance
_active_trade_store: Optional[ActiveTradeStore] = None


def get_active_trade_store() -> ActiveTradeStore:
    """Get the global active trade store instance."""
    global _active_trade_store
    if _active_trade_store is None:
        _active_trade_store = ActiveTradeStore()
    return _active_trade_store
