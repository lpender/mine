"""Tests for unified safe_sell() function.

The safe_sell() function is the ONLY path that should be used to sell positions.
It verifies position exists at broker before attempting to sell, preventing
422 errors from trying to sell shares we don't have.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from src.live_trading_service import safe_sell, SafeSellResult
from src.trading.base import Position, Order


class TestSafeSell:
    """Test cases for safe_sell() unified sell function."""

    def test_successful_sell_position_exists(self):
        """Test normal sell when position exists with matching shares."""
        mock_trader = Mock()
        mock_trader.get_position.return_value = Position(
            ticker="AAPL",
            shares=100,
            avg_entry_price=150.0,
            market_value=15000.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
        )
        mock_trader.sell.return_value = Order(
            order_id="order123",
            ticker="AAPL",
            side="sell",
            shares=100,
            order_type="limit",
            status="filled",
            created_at=datetime.utcnow(),
            limit_price=150.0,
        )

        result = safe_sell(
            trader=mock_trader,
            ticker="AAPL",
            expected_shares=100,
            limit_price=150.0,
        )

        assert result.success is True
        assert result.ticker == "AAPL"
        assert result.shares_sold == 100
        assert result.order_status == "filled"
        assert result.was_ghost is False
        assert result.error is None
        mock_trader.sell.assert_called_once_with("AAPL", 100, limit_price=150.0)

    def test_share_mismatch_broker_has_fewer(self):
        """Test that when broker has fewer shares, we sell what's available."""
        mock_trader = Mock()
        # Broker says we have 75 shares, but we think we have 100
        mock_trader.get_position.return_value = Position(
            ticker="AAPL",
            shares=75,
            avg_entry_price=150.0,
            market_value=11250.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
        )
        mock_trader.sell.return_value = Order(
            order_id="order123",
            ticker="AAPL",
            side="sell",
            shares=75,
            order_type="limit",
            status="filled",
            created_at=datetime.utcnow(),
            limit_price=150.0,
        )

        result = safe_sell(
            trader=mock_trader,
            ticker="AAPL",
            expected_shares=100,  # We think we have 100
            limit_price=150.0,
        )

        assert result.success is True
        assert result.shares_sold == 75  # Should sell broker's count (fewer)
        mock_trader.sell.assert_called_once_with("AAPL", 75, limit_price=150.0)

    def test_share_mismatch_broker_has_more_multi_strategy(self):
        """Test that when broker has MORE shares (combined position), we sell only our portion."""
        mock_trader = Mock()
        # Broker has 150 shares total (from multiple strategies), we want to sell 100
        mock_trader.get_position.return_value = Position(
            ticker="AAPL",
            shares=150,  # Combined position from multiple strategies
            avg_entry_price=150.0,
            market_value=22500.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
        )
        mock_trader.sell.return_value = Order(
            order_id="order123",
            ticker="AAPL",
            side="sell",
            shares=100,
            order_type="limit",
            status="filled",
            created_at=datetime.utcnow(),
            limit_price=150.0,
        )

        result = safe_sell(
            trader=mock_trader,
            ticker="AAPL",
            expected_shares=100,  # Our strategy's portion
            limit_price=150.0,
        )

        assert result.success is True
        assert result.shares_sold == 100  # Should sell only our expected amount
        mock_trader.sell.assert_called_once_with("AAPL", 100, limit_price=150.0)

    def test_ghost_position_no_position_at_broker(self):
        """Test ghost position detection when broker has no position."""
        mock_trader = Mock()
        mock_trader.get_position.return_value = None  # No position at broker

        with patch("src.active_trade_store.get_active_trade_store") as mock_store_factory:
            mock_store = Mock()
            mock_store_factory.return_value = mock_store

            result = safe_sell(
                trader=mock_trader,
                ticker="AAPL",
                expected_shares=100,
                strategy_id="strategy123",
                cleanup_db=True,
            )

        assert result.success is False
        assert result.was_ghost is True
        assert result.error == "Position does not exist at broker"
        assert result.shares_sold == 0
        mock_trader.sell.assert_not_called()  # Should NOT attempt sell
        mock_store.delete_trade.assert_called_once()  # Should clean up DB

    def test_ghost_position_zero_shares_at_broker(self):
        """Test ghost position detection when broker has 0 shares."""
        mock_trader = Mock()
        mock_trader.get_position.return_value = Position(
            ticker="AAPL",
            shares=0,  # Zero shares
            avg_entry_price=150.0,
            market_value=0.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
        )

        with patch("src.active_trade_store.get_active_trade_store") as mock_store_factory:
            mock_store = Mock()
            mock_store_factory.return_value = mock_store

            result = safe_sell(
                trader=mock_trader,
                ticker="AAPL",
                expected_shares=100,
                trade_id="trade123",
                cleanup_db=True,
            )

        assert result.success is False
        assert result.was_ghost is True
        mock_trader.sell.assert_not_called()
        # Should clean up by trade_id
        mock_store.delete_trade.assert_called_once_with("trade123")

    def test_sell_failure_non_ghost(self):
        """Test sell failure that's NOT a ghost position (e.g., network error)."""
        mock_trader = Mock()
        mock_trader.get_position.return_value = Position(
            ticker="AAPL",
            shares=100,
            avg_entry_price=150.0,
            market_value=15000.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
        )
        mock_trader.sell.side_effect = Exception("Network timeout")

        result = safe_sell(
            trader=mock_trader,
            ticker="AAPL",
            expected_shares=100,
        )

        assert result.success is False
        assert result.was_ghost is False
        assert "Network timeout" in result.error
        assert result.shares_sold == 0

    def test_sell_failure_short_sale_error_triggers_cleanup(self):
        """Test that 'cannot be sold short' error triggers ghost cleanup."""
        mock_trader = Mock()
        mock_trader.get_position.return_value = Position(
            ticker="AAPL",
            shares=100,
            avg_entry_price=150.0,
            market_value=15000.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
        )
        # Race condition: position existed when checked but gone when sell submitted
        mock_trader.sell.side_effect = Exception("cannot be sold short (error code 42210000)")

        with patch("src.active_trade_store.get_active_trade_store") as mock_store_factory:
            mock_store = Mock()
            mock_store_factory.return_value = mock_store

            result = safe_sell(
                trader=mock_trader,
                ticker="AAPL",
                expected_shares=100,
                strategy_id="strategy123",
                cleanup_db=True,
            )

        assert result.success is False
        assert result.was_ghost is True  # Detected as ghost via error message
        mock_store.delete_trade.assert_called_once()  # Should clean up

    def test_cleanup_db_false_skips_deletion(self):
        """Test that cleanup_db=False prevents database deletion."""
        mock_trader = Mock()
        mock_trader.get_position.return_value = None  # Ghost position

        with patch("src.active_trade_store.get_active_trade_store") as mock_store_factory:
            mock_store = Mock()
            mock_store_factory.return_value = mock_store

            result = safe_sell(
                trader=mock_trader,
                ticker="AAPL",
                expected_shares=100,
                cleanup_db=False,  # Explicitly disable cleanup
            )

        assert result.was_ghost is True
        mock_store.delete_trade.assert_not_called()  # Should NOT delete

    def test_get_position_failure_continues_sell_attempt(self):
        """Test that get_position() failure doesn't block sell attempt."""
        mock_trader = Mock()
        mock_trader.get_position.side_effect = Exception("API error")
        mock_trader.sell.return_value = Order(
            order_id="order123",
            ticker="AAPL",
            side="sell",
            shares=100,
            order_type="limit",
            status="filled",
            created_at=datetime.utcnow(),
            limit_price=150.0,
        )

        # When get_position fails, broker_position is None, treated as ghost
        with patch("src.active_trade_store.get_active_trade_store") as mock_store_factory:
            mock_store = Mock()
            mock_store_factory.return_value = mock_store

            result = safe_sell(
                trader=mock_trader,
                ticker="AAPL",
                expected_shares=100,
                cleanup_db=False,  # Don't cleanup - just test behavior
            )

        # Position check failed, so treated as ghost (conservative approach)
        assert result.was_ghost is True
        assert result.success is False


class TestSafeSellResult:
    """Test the SafeSellResult dataclass."""

    def test_success_result(self):
        result = SafeSellResult(
            success=True,
            ticker="AAPL",
            shares_sold=100,
            order_status="filled",
        )
        assert result.success is True
        assert result.ticker == "AAPL"
        assert result.shares_sold == 100
        assert result.was_ghost is False
        assert result.error is None

    def test_ghost_result(self):
        result = SafeSellResult(
            success=False,
            ticker="AAPL",
            shares_sold=0,
            error="Position does not exist",
            was_ghost=True,
        )
        assert result.success is False
        assert result.was_ghost is True
        assert result.error == "Position does not exist"

    def test_failure_result(self):
        result = SafeSellResult(
            success=False,
            ticker="AAPL",
            shares_sold=0,
            error="Network error",
            was_ghost=False,
        )
        assert result.success is False
        assert result.was_ghost is False
        assert result.error == "Network error"
