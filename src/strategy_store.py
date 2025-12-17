"""Strategy persistence for named trading strategies."""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .database import SessionLocal, StrategyDB, ActiveTradeDB
from .strategy import StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class Strategy:
    """Represents a saved strategy."""
    id: str
    name: str
    description: Optional[str]
    config: StrategyConfig
    enabled: bool
    priority: int  # Lower = higher priority (processed first)
    created_at: datetime
    updated_at: datetime


class StrategyStore:
    """CRUD operations for trading strategies."""

    def _get_db(self) -> Session:
        return SessionLocal()

    def save_strategy(
        self,
        name: str,
        config: StrategyConfig,
        description: Optional[str] = None,
    ) -> str:
        """
        Save a new strategy to the database.

        Args:
            name: Unique name for the strategy
            config: StrategyConfig instance
            description: Optional description

        Returns:
            ID of the saved strategy
        """
        db = self._get_db()
        try:
            strategy_id = str(uuid.uuid4())

            # Get next priority (new strategies go to end)
            from sqlalchemy import func
            max_priority = db.query(func.max(StrategyDB.priority)).scalar() or -1
            next_priority = max_priority + 1

            db_strategy = StrategyDB(
                id=strategy_id,
                name=name,
                description=description,
                config=json.dumps(config.to_dict()),
                enabled=False,
                priority=next_priority,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(db_strategy)
            db.commit()
            logger.info(f"Saved strategy '{name}' with ID {strategy_id}, priority={next_priority}")
            return strategy_id
        finally:
            db.close()

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """Get a strategy by ID."""
        db = self._get_db()
        try:
            row = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not row:
                return None
            return self._db_to_strategy(row)
        finally:
            db.close()

    def get_strategy_by_name(self, name: str) -> Optional[Strategy]:
        """Get a strategy by name."""
        db = self._get_db()
        try:
            row = db.query(StrategyDB).filter(StrategyDB.name == name).first()
            if not row:
                return None
            return self._db_to_strategy(row)
        finally:
            db.close()

    def list_strategies(self, enabled_only: bool = False) -> List[Strategy]:
        """
        List all strategies ordered by priority (lower = higher priority).

        Args:
            enabled_only: If True, only return enabled strategies
        """
        db = self._get_db()
        try:
            query = db.query(StrategyDB)
            if enabled_only:
                query = query.filter(StrategyDB.enabled == True)
            rows = query.order_by(StrategyDB.priority, StrategyDB.name).all()
            return [self._db_to_strategy(row) for row in rows]
        finally:
            db.close()

    def update_strategy(
        self,
        strategy_id: str,
        config: Optional[StrategyConfig] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """
        Update a strategy.

        Returns:
            True if updated, False if not found
        """
        db = self._get_db()
        try:
            row = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not row:
                return False

            if config is not None:
                row.config = json.dumps(config.to_dict())
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description

            row.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Updated strategy {strategy_id}")
            return True
        finally:
            db.close()

    def delete_strategy(self, strategy_id: str) -> bool:
        """
        Delete a strategy and any associated active trades.

        Returns:
            True if deleted, False if not found
        """
        db = self._get_db()
        try:
            row = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not row:
                return False

            # Delete any active trades for this strategy first (foreign key constraint)
            deleted_trades = db.query(ActiveTradeDB).filter(
                ActiveTradeDB.strategy_id == strategy_id
            ).delete()
            if deleted_trades > 0:
                logger.info(f"Deleted {deleted_trades} active trades for strategy {strategy_id}")

            db.delete(row)
            db.commit()
            logger.info(f"Deleted strategy {strategy_id}")
            return True
        finally:
            db.close()

    def set_enabled(self, strategy_id: str, enabled: bool) -> bool:
        """
        Enable or disable a strategy.

        Returns:
            True if updated, False if not found
        """
        db = self._get_db()
        try:
            row = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not row:
                return False

            row.enabled = enabled
            row.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Strategy {strategy_id} enabled={enabled}")
            return True
        finally:
            db.close()

    def move_strategy_up(self, strategy_id: str) -> bool:
        """
        Move a strategy up in priority (lower number = higher priority).

        Returns:
            True if moved, False if already at top or not found
        """
        db = self._get_db()
        try:
            # Get current strategy
            current = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not current:
                return False

            # Find strategy with next lower priority (higher in list)
            above = db.query(StrategyDB).filter(
                StrategyDB.priority < current.priority
            ).order_by(StrategyDB.priority.desc()).first()

            if not above:
                return False  # Already at top

            # Swap priorities
            current.priority, above.priority = above.priority, current.priority
            current.updated_at = datetime.utcnow()
            above.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Moved strategy {strategy_id} up (priority {current.priority} <-> {above.priority})")
            return True
        finally:
            db.close()

    def move_strategy_down(self, strategy_id: str) -> bool:
        """
        Move a strategy down in priority (higher number = lower priority).

        Returns:
            True if moved, False if already at bottom or not found
        """
        db = self._get_db()
        try:
            # Get current strategy
            current = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not current:
                return False

            # Find strategy with next higher priority (lower in list)
            below = db.query(StrategyDB).filter(
                StrategyDB.priority > current.priority
            ).order_by(StrategyDB.priority.asc()).first()

            if not below:
                return False  # Already at bottom

            # Swap priorities
            current.priority, below.priority = below.priority, current.priority
            current.updated_at = datetime.utcnow()
            below.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Moved strategy {strategy_id} down (priority {current.priority} <-> {below.priority})")
            return True
        finally:
            db.close()

    def _db_to_strategy(self, row: StrategyDB) -> Strategy:
        """Convert database row to Strategy."""
        config_dict = {}
        if row.config:
            try:
                config_dict = json.loads(row.config)
            except json.JSONDecodeError:
                pass

        config = _config_from_dict(config_dict)

        return Strategy(
            id=row.id,
            name=row.name,
            description=row.description,
            config=config,
            enabled=row.enabled,
            priority=row.priority if row.priority is not None else 0,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _config_from_dict(data: dict) -> StrategyConfig:
    """Reconstruct StrategyConfig from serialized dict."""
    if not data:
        return StrategyConfig()

    filters = data.get("filters", {})
    entry = data.get("entry", {})
    exit_rules = data.get("exit", {})
    position = data.get("position", {})

    return StrategyConfig(
        channels=filters.get("channels", ["select-news"]),
        directions=filters.get("directions", ["up_right"]),
        price_min=filters.get("price_min", 1.0),
        price_max=filters.get("price_max", 10.0),
        sessions=filters.get("sessions", ["premarket", "market"]),
        country_blacklist=filters.get("country_blacklist", []),
        max_intraday_mentions=filters.get("max_intraday_mentions"),
        exclude_financing_headlines=filters.get("exclude_financing_headlines", False),
        consec_green_candles=entry.get("consec_green_candles", 1),
        min_candle_volume=entry.get("min_candle_volume", 5000),
        entry_window_minutes=entry.get("entry_window_minutes", 5),
        take_profit_pct=exit_rules.get("take_profit_pct", 10.0),
        stop_loss_pct=exit_rules.get("stop_loss_pct", 11.0),
        stop_loss_from_open=exit_rules.get("stop_loss_from_open", True),
        trailing_stop_pct=exit_rules.get("trailing_stop_pct", 7.0),
        timeout_minutes=exit_rules.get("timeout_minutes", 15),
        stake_mode=position.get("stake_mode", "fixed"),
        stake_amount=position.get("stake_amount", 50.0),
        volume_pct=position.get("volume_pct", 1.0),
        max_stake=position.get("max_stake", 10000.0),
    )


# Global instance for convenience
_strategy_store: Optional[StrategyStore] = None


def get_strategy_store() -> StrategyStore:
    """Get the global strategy store."""
    global _strategy_store
    if _strategy_store is None:
        _strategy_store = StrategyStore()
    return _strategy_store
