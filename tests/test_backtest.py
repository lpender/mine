import pytest
from datetime import datetime, timedelta
from src.models import Announcement, OHLCVBar, BacktestConfig, TradeResult
from src.backtest import run_single_backtest


def make_announcement(ticker: str = "TEST", timestamp: datetime = None) -> Announcement:
    """Helper to create test announcements."""
    if timestamp is None:
        timestamp = datetime(2025, 1, 15, 9, 30)
    return Announcement(
        ticker=ticker,
        timestamp=timestamp,
        price_threshold=1.0,
        headline="Test announcement",
        country="US",
    )


def make_bar(
    timestamp: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> OHLCVBar:
    """Helper to create test OHLCV bars."""
    return OHLCVBar(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class TestCumulativeVolumeEntry:
    """Tests for cumulative volume-based entry logic."""

    def test_entry_on_first_bar_when_volume_met(self):
        """Entry triggers on first bar when it alone meets volume threshold."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.15, low=1.05, close=1.12, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from open (1.0 -> 1.05)
            volume_threshold=75_000,  # First bar has 100k, meets threshold
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        assert result.entry_time == base_time, "Should enter on first bar"
        # Entry at 5% above open = 1.05
        assert result.entry_price == pytest.approx(1.05, rel=0.01)

    def test_entry_on_second_bar_cumulative_volume(self):
        """Entry waits for second bar when cumulative volume is needed."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # First bar: 50k volume, price triggers but volume insufficient
        # Second bar: 50k more volume, cumulative = 100k, now meets threshold
        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=50_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.20, low=1.05, close=1.15, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from open (1.0 -> 1.05)
            volume_threshold=75_000,  # Need cumulative 75k
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        assert result.entry_time == base_time + timedelta(minutes=1), "Should enter on second bar"

    def test_interpolated_entry_price_halfway(self):
        """
        Entry price is interpolated based on when volume threshold is met within the bar.

        Example:
        - First bar: 50k volume
        - Second bar: 50k volume
        - Threshold: 75k (met halfway through second bar)
        - Second bar: low=2.0, high=3.0
        - Entry price should be 2.5 (halfway between low and high)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=50_000),
            make_bar(base_time + timedelta(minutes=1), open_=2.0, high=3.0, low=2.0, close=2.8, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,  # Enter immediately (no price trigger needed)
            volume_threshold=75_000,  # Met at 50% through second bar
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        # 75k threshold, 50k from first bar, need 25k more from second bar (50k total)
        # That's 50% through the second bar's volume
        # Price should be 50% between low (2.0) and high (3.0) = 2.5
        assert result.entry_price == pytest.approx(2.5, rel=0.01), f"Expected ~2.5, got {result.entry_price}"

    def test_interpolated_entry_price_80_percent(self):
        """
        Entry price interpolated at 80% through the bar.

        - First bar: 50k volume
        - Second bar: 50k volume
        - Threshold: 90k (80% through second bar: 50k + 40k = 90k)
        - Second bar: low=2.0, high=3.0
        - Entry price should be 2.8 (80% between low and high)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=50_000),
            make_bar(base_time + timedelta(minutes=1), open_=2.0, high=3.0, low=2.0, close=2.8, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,  # Enter immediately
            volume_threshold=90_000,  # Met at 80% through second bar
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        # 90k threshold, 50k from first bar, need 40k more from second bar (50k total)
        # That's 80% through the second bar's volume
        # Price should be 80% between low (2.0) and high (3.0) = 2.8
        assert result.entry_price == pytest.approx(2.8, rel=0.01), f"Expected ~2.8, got {result.entry_price}"

    def test_no_entry_when_volume_never_met(self):
        """No entry when cumulative volume never reaches threshold."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=30_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.20, low=1.05, close=1.15, volume=30_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,
            volume_threshold=100_000,  # Never reached (only 60k total)
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert not result.entered, "Should not have entered"
        assert result.trigger_type == "no_entry"

    def test_entry_requires_both_price_and_volume(self):
        """Entry requires both price trigger AND cumulative volume threshold."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # First bar: price triggers (+10%) but volume insufficient
        # Second bar: volume now sufficient, but need to check price still valid
        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=30_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.12, low=1.05, close=1.10, volume=70_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from open (1.0 -> 1.05)
            volume_threshold=75_000,  # Met on second bar (30k + 70k = 100k)
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered when both conditions met"
        assert result.entry_time == base_time + timedelta(minutes=1), "Entry on second bar"

    def test_entry_price_combines_trigger_and_interpolation(self):
        """
        When both price trigger and volume are needed, entry price accounts for both.

        Scenario:
        - Price trigger: 5% above open (1.0 -> 1.05)
        - First bar hits the price trigger but doesn't have enough volume
        - Second bar has enough cumulative volume
        - Entry should be at the trigger price (1.05), not interpolated
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=30_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.15, low=1.06, close=1.12, volume=70_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Entry at 1.05
            volume_threshold=75_000,  # Met on second bar
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        # Entry price should be the trigger price (5% above open)
        # The volume interpolation affects WHEN we enter, not the price when price trigger is set
        assert result.entry_price == pytest.approx(1.05, rel=0.01)

    def test_zero_volume_threshold_enters_on_price_trigger_only(self):
        """With zero volume threshold, entry is based purely on price trigger."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=100),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,
            volume_threshold=0,  # No volume requirement
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == pytest.approx(1.05, rel=0.01)


class TestExitLogic:
    """Tests to ensure exit logic still works correctly."""

    def test_exit_happens_on_next_bar_not_entry_bar(self):
        """Exit cannot happen on the same bar as entry."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # Entry bar has high enough to hit take profit, but exit should be on next bar
        bars = [
            make_bar(base_time, open_=1.0, high=1.20, low=0.99, close=1.15, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.15, high=1.20, low=1.10, close=1.18, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Entry at 1.05
            volume_threshold=0,
            take_profit_pct=10.0,  # Exit at 1.155 (10% above 1.05)
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_time == base_time
        # Exit should be on second bar, not first
        assert result.exit_time == base_time + timedelta(minutes=1)

    def test_stop_loss_exit(self):
        """Stop loss triggers correctly."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.09, low=1.00, close=1.01, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Entry at 1.05
            volume_threshold=0,
            take_profit_pct=10.0,
            stop_loss_pct=3.0,  # Exit at 1.0185 (3% below 1.05)
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "stop_loss"
        assert result.exit_price == pytest.approx(1.05 * 0.97, rel=0.01)

    def test_timeout_exit(self):
        """Timeout exit uses last bar's close."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.10, low=1.05, close=1.07, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,
            volume_threshold=0,
            take_profit_pct=20.0,  # Won't hit
            stop_loss_pct=10.0,    # Won't hit
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "timeout"
        assert result.exit_price == 1.07  # Last bar's close
