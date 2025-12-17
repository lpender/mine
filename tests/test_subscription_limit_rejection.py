"""Test that alerts are rejected when at websocket subscription limit."""

import pytest
from datetime import datetime
from unittest.mock import Mock

from src.strategy import StrategyEngine, StrategyConfig, Announcement


class TestSubscriptionLimitRejection:
    """Test that alerts are properly rejected when at subscription limit."""

    def create_engine(self):
        """Create a test engine with mocked trader."""
        config = StrategyConfig(
            channels=["test-channel"],
            directions=["up"],  # Match the announcement direction
            sessions=["market"],  # Match the market session returned by get_market_session
            price_min=0.5,
            price_max=200,
            consec_green_candles=1,
            min_candle_volume=1000,
            take_profit_pct=5,
            stop_loss_pct=3,
            trailing_stop_pct=0,
            timeout_minutes=60,
        )

        trader = Mock()
        trader.get_positions.return_value = []
        trader.get_open_orders.return_value = []
        trader.is_tradeable.return_value = (True, "tradeable")

        engine = StrategyEngine(
            strategy_id="test-strategy",
            config=config,
            trader=trader,
        )

        return engine

    def create_announcement(self, ticker="TEST"):
        """Create a test announcement."""
        return Announcement(
            ticker=ticker,
            timestamp=datetime(2025, 12, 12, 15, 0, 0),  # Market hours
            price_threshold=5.0,
            headline="Test announcement",
            country="US",
            channel="test-channel",
            direction="up",
        )

    def test_alert_rejected_when_subscription_fails(self):
        """Alert should be rejected if subscription callback returns False."""
        engine = self.create_engine()

        # Mock callback that returns False (subscription failed)
        engine.on_subscribe = Mock(return_value=False)

        ann = self.create_announcement()

        # Alert should be rejected
        result = engine.on_alert(ann)

        assert result is False
        # Check using helper method (pending_entries keyed by trade_id, not ticker)
        assert not engine._has_pending_or_trade("TEST")
        engine.on_subscribe.assert_called_once_with("TEST")

    def test_alert_accepted_when_subscription_succeeds(self):
        """Alert should be accepted if subscription callback returns True."""
        engine = self.create_engine()

        # Mock callback that returns True (subscription succeeded)
        engine.on_subscribe = Mock(return_value=True)

        ann = self.create_announcement()

        # Alert should be accepted
        result = engine.on_alert(ann)

        assert result is True
        # Check using helper method (pending_entries keyed by trade_id, not ticker)
        assert engine._has_pending_or_trade("TEST")
        engine.on_subscribe.assert_called_once_with("TEST")

    def test_alert_accepted_when_no_callback(self):
        """Alert should be accepted if no subscription callback is set."""
        engine = self.create_engine()

        # No callback set (None)
        engine.on_subscribe = None

        ann = self.create_announcement()

        # Alert should still be accepted (for backward compatibility)
        result = engine.on_alert(ann)

        assert result is True
        # Check using helper method (pending_entries keyed by trade_id, not ticker)
        assert engine._has_pending_or_trade("TEST")

    def test_multiple_alerts_with_subscription_limit(self):
        """Simulate hitting subscription limit after accepting some alerts."""
        engine = self.create_engine()

        # Mock callback that succeeds for first 2, then fails
        subscription_attempts = []

        def mock_subscribe(ticker):
            subscription_attempts.append(ticker)
            # First 2 succeed, rest fail (simulating limit of 2)
            return len(subscription_attempts) <= 2

        engine.on_subscribe = mock_subscribe

        # Try to add 4 tickers
        tickers = ["TICK1", "TICK2", "TICK3", "TICK4"]
        results = []

        for ticker in tickers:
            ann = self.create_announcement(ticker)
            result = engine.on_alert(ann)
            results.append((ticker, result))

        # First 2 should succeed, last 2 should fail
        assert results[0] == ("TICK1", True)
        assert results[1] == ("TICK2", True)
        assert results[2] == ("TICK3", False)
        assert results[3] == ("TICK4", False)

        # Only first 2 should be in pending entries (check via helper, keyed by trade_id)
        assert engine._has_pending_or_trade("TICK1")
        assert engine._has_pending_or_trade("TICK2")
        assert not engine._has_pending_or_trade("TICK3")
        assert not engine._has_pending_or_trade("TICK4")

        # All 4 subscription attempts should have been made
        assert len(subscription_attempts) == 4

