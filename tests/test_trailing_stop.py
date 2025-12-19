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
        # Entry at $10 (close of first bar)
        # Bar 1: Spikes to $11 (high), trailing stop moves to $10.89 (11 * 0.99)
        # Bar 2: Drops to $10.50, should trigger trailing stop at $10.89
        bars = [
            OHLCVBar(
                timestamp=base_time,
                open=10.0,
                high=11.0,
                low=10.0,
                close=10.0,
                volume=10000,
            ),
            OHLCVBar(
                timestamp=base_time + timedelta(minutes=1),
                open=10.95,
                high=10.95,
                low=10.50,
                close=10.60,
                volume=5000,
            ),
        ]

        config = BacktestConfig(
            take_profit_pct=20.0,  # TP at $12 (won't hit)
            stop_loss_pct=10.0,    # SL at $9 (won't hit)
            trailing_stop_pct=1.0,  # Trailing stop at 1%
            window_minutes=30,
            entry_at_candle_close=True,  # Enter at first bar's close
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        assert result.entry_price == 10.0, "Entry should be at first bar close"

        # Trailing stop should trigger
        assert result.trigger_type == "trailing_stop", \
            f"Should trigger trailing_stop, got {result.trigger_type}"

        # Exit price should be trailing stop level: $11 * 0.99 = $10.89
        expected_trailing_stop = 11.0 * 0.99
        assert result.exit_price == pytest.approx(expected_trailing_stop, rel=0.01), \
            f"Exit should be at trailing stop ${expected_trailing_stop:.2f}, got ${result.exit_price:.2f}"

    def test_trailing_stop_checked_before_fixed_stop_loss(self):
        """When both trailing and fixed SL are triggered, trailing stop takes precedence."""
        # Use datetime with second=0 for entry_by_message_second mode
        base_time = datetime(2025, 1, 1, 10, 0, 0)

        # Announcement at 09:59:00 (second=0) so ann_minute_end=10:00:00 and bars at 10:00 are valid
        # The second=0 is used by entry_by_message_second to determine entry price
        announcement = Announcement(
            ticker="TEST",
            timestamp=datetime(2025, 1, 1, 9, 59, 0),
            price_threshold=0.50,
            headline="Test announcement",
            country="US",
        )

        # Scenario similar to DWTX:
        # Entry at $9.32 (first bar low, via entry_by_message_second at second=0)
        # First bar spikes to $10.81, then second bar drops to $7.70
        # Trailing stop (1%): $10.81 * 0.99 = $10.70
        # Fixed SL (13.5%): $9.32 * 0.865 = $8.06
        # Both are hit on second bar, but trailing stop should trigger first
        bars = [
            OHLCVBar(
                timestamp=base_time,
                open=9.32,
                high=10.81,
                low=9.32,
                close=10.50,
                volume=10000,
            ),
            OHLCVBar(
                timestamp=base_time + timedelta(minutes=1),
                open=10.45,
                high=10.50,
                low=7.70,
                close=8.20,
                volume=5000,
            ),
        ]

        config = BacktestConfig(
            take_profit_pct=50.0,  # TP at $13.98 (won't hit)
            stop_loss_pct=13.5,
            trailing_stop_pct=1.0,
            window_minutes=30,
            entry_by_message_second=True,  # Enter within first bar based on message second
            entry_trigger_pct=0.0,  # No trigger requirement
            volume_threshold=0,  # No volume requirement
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        assert result.entry_price == 9.32, "Entry should be at first bar low (second=0)"
        assert result.trigger_type == "trailing_stop", \
            f"Trailing stop should take precedence over fixed SL, got {result.trigger_type}"

        # Exit should be at trailing stop level: $10.81 * 0.99 = $10.70
        expected_trailing_stop = 10.81 * 0.99
        assert result.exit_price == pytest.approx(expected_trailing_stop, rel=0.01), \
            f"Exit should be at trailing stop ${expected_trailing_stop:.2f}, got ${result.exit_price:.2f}"

    def test_trailing_stop_does_not_move_down(self):
        """Trailing stop should only move up with price, never down."""
        base_time = datetime(2025, 1, 1, 10, 0)

        announcement = Announcement(
            ticker="TEST",
            timestamp=base_time,
            price_threshold=0.50,
            headline="Test announcement",
            country="US",
        )

        # Price goes up to $11, then down to $10.95, then up to $10.98
        # Trailing stop set at $10.89 (when price hit $11) should NOT move down
        # Price stays above $10.89, so should timeout
        bars = [
            OHLCVBar(timestamp=base_time, open=10.0, high=10.0, low=10.0, close=10.0, volume=1000),
            OHLCVBar(timestamp=base_time + timedelta(minutes=1), open=10.0, high=11.0, low=10.0, close=11.0, volume=1000),
            OHLCVBar(timestamp=base_time + timedelta(minutes=2), open=11.0, high=11.0, low=10.90, close=10.95, volume=1000),
            OHLCVBar(timestamp=base_time + timedelta(minutes=3), open=10.95, high=10.98, low=10.90, close=10.97, volume=1000),
        ]

        config = BacktestConfig(
            take_profit_pct=20.0,
            stop_loss_pct=20.0,
            trailing_stop_pct=1.0,
            window_minutes=30,
            entry_at_candle_close=True,  # Enter at first bar's close
        )

        result = run_single_backtest(announcement, bars, config)

        # Should NOT trigger trailing stop (lowest point is $10.90, which is above $10.89)
        # Should timeout instead
        assert result.trigger_type == "timeout", \
            f"Should timeout since price stayed above trailing stop, got {result.trigger_type}"
