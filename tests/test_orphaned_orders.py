"""Tests for orphaned order detection and auto-cancellation."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from src.strategy import StrategyEngine, StrategyConfig
from src.trading.base import Order


def test_orphaned_order_detection(test_db_session):
    """Test that orphaned orders are detected and logged."""
    # Setup
    config = StrategyConfig(
        buy_order_timeout_seconds=5,
    )

    mock_trader = Mock()
    mock_trader.get_positions.return_value = []

    # Create orphaned orders (not in pending_orders)
    orphaned_orders = [
        Order(
            order_id="orphan1",
            ticker="TEST",
            side="buy",
            shares=100,
            order_type="limit",
            status="new",
            created_at=datetime.utcnow() - timedelta(seconds=10),  # 10 seconds old
            limit_price=10.50,
        ),
        Order(
            order_id="orphan2",
            ticker="TEST",
            side="buy",
            shares=50,
            order_type="limit",
            status="new",
            created_at=datetime.utcnow() - timedelta(seconds=3),  # 3 seconds old
            limit_price=10.50,
        ),
    ]
    mock_trader.get_open_orders.return_value = orphaned_orders

    engine = StrategyEngine(
        config=config,
        trader=mock_trader,
        strategy_name="test_strategy",
        strategy_id="test_id",
        paper=True,
    )

    # Execute
    with patch('src.strategy.logger') as mock_logger:
        engine._recover_pending_orders()

        # Verify warnings were logged
        assert any("UNTRACKED ORDERS" in str(call) for call in mock_logger.warning.call_args_list)
        assert any("Orphaned buy order" in str(call) for call in mock_logger.warning.call_args_list)


def test_orphaned_order_auto_cancel(test_db_session):
    """Test that old orphaned orders are auto-cancelled."""
    # Setup
    config = StrategyConfig(
        buy_order_timeout_seconds=5,
    )

    mock_trader = Mock()
    mock_trader.get_positions.return_value = []
    mock_trader.cancel_order.return_value = True

    # Create old orphaned order (should be cancelled)
    old_order = Order(
        order_id="old_orphan",
        ticker="TEST",
        side="buy",
        shares=100,
        order_type="limit",
        status="new",
        created_at=datetime.utcnow() - timedelta(seconds=10),  # 10 seconds old (> 5s threshold)
        limit_price=10.50,
    )
    mock_trader.get_open_orders.return_value = [old_order]

    engine = StrategyEngine(
        config=config,
        trader=mock_trader,
        strategy_name="test_strategy",
        strategy_id="test_id",
        paper=True,
    )

    # Execute
    with patch('src.strategy.logger') as mock_logger:
        engine._recover_pending_orders()

        # Verify order was cancelled (may be called multiple times from different recovery methods)
        assert mock_trader.cancel_order.called
        assert "old_orphan" in [call[0][0] for call in mock_trader.cancel_order.call_args_list]

        # Verify cancellation was logged
        assert any("Auto-canceling" in str(call) for call in mock_logger.warning.call_args_list)
        assert any("Successfully cancelled" in str(call) for call in mock_logger.info.call_args_list)


def test_orphaned_order_not_cancelled_if_recent(test_db_session):
    """Test that recent orphaned orders are NOT auto-cancelled."""
    # Setup
    config = StrategyConfig(
        buy_order_timeout_seconds=5,
    )

    mock_trader = Mock()
    mock_trader.get_positions.return_value = []

    # Create recent orphaned order (should NOT be cancelled)
    recent_order = Order(
        order_id="recent_orphan",
        ticker="TEST",
        side="buy",
        shares=100,
        order_type="limit",
        status="new",
        created_at=datetime.utcnow() - timedelta(seconds=2),  # 2 seconds old (< 5s threshold)
        limit_price=10.50,
    )
    mock_trader.get_open_orders.return_value = [recent_order]

    engine = StrategyEngine(
        config=config,
        trader=mock_trader,
        strategy_name="test_strategy",
        strategy_id="test_id",
        paper=True,
    )

    # Execute
    with patch('src.strategy.logger'):
        engine._recover_pending_orders()

        # Verify order was NOT cancelled
        mock_trader.cancel_order.assert_not_called()


def test_orphaned_order_store(test_db_session):
    """Test that orphaned orders are recorded in database."""
    from src.orphaned_order_store import OrphanedOrderStore

    store = OrphanedOrderStore()

    # Record orphaned order
    order_id = store.record_orphaned_order(
        broker_order_id="test_order_123",
        ticker="TEST",
        side="buy",
        shares=100,
        order_type="limit",
        status="new",
        limit_price=10.50,
        order_created_at=datetime.utcnow() - timedelta(seconds=10),
        strategy_name="test_strategy",
        reason="Found untracked order",
        paper=True,
    )

    assert order_id is not None

    # Mark as cancelled
    success = store.mark_as_cancelled("test_order_123", reason="Auto-cancelled")
    assert success is True

    # Verify duplicate detection
    duplicate_id = store.record_orphaned_order(
        broker_order_id="test_order_123",
        ticker="TEST",
        side="buy",
        shares=100,
        order_type="limit",
        status="new",
        limit_price=10.50,
    )
    assert duplicate_id == order_id  # Should return existing ID


def test_tracked_orders_not_flagged_as_orphaned(test_db_session):
    """Test that orders we're tracking are NOT flagged as orphaned."""
    # Setup
    config = StrategyConfig(
        buy_order_timeout_seconds=5,
    )

    mock_trader = Mock()
    mock_trader.get_positions.return_value = []

    # Create order that's being tracked (use recent timestamp so it wouldn't be cancelled anyway)
    tracked_order = Order(
        order_id="tracked_order",
        ticker="TEST",
        side="buy",
        shares=100,
        order_type="limit",
        status="new",
        created_at=datetime.utcnow() - timedelta(seconds=2),  # Recent order
        limit_price=10.50,
    )
    mock_trader.get_open_orders.return_value = [tracked_order]

    engine = StrategyEngine(
        config=config,
        trader=mock_trader,
        strategy_name="test_strategy",
        strategy_id="test_id",
        paper=True,
    )

    # Add to pending orders (simulate tracking)
    from src.strategy import PendingOrder
    engine.pending_orders["tracked_order"] = PendingOrder(
        order_id="tracked_order",
        ticker="TEST",
        side="buy",
        shares=100,
        limit_price=10.50,
        submitted_at=datetime.utcnow(),
        trade_id="test_trade",
    )

    # Execute
    with patch('src.strategy.logger') as mock_logger:
        engine._recover_pending_orders()

        # Verify NO warnings about untracked orders (because we're tracking it)
        assert not any("UNTRACKED ORDERS" in str(call) for call in mock_logger.warning.call_args_list)

