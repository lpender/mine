"""Trade persistence for live/paper trading."""

import json
import logging
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .database import SessionLocal, TradeDB

logger = logging.getLogger(__name__)


@dataclass
class CompletedTrade:
    """Represents a completed trade."""
    id: Optional[int]
    ticker: str
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    exit_reason: str
    shares: int
    return_pct: float
    pnl: float
    paper: bool
    strategy_params: dict
    strategy_id: Optional[str] = None
    strategy_name: Optional[str] = None
    trade_id: Optional[str] = None  # UUID linking to orders
    created_at: Optional[datetime] = None


class TradeStore:
    """Store for saving/loading completed trades."""

    def _get_db(self) -> Session:
        return SessionLocal()

    def save_trade(
        self,
        trade: dict,
        paper: bool = True,
        strategy_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        trade_id: Optional[str] = None,
    ) -> int:
        """
        Save a completed trade to the database.

        Args:
            trade: Dict with trade details from StrategyEngine
            paper: Whether this was a paper trade
            strategy_id: Optional strategy ID
            strategy_name: Optional strategy name
            trade_id: UUID linking this trade to its orders

        Returns:
            ID of the saved trade
        """
        db = self._get_db()
        try:
            # Parse times if they're strings
            entry_time = trade["entry_time"]
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)

            exit_time = trade["exit_time"]
            if isinstance(exit_time, str):
                exit_time = datetime.fromisoformat(exit_time)

            db_trade = TradeDB(
                ticker=trade["ticker"],
                trade_id=trade_id,
                entry_price=trade["entry_price"],
                entry_time=entry_time,
                exit_price=trade["exit_price"],
                exit_time=exit_time,
                exit_reason=trade.get("exit_reason", ""),
                shares=trade["shares"],
                return_pct=trade.get("return_pct", 0),
                pnl=trade.get("pnl", 0),
                paper=paper,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                strategy_params=json.dumps(trade.get("strategy_params", {})),
            )
            db.add(db_trade)
            db.commit()
            db.refresh(db_trade)
            logger.info(f"Saved trade {db_trade.id}: {trade['ticker']} {trade['return_pct']:+.2f}%")
            return db_trade.id
        finally:
            db.close()

    def get_trades(
        self,
        paper: Optional[bool] = None,
        ticker: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[CompletedTrade]:
        """
        Load trades with optional filters.

        Args:
            paper: Filter by paper/live trading
            ticker: Filter by ticker symbol
            start: Filter by entry time >= start
            end: Filter by entry time <= end
            limit: Max number of trades to return
        """
        db = self._get_db()
        try:
            query = db.query(TradeDB)

            if paper is not None:
                query = query.filter(TradeDB.paper == paper)
            if ticker:
                query = query.filter(TradeDB.ticker == ticker)
            if start:
                query = query.filter(TradeDB.entry_time >= start)
            if end:
                query = query.filter(TradeDB.entry_time <= end)

            rows = query.order_by(TradeDB.entry_time.desc()).limit(limit).all()

            return [self._db_to_trade(row) for row in rows]
        finally:
            db.close()

    def get_trade_stats(self, paper: Optional[bool] = None) -> dict:
        """Get aggregate statistics for trades."""
        db = self._get_db()
        try:
            query = db.query(TradeDB)
            if paper is not None:
                query = query.filter(TradeDB.paper == paper)

            trades = query.all()

            if not trades:
                return {
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0,
                    "total_pnl": 0,
                    "avg_return_pct": 0,
                    "best_trade_pct": 0,
                    "worst_trade_pct": 0,
                }

            wins = sum(1 for t in trades if t.pnl > 0)
            losses = sum(1 for t in trades if t.pnl <= 0)
            total_pnl = sum(t.pnl for t in trades)
            returns = [t.return_pct for t in trades]

            return {
                "total_trades": len(trades),
                "wins": wins,
                "losses": losses,
                "win_rate": wins / len(trades) * 100 if trades else 0,
                "total_pnl": total_pnl,
                "avg_return_pct": sum(returns) / len(returns) if returns else 0,
                "best_trade_pct": max(returns) if returns else 0,
                "worst_trade_pct": min(returns) if returns else 0,
            }
        finally:
            db.close()

    def _db_to_trade(self, row: TradeDB) -> CompletedTrade:
        """Convert database row to CompletedTrade."""
        params = {}
        if row.strategy_params:
            try:
                params = json.loads(row.strategy_params)
            except json.JSONDecodeError:
                pass

        return CompletedTrade(
            id=row.id,
            ticker=row.ticker,
            entry_price=row.entry_price,
            entry_time=row.entry_time,
            exit_price=row.exit_price,
            exit_time=row.exit_time,
            exit_reason=row.exit_reason,
            shares=row.shares,
            return_pct=row.return_pct,
            pnl=row.pnl,
            paper=row.paper,
            strategy_params=params,
            strategy_id=row.strategy_id,
            strategy_name=row.strategy_name,
            trade_id=row.trade_id,
            created_at=row.created_at,
        )


# Global instance for convenience
_trade_store: Optional[TradeStore] = None


def get_trade_store() -> TradeStore:
    """Get the global trade store."""
    global _trade_store
    if _trade_store is None:
        _trade_store = TradeStore()
    return _trade_store
