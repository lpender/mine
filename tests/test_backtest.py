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


class TestIntraCandleVolumeEntry:
    """Tests for intra-candle volume-based entry logic."""

    def test_entry_when_single_bar_meets_volume(self):
        """Entry triggers when a single bar meets volume threshold."""
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
        # Entry interpolated: 75k/100k = 75% through bar
        # low=0.99, high=1.10: 0.99 + (1.10 - 0.99) * 0.75 = 1.0725
        assert result.entry_price == pytest.approx(1.0725, rel=0.01)

    def test_no_entry_when_bar_volume_insufficient(self):
        """No entry when individual bar volume doesn't meet threshold (not cumulative)."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # Each bar has 50k volume, but we need 75k per bar (not cumulative)
        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=50_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.20, low=1.05, close=1.15, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from open (1.0 -> 1.05)
            volume_threshold=75_000,  # Neither bar meets this individually
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert not result.entered, "Should NOT have entered - volume is per-bar, not cumulative"
        assert result.trigger_type == "no_entry"

    def test_entry_on_second_bar_when_it_meets_volume(self):
        """Entry happens on second bar when it individually meets volume."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # First bar: 50k volume (not enough)
        # Second bar: 100k volume (enough)
        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=50_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.20, low=1.05, close=1.15, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from open (1.0 -> 1.05)
            volume_threshold=75_000,  # Second bar meets this
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered on second bar"
        assert result.entry_time == base_time + timedelta(minutes=1), "Should enter on second bar"

    def test_interpolated_entry_price_halfway(self):
        """
        Entry price is interpolated based on when volume threshold is met within the bar.

        Example:
        - Bar: low=1.0, high=2.0, volume=100k
        - Threshold: 50k (met halfway through bar)
        - Entry price should be 1.5 (halfway between low and high)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=2.0, low=1.0, close=1.8, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,  # Enter immediately (no price trigger needed)
            volume_threshold=50_000,  # Met at 50% through the bar
            take_profit_pct=50.0,
            stop_loss_pct=30.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        # 50k threshold, bar has 100k volume
        # That's 50% through the bar's volume
        # Price should be 50% between low (1.0) and high (2.0) = 1.5
        assert result.entry_price == pytest.approx(1.5, rel=0.01), f"Expected ~1.5, got {result.entry_price}"

    def test_interpolated_entry_price_75_percent(self):
        """
        Entry price interpolated at 75% through the bar.

        - Bar: low=1.0, high=2.0, volume=100k
        - Threshold: 75k (75% through bar)
        - Entry price should be 1.75 (75% between low and high)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=2.0, low=1.0, close=1.8, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,  # Enter immediately
            volume_threshold=75_000,  # Met at 75% through bar
            take_profit_pct=50.0,
            stop_loss_pct=30.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        # 75k threshold, bar has 100k volume
        # That's 75% through the bar's volume
        # Price should be 75% between low (1.0) and high (2.0) = 1.75
        assert result.entry_price == pytest.approx(1.75, rel=0.01), f"Expected ~1.75, got {result.entry_price}"

    def test_interpolated_entry_price_25_percent(self):
        """
        Entry price interpolated at 25% through the bar.

        - Bar: low=1.0, high=2.0, volume=100k
        - Threshold: 25k (25% through bar)
        - Entry price should be 1.25 (25% between low and high)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=2.0, low=1.0, close=1.8, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,  # Enter immediately
            volume_threshold=25_000,  # Met at 25% through bar
            take_profit_pct=50.0,
            stop_loss_pct=30.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered"
        # 25k threshold, bar has 100k volume
        # That's 25% through the bar's volume
        # Price should be 25% between low (1.0) and high (2.0) = 1.25
        assert result.entry_price == pytest.approx(1.25, rel=0.01), f"Expected ~1.25, got {result.entry_price}"

    def test_entry_requires_both_price_and_volume(self):
        """Entry requires both price trigger AND volume threshold on same bar."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # First bar: price triggers (+10%) but volume insufficient
        # Second bar: volume sufficient AND price still above trigger
        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=0.99, close=1.08, volume=30_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.08, high=1.12, low=1.05, close=1.10, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from open (1.0 -> 1.05)
            volume_threshold=75_000,  # Only met on second bar
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered, "Should have entered when both conditions met"
        assert result.entry_time == base_time + timedelta(minutes=1), "Entry on second bar"

    def test_volume_interpolation_with_price_trigger(self):
        """
        Volume interpolation is always used when volume threshold is set,
        even when there's also a price trigger.

        Scenario:
        - Price trigger: 5% above open (1.0 -> 1.05) - determines IF we enter
        - Bar: low=1.0, high=2.0, volume=100k
        - Threshold: 50k (50% through bar) - determines WHERE we enter
        - Entry should be at 1.5 (interpolated), not 1.05 (trigger price)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=2.0, low=1.0, close=1.8, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Determines IF we can enter
            volume_threshold=50_000,  # Determines WHERE we enter (50% through bar)
            take_profit_pct=50.0,
            stop_loss_pct=30.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        # Entry price should be interpolated based on volume, not the trigger price
        # 50k / 100k = 50% through the bar: 1.0 + (2.0 - 1.0) * 0.5 = 1.5
        assert result.entry_price == pytest.approx(1.5, rel=0.01)

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

    def test_entry_uses_later_of_price_or_volume(self):
        """
        Entry price is the LATER of price trigger or volume interpolation.

        Scenario:
        - Price trigger: 5% above open (1.0 -> 1.05)
        - Bar: low=1.0, high=1.10, volume=100k
        - Threshold: 10k (10% through bar) -> interpolated price = 1.01
        - Since trigger price (1.05) > volume price (1.01), entry at 1.05
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.10, low=1.0, close=1.08, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Trigger at 1.05
            volume_threshold=10_000,  # 10% through bar = 1.01
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        # Volume interpolation: 10k/100k = 10% -> 1.0 + (1.10 - 1.0) * 0.1 = 1.01
        # Price trigger: 1.05
        # Entry at max(1.01, 1.05) = 1.05
        assert result.entry_price == pytest.approx(1.05, rel=0.01)

    def test_no_entry_when_price_never_triggers(self):
        """No entry when price never reaches trigger level, even with high volume."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=1.0, high=1.03, low=0.99, close=1.02, volume=1_000_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% but bar only reaches +3%
            volume_threshold=50_000,
            take_profit_pct=10.0,
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert not result.entered
        assert result.trigger_type == "no_entry"


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
