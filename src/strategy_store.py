"""Strategy persistence for named trading strategies."""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .database import SessionLocal, StrategyDB
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
            db_strategy = StrategyDB(
                id=strategy_id,
                name=name,
                description=description,
                config=json.dumps(config.to_dict()),
                enabled=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(db_strategy)
            db.commit()
            logger.info(f"Saved strategy '{name}' with ID {strategy_id}")
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
        List all strategies.

        Args:
            enabled_only: If True, only return enabled strategies
        """
        db = self._get_db()
        try:
            query = db.query(StrategyDB)
            if enabled_only:
                query = query.filter(StrategyDB.enabled == True)
            rows = query.order_by(StrategyDB.name).all()
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
        Delete a strategy.

        Returns:
            True if deleted, False if not found
        """
        db = self._get_db()
        try:
            row = db.query(StrategyDB).filter(StrategyDB.id == strategy_id).first()
            if not row:
                return False

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
        consec_green_candles=entry.get("consec_green_candles", 1),
        min_candle_volume=entry.get("min_candle_volume", 5000),
        take_profit_pct=exit_rules.get("take_profit_pct", 10.0),
        stop_loss_pct=exit_rules.get("stop_loss_pct", 11.0),
        stop_loss_from_open=exit_rules.get("stop_loss_from_open", True),
        trailing_stop_pct=exit_rules.get("trailing_stop_pct", 7.0),
        timeout_minutes=exit_rules.get("timeout_minutes", 15),
        stake_amount=position.get("stake_amount", 50.0),
    )


# Global instance for convenience
_strategy_store: Optional[StrategyStore] = None


def get_strategy_store() -> StrategyStore:
    """Get the global strategy store."""
    global _strategy_store
    if _strategy_store is None:
        _strategy_store = StrategyStore()
    return _strategy_store
