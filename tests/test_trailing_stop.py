"""Test trailing stop behavior in backtest."""

import pytest
from datetime import datetime, timedelta
from src.models import Announcement, OHLCVBar, BacktestConfig
from src.backtest import run_single_backtest


class TestTrailingStop:
    """Tests for trailing stop functionality."""

    def test_trailing_stop_updates_with_price_increases(self):
        """Trailing stop should update as price increases, then trigger on pullback."""
        base_time = datetime(2025, 1, 1, 10, 0)

        # Announcement at 09:59:30 so ann_minute_end=10:00:00 and bars at 10:00 are valid
        announcement = Announcement(
            ticker="TEST",
            timestamp=base_time - timedelta(seconds=30),
            price_threshold=0.50,
            headline="Test announcement",
            country="US",
        )

        # Scenario: Price spikes up then pulls back
        # Entry at $10 (open of first bar)
        # Bar 1: Stays above entry, spikes to $11 (high), trailing stop moves to $10.89 (11 * 0.99)
        # Bar 2: Drops to $10.50, should trigger trailing stop at $10.89
        bars = [
            OHLCVBar(
                timestamp=base_time,
                open=10.0,
                high=11.0,
                low=10.0,  # Low at open (doesn't trigger trailing stop)
                close=10.5,
                volume=10000,
            ),
            OHLCVBar(
                timestamp=base_time + timedelta(minutes=1),
                open=10.45,
                high=10.95,
                low=10.50,  # Above $10.89, doesn't trigger trailing
                close=10.60,
                volume=5000,
            ),
            OHLCVBar(
                timestamp=base_time + timedelta(minutes=2),
                open=10.60,
                high=10.70,
                low=10.80,  # Below $10.89, triggers trailing stop
                close=10.85,
                volume=5000,
            ),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,  # Enter immediately at bar open
            take_profit_pct=20.0,  # TP at $12 (won't hit)
            stop_loss_pct=10.0,    # SL at $9 (won't hit)
            trailing_stop_pct=1.0,  # Trailing stop at 1%
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        assert result.entry_price == 10.0, "Entry should be at first bar open"

        # Trailing stop should trigger
        assert result.trigger_type == "trailing_stop", \
            f"Should trigger trailing_stop, got {result.trigger_type}"

        # Exit price should be trailing stop level: $11 * 0.99 = $10.89
        expected_trailing_stop = 11.0 * 0.99
        assert result.exit_price == pytest.approx(expected_trailing_stop, rel=0.01), \
            f"Exit should be at trailing stop ${expected_trailing_stop:.2f}, got ${result.exit_price:.2f}"

    def test_trailing_stop_checked_before_fixed_stop_loss(self):
        """When both trailing and fixed SL are triggered, trailing stop takes precedence."""
        base_time = datetime(2025, 1, 1, 10, 0, 0)

        # Announcement at 09:59:30 so bars at 10:00 are valid
        announcement = Announcement(
            ticker="TEST",
            timestamp=datetime(2025, 1, 1, 9, 59, 30),
            price_threshold=0.50,
            headline="Test announcement",
            country="US",
        )

        # Scenario:
        # Entry at $10 (first bar open)
        # First bar spikes to $11, trailing stop moves to $10.89 (11 * 0.99)
        # Second bar drops through both trailing stop and fixed SL
        # Trailing stop ($10.89) should trigger before fixed SL ($8.65)
        bars = [
            OHLCVBar(
                timestamp=base_time,
                open=10.0,
                high=11.0,
                low=10.0,  # Low at open (doesn't trigger trailing stop)
                close=10.50,
                volume=10000,
            ),
            OHLCVBar(
                timestamp=base_time + timedelta(minutes=1),
                open=10.45,
                high=10.50,
                low=7.70,  # Drops through both stops
                close=8.20,
                volume=5000,
            ),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,  # Enter immediately at bar open
            take_profit_pct=50.0,  # TP won't hit
            stop_loss_pct=13.5,    # Fixed SL at $8.65
            trailing_stop_pct=1.0,  # Trailing stop 1% from high
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        assert result.entry_price == 10.0, "Entry should be at first bar open"
        assert result.trigger_type == "trailing_stop", \
            f"Trailing stop should take precedence over fixed SL, got {result.trigger_type}"

        # Exit should be at trailing stop level: $11 * 0.99 = $10.89
        expected_trailing_stop = 11.0 * 0.99
        assert result.exit_price == pytest.approx(expected_trailing_stop, rel=0.01), \
            f"Exit should be at trailing stop ${expected_trailing_stop:.2f}, got ${result.exit_price:.2f}"

    def test_trailing_stop_does_not_move_down(self):
        """Trailing stop should only move up with price, never down."""
        base_time = datetime(2025, 1, 1, 10, 0)

        announcement = Announcement(
            ticker="TEST",
            timestamp=base_time - timedelta(seconds=30),
            price_threshold=0.50,
            headline="Test announcement",
            country="US",
        )

        # Price goes up to $11, then fluctuates but stays above trailing stop ($10.89)
        # Trailing stop set at $10.89 (when price hit $11) should NOT move down
        # Price stays above $10.89 in all OHLC values, so should timeout
        # Note: In 4-stage model, close is checked after high, so close must be above trailing stop too
        bars = [
            OHLCVBar(timestamp=base_time, open=10.0, high=11.0, low=10.0, close=10.95, volume=1000),  # Peak at $11, close above $10.89
            OHLCVBar(timestamp=base_time + timedelta(minutes=1), open=10.95, high=10.98, low=10.92, close=10.95, volume=1000),  # All above $10.89
            OHLCVBar(timestamp=base_time + timedelta(minutes=2), open=10.95, high=10.98, low=10.92, close=10.97, volume=1000),  # All above $10.89
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,  # Enter immediately at bar open
            take_profit_pct=20.0,
            stop_loss_pct=20.0,
            trailing_stop_pct=1.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        # Should NOT trigger trailing stop (lowest point is $10.90, which is above $10.89)
        # Should timeout instead
        assert result.trigger_type == "timeout", \
            f"Should timeout since price stayed above trailing stop, got {result.trigger_type}"
