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


class TestEntryByMessageSecond:
    """Tests for entering within first candle based on announcement second."""

    def test_entry_1st_second_is_1_over_60th_through_candle(self):
        base_time = datetime(2025, 1, 15, 9, 30, 1)  # second=1
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(datetime(2025, 1, 15, 9, 30), open_=1.5, high=2.0, low=1.0, close=1.6, volume=100_000),
            make_bar(datetime(2025, 1, 15, 9, 31), open_=1.6, high=1.7, low=1.5, close=1.6, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,
            volume_threshold=0,
            take_profit_pct=50.0,
            stop_loss_pct=50.0,
            window_minutes=30,
            entry_at_candle_close=False,
            entry_by_message_second=True,
        )

        result = run_single_backtest(announcement, bars, config)
        assert result.entered
        # low=1, high=2, sec=1 => 1 + (2-1)*(1/60) = 1.016666...
        assert result.entry_price == pytest.approx(1.0 + 1.0 * (1 / 60.0), rel=1e-6)

    def test_entry_30th_second_is_halfway_through_candle(self):
        base_time = datetime(2025, 1, 15, 9, 30, 30)  # second=30
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(datetime(2025, 1, 15, 9, 30), open_=1.2, high=2.0, low=1.0, close=1.8, volume=100_000),
            make_bar(datetime(2025, 1, 15, 9, 31), open_=1.8, high=1.9, low=1.7, close=1.8, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=0.0,
            volume_threshold=0,
            take_profit_pct=50.0,
            stop_loss_pct=50.0,
            window_minutes=30,
            entry_at_candle_close=False,
            entry_by_message_second=True,
        )

        result = run_single_backtest(announcement, bars, config)
        assert result.entered
        assert result.entry_price == pytest.approx(1.5, rel=1e-6)


class TestExitLogic:
    """Tests to ensure exit logic still works correctly."""

    def test_exit_can_happen_on_entry_bar_with_price_trigger(self):
        """With price trigger entry (not candle close), exit CAN happen on entry bar."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # Entry bar has low that breaches stop loss
        bars = [
            make_bar(base_time, open_=1.0, high=1.20, low=0.99, close=1.15, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=1.15, high=1.20, low=1.10, close=1.18, volume=50_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Entry at 1.05
            volume_threshold=0,
            take_profit_pct=10.0,  # Exit at 1.155
            stop_loss_pct=3.0,  # Exit at 1.0185
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_time == base_time
        # With price trigger entry, exit CAN happen on same bar
        # Stop loss at 1.05 * 0.97 = 1.0185, low of 0.99 breaches it
        assert result.trigger_type == "stop_loss"
        assert result.exit_time == base_time  # Same bar

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


class TestFourStageIntraCandleModel:
    """
    Tests for the 4-stage intra-candle price path model.

    The model assumes price moves within each candle as:
    1. Stage 1: Opens at open
    2. Stage 2: Drops to low (only if low < open)
    3. Stage 3: Rises to high (only if high > close)
    4. Stage 4: Settles at close
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 2 Tests: Stop loss and trailing stop at the low
    # ─────────────────────────────────────────────────────────────────────────

    def test_stop_loss_triggers_at_low_not_close(self):
        """
        Stop loss should trigger at the low (stage 2), exiting at stop_loss_price.

        Scenario: Entry at $10, stop loss at 3% ($9.70)
        Bar has low of $9.50 (below stop) but closes at $9.80 (above stop)
        Should exit at $9.70 (stop price), not $9.80 (close)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            # Entry bar - enter at close of $10
            make_bar(base_time, open_=9.90, high=10.10, low=9.85, close=10.00, volume=100_000),
            # Exit bar - low breaches stop, but close is above stop
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.05, low=9.50, close=9.80, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=3.0,  # Stop at 10.00 * 0.97 = 9.70
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "stop_loss"
        assert result.exit_price == pytest.approx(9.70, rel=0.01)  # Stop price, not close

    def test_stop_loss_priority_over_trailing_stop_at_low(self):
        """
        Fixed stop loss should be checked before trailing stop at stage 2.

        If both would trigger at the low, stop loss wins.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Low of 9.50 breaches both 3% stop loss ($9.70) and 1% trailing stop ($9.90)
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.05, low=9.50, close=9.80, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=3.0,  # Stop at $9.70
            trailing_stop_pct=1.0,  # Trailing at $9.90 from entry
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "stop_loss"  # Not trailing_stop
        assert result.exit_price == pytest.approx(9.70, rel=0.01)

    def test_trailing_stop_triggers_at_low_when_stop_loss_not_breached(self):
        """
        Trailing stop triggers at low (stage 2) when stop loss isn't breached.

        Entry at $10, trailing stop at 5% = $9.50
        Bar low is $9.40 (breaches trailing), stop loss at 10% = $9.00 (not breached)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.05, low=9.40, close=9.80, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=10.0,  # Stop at $9.00 (not breached)
            trailing_stop_pct=5.0,  # Trailing at $9.50 (breached by low of $9.40)
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        assert result.exit_price == pytest.approx(9.50, rel=0.01)

    def test_stage_2_skipped_when_low_equals_open(self):
        """
        Stage 2 (drop to low) is skipped when low >= open.

        For a green candle where price only went up, no stop checks at low.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Green candle: open=10, low=10 (equal), high=10.50, close=10.40
            # Price never dropped below open, so stage 2 skipped
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.50, low=10.00, close=10.40, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=3.0,  # Would trigger at $9.70 if low was checked
            trailing_stop_pct=1.0,  # Would trigger at $9.90 if low was checked
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        # Neither stop should trigger since price never dropped below open
        assert result.trigger_type == "timeout"

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 3 Tests: Take profit at high, trailing stop at close after peak
    # ─────────────────────────────────────────────────────────────────────────

    def test_take_profit_triggers_at_high(self):
        """Take profit triggers when high reaches target."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # High of $11.50 exceeds 10% take profit ($11.00)
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=11.50, low=9.90, close=10.50, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=10.0,  # TP at $11.00
            stop_loss_pct=3.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "take_profit"
        assert result.exit_price == pytest.approx(11.00, rel=0.01)

    def test_trailing_stop_after_peak_triggers_at_close(self):
        """
        Trailing stop triggers at close (stage 4) after price peaked.

        Entry at $10, bar goes: open $10 -> low $9.90 -> high $11 -> close $10.40
        Trailing stop at 10% from $11 high = $9.90
        Close of $10.40 is above $9.90, so no trigger.

        But with 5% trailing: $11 * 0.95 = $10.45
        Close of $10.40 < $10.45, so trailing triggers
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Price peaks at $11, closes at $10.40 (dropped 5.45% from peak)
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=11.00, low=9.90, close=10.40, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,  # Won't hit
            stop_loss_pct=15.0,  # Won't hit
            trailing_stop_pct=5.0,  # Triggers: $11 * 0.95 = $10.45, close $10.40 < $10.45
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        assert result.exit_price == pytest.approx(10.45, rel=0.01)

    def test_trailing_stop_at_close_uses_updated_highest(self):
        """
        Trailing stop at close (stage 4) uses the bar's high as highest_since_entry.

        Previous highest was $10, current bar high is $12.
        Trailing stop at 10% from $12 = $10.80
        Close of $10.50 < $10.80, triggers.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=12.00, low=9.95, close=10.50, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=30.0,
            stop_loss_pct=15.0,
            trailing_stop_pct=10.0,  # $12 * 0.90 = $10.80
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        # Exit at trailing stop price, not close
        assert result.exit_price == pytest.approx(10.80, rel=0.01)

    def test_stage_3_trailing_stop_skipped_when_high_equals_close(self):
        """
        Stage 3 trailing stop (at close) skipped when high == close.

        If bar closes at its high, price didn't "come back down", so no trailing check.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # High == close, so price didn't drop from peak
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.50, low=9.95, close=10.50, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=15.0,
            trailing_stop_pct=1.0,  # Would trigger if close was checked vs high
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        # Trailing stop shouldn't trigger because high == close
        assert result.trigger_type == "timeout"

    # ─────────────────────────────────────────────────────────────────────────
    # Stage 4 Tests: Fixed stop loss at close
    # ─────────────────────────────────────────────────────────────────────────

    def test_stop_loss_at_close_when_low_didnt_breach(self):
        """
        Stop loss triggers at close when low didn't breach but close does.

        Entry at $10, stop at 3% = $9.70
        Bar: open=$10, low=$9.75 (above stop), close=$9.60 (below stop)
        Since low >= open (stage 2 skipped), stop checked at close.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Low ($9.75) is above open ($10) ... wait, that's not possible
            # Let's use a bar where low doesn't breach stop but close does
            # Actually, if low < open, stage 2 runs. So for stage 4 to matter:
            # Either low >= open (stage 2 skipped), or low > stop_loss_price
            make_bar(base_time + timedelta(minutes=1), open_=9.75, high=9.80, low=9.75, close=9.60, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=3.0,  # Stop at $9.70
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "stop_loss"
        # Exit at stop_loss_price, not close
        assert result.exit_price == pytest.approx(9.70, rel=0.01)

    # ─────────────────────────────────────────────────────────────────────────
    # Entry Mode Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_entry_at_candle_close_exits_start_next_bar(self):
        """
        When entering at candle close, exits can't happen on the same candle.

        Even if bar 0 would trigger an exit, we enter at its close,
        so exits are only checked starting from bar 1.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            # Bar 0: Entry at close ($10). Low would hit 5% trailing but we're not in yet.
            make_bar(base_time, open_=11.00, high=11.00, low=9.00, close=10.00, volume=100_000),
            # Bar 1: Price stable, no stops hit
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.50, low=9.80, close=10.20, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=15.0,
            trailing_stop_pct=5.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.00  # Close of bar 0
        # Should timeout, not trigger trailing stop from bar 0's low
        assert result.trigger_type == "timeout"

    def test_consecutive_green_candles_entry(self):
        """Entry after X consecutive green candles enters at OPEN of next bar."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            # Green candle 1
            make_bar(base_time, open_=10.00, high=10.50, low=9.90, close=10.30, volume=100_000),
            # Green candle 2 (signal bar)
            make_bar(base_time + timedelta(minutes=1), open_=10.30, high=10.80, low=10.20, close=10.60, volume=100_000),
            # Entry bar - enter at OPEN
            make_bar(base_time + timedelta(minutes=2), open_=10.60, high=11.00, low=10.40, close=10.80, volume=50_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=2,
            min_candle_volume=50_000,
            take_profit_pct=20.0,
            stop_loss_pct=10.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.60  # Open of bar after signal
        assert result.entry_time == base_time + timedelta(minutes=2)

    def test_consecutive_candles_resets_on_red(self):
        """Consecutive green candle count resets on a red candle."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            # Green candle 1
            make_bar(base_time, open_=10.00, high=10.50, low=9.90, close=10.30, volume=100_000),
            # Red candle - resets count
            make_bar(base_time + timedelta(minutes=1), open_=10.30, high=10.40, low=10.00, close=10.10, volume=100_000),
            # Green candle 1 (after reset)
            make_bar(base_time + timedelta(minutes=2), open_=10.10, high=10.60, low=10.00, close=10.50, volume=100_000),
            # Green candle 2 (signal bar)
            make_bar(base_time + timedelta(minutes=3), open_=10.50, high=11.00, low=10.40, close=10.80, volume=100_000),
            # Entry bar
            make_bar(base_time + timedelta(minutes=4), open_=10.80, high=11.20, low=10.60, close=11.00, volume=50_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=2,
            min_candle_volume=50_000,
            take_profit_pct=20.0,
            stop_loss_pct=10.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.80  # Open of bar 4 (after 2 consecutive greens)
        assert result.entry_time == base_time + timedelta(minutes=4)


class TestTrailingStopEdgeCases:
    """Edge cases for trailing stop behavior."""

    def test_trailing_stop_tracks_across_multiple_bars(self):
        """Trailing stop tracks highest across multiple bars."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Bar 1: High of $11
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=11.00, low=9.95, close=10.50, volume=50_000),
            # Bar 2: High of $12 (new highest)
            make_bar(base_time + timedelta(minutes=2), open_=10.50, high=12.00, low=10.40, close=11.50, volume=50_000),
            # Bar 3: Drops, trailing stop from $12 should trigger
            make_bar(base_time + timedelta(minutes=3), open_=11.50, high=11.60, low=10.50, close=10.60, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=30.0,
            stop_loss_pct=20.0,
            trailing_stop_pct=10.0,  # $12 * 0.90 = $10.80
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        # Trailing from highest ($12) at 10% = $10.80
        assert result.exit_price == pytest.approx(10.80, rel=0.01)

    def test_trailing_stop_at_low_uses_previous_bars_highest(self):
        """
        At stage 2 (low), trailing stop uses highest from PREVIOUS bars.

        Entry at $10, bar 1 peaks at $11 (highest = $11)
        Bar 2: open=$11, drops to low=$9.80
        Trailing at 10%: $11 * 0.90 = $9.90
        Low $9.80 < $9.90, triggers at stage 2
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Bar 1: Peaks at $11
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=11.00, low=9.95, close=10.80, volume=50_000),
            # Bar 2: Low breaches trailing stop from $11
            make_bar(base_time + timedelta(minutes=2), open_=11.00, high=11.10, low=9.80, close=10.50, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=30.0,
            stop_loss_pct=20.0,
            trailing_stop_pct=10.0,  # From $11: $9.90
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        assert result.exit_price == pytest.approx(9.90, rel=0.01)

    def test_no_trailing_stop_when_disabled(self):
        """With trailing_stop_pct=0, no trailing stop triggers."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=15.00, low=9.00, close=10.00, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=60.0,
            stop_loss_pct=20.0,
            trailing_stop_pct=0.0,  # Disabled
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        # Despite huge swing from $15 to $9, trailing stop disabled
        assert result.trigger_type == "timeout"


class TestRealWorldScenarios:
    """Tests based on real scenarios that were fixed."""

    def test_rime_scenario_stop_loss_at_low(self):
        """
        RIME scenario: Stop loss should trigger at low, not get bypassed.

        Entry at $2.32, stop loss at 3% = $2.25
        Bar drops to low of $2.20, closes at $2.22
        Should exit at $2.25 (stop loss), not $2.22 (close)
        """
        base_time = datetime(2025, 11, 19, 7, 30)
        announcement = make_announcement(ticker="RIME", timestamp=base_time)

        bars = [
            # Entry bar - close at $2.32
            make_bar(base_time, open_=2.20, high=2.45, low=2.15, close=2.32, volume=500_000),
            # Exit bar - low breaches stop loss
            make_bar(base_time + timedelta(minutes=1), open_=2.30, high=2.32, low=2.20, close=2.22, volume=200_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=30.0,
            stop_loss_pct=3.0,  # $2.32 * 0.97 = $2.25
            trailing_stop_pct=0.0,
            window_minutes=120,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == pytest.approx(2.32, rel=0.01)
        assert result.trigger_type == "stop_loss"
        assert result.exit_price == pytest.approx(2.25, rel=0.01)  # Stop price, not close

    def test_entry_at_close_cannot_exit_same_bar(self):
        """
        Entry at candle close means we can't check exits on that candle.

        If first bar has a huge wick down that would hit stops,
        we don't care because we enter at the END of that bar.
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            # Bar 0: Huge wick down to $5, but we enter at close of $10
            make_bar(base_time, open_=12.00, high=12.00, low=5.00, close=10.00, volume=500_000),
            # Bar 1: Normal bar, no stops hit
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.50, low=9.80, close=10.20, volume=200_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=5.0,  # Would trigger at $9.50 if bar 0 was checked
            trailing_stop_pct=10.0,
            window_minutes=120,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.00
        # Bar 0's low of $5 should NOT trigger anything because we entered at close
        assert result.trigger_type == "timeout"


class TestStopLossEdgeCases:
    """Tests for stop loss edge cases found in production."""

    def test_sl_from_open_above_entry_falls_back_to_entry_based_stop(self):
        """
        MCRP scenario: sl_from_open results in stop above entry price.

        First candle: open=$1.02, close=$0.976 (massive drop)
        With sl_from_open=True and 1% stop: $1.02 * 0.99 = $1.0098
        But entry is at $0.976 (close), which is BELOW the stop price!

        A stop above entry makes no sense, so it should fall back to
        entry-based stop: $0.976 * 0.99 = $0.9662
        """
        base_time = datetime(2025, 12, 3, 13, 5)
        announcement = make_announcement(ticker="MCRP", timestamp=base_time)

        bars = [
            # Entry bar: big drop from open to close
            make_bar(base_time, open_=1.02, high=1.03, low=0.957, close=0.976, volume=255_000),
            # Next bar: gaps down, triggers stop
            make_bar(base_time + timedelta(minutes=1), open_=0.962, high=0.962, low=0.895, close=0.899, volume=142_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=10.0,
            stop_loss_pct=1.0,
            stop_loss_from_open=True,  # Would give $1.0098, above entry!
            window_minutes=60,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == pytest.approx(0.976, rel=0.01)
        # Stop should be entry-based: 0.976 * 0.99 = 0.9662 (NOT 1.0098)
        # Since bar 1 gaps below this, exit at bar.open
        assert result.trigger_type == "stop_loss"
        assert result.exit_price == pytest.approx(0.962, rel=0.01)  # Gap fill at bar.open
        assert result.exit_price < result.entry_price  # Must be a loss

    def test_gap_down_through_stop_fills_at_bar_open(self):
        """
        Gap-down scenario: bar opens below stop level.

        Entry at $10, stop at 5% = $9.50
        Next bar gaps down to open at $9.00 (below stop)
        Should fill at $9.00 (bar.open), not $9.50 (stop price never traded)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=9.90, high=10.10, low=9.85, close=10.00, volume=100_000),
            # Gap down - entire bar is below stop
            make_bar(base_time + timedelta(minutes=1), open_=9.00, high=9.20, low=8.80, close=9.10, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=5.0,  # Stop at $9.50
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.00
        assert result.trigger_type == "stop_loss"
        # Gap through stop: fill at bar.open ($9.00), not stop ($9.50)
        assert result.exit_price == pytest.approx(9.00, rel=0.01)

    def test_gap_down_trailing_stop_fills_at_bar_open(self):
        """
        Gap-down through trailing stop level.

        Entry at $10, peaks at $12, trailing 10% = $10.80
        Next bar gaps down to open at $10.00 (below trailing stop)
        Should fill at $10.00 (bar.open), not $10.80 (trailing stop price)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Bar peaks at $12
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=12.00, low=10.00, close=11.50, volume=50_000),
            # Gap down below trailing stop level
            make_bar(base_time + timedelta(minutes=2), open_=10.00, high=10.20, low=9.80, close=10.10, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=30.0,
            stop_loss_pct=20.0,
            trailing_stop_pct=10.0,  # From $12: $10.80
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        # Gap through trailing stop: fill at bar.open ($10.00), not trailing ($10.80)
        assert result.exit_price == pytest.approx(10.00, rel=0.01)

    def test_normal_stop_loss_still_fills_at_stop_price(self):
        """
        Normal scenario: bar trades through stop level without gap.

        Entry at $10, stop at 5% = $9.50
        Bar: open=$10, high=$10.05, low=$9.40, close=$9.60
        Bar.high ($10.05) > stop ($9.50), so stop was traded
        Should fill at $9.50 (stop price)
        """
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.95, close=10.00, volume=100_000),
            # Normal bar - drops through stop but didn't gap
            make_bar(base_time + timedelta(minutes=1), open_=10.00, high=10.05, low=9.40, close=9.60, volume=50_000),
        ]

        config = BacktestConfig(
            entry_at_candle_close=True,
            take_profit_pct=20.0,
            stop_loss_pct=5.0,  # Stop at $9.50
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.00
        assert result.trigger_type == "stop_loss"
        # Normal stop (no gap): fill at stop price
        assert result.exit_price == pytest.approx(9.50, rel=0.01)


class TestEntryWindowMinutes:
    """Tests for entry_window_minutes - limiting how long to look for entry."""

    def test_entry_window_limits_search_for_consecutive_candles(self):
        """Entry window limits how long we search for consecutive green candles."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # 3 red candles, then 2 green candles at minute 4 and 5
        # But entry window is 3 minutes, so we never see the green candles
        bars = [
            # Red candles (minutes 0-2)
            make_bar(base_time, open_=10.00, high=10.10, low=9.80, close=9.90, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=9.90, high=10.00, low=9.70, close=9.80, volume=100_000),
            make_bar(base_time + timedelta(minutes=2), open_=9.80, high=9.90, low=9.60, close=9.70, volume=100_000),
            # Green candles (minutes 3-4) - outside 3 minute entry window
            make_bar(base_time + timedelta(minutes=3), open_=9.70, high=10.00, low=9.65, close=9.90, volume=100_000),
            make_bar(base_time + timedelta(minutes=4), open_=9.90, high=10.20, low=9.85, close=10.10, volume=100_000),
            # Entry would be here (minute 5)
            make_bar(base_time + timedelta(minutes=5), open_=10.10, high=10.50, low=10.00, close=10.40, volume=100_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=2,
            min_candle_volume=50_000,
            take_profit_pct=20.0,
            stop_loss_pct=10.0,
            window_minutes=60,
            entry_window_minutes=3,  # Only look for entry in first 3 minutes
        )

        result = run_single_backtest(announcement, bars, config)

        # Should NOT enter because entry window expired before green candles
        assert not result.entered
        assert result.trigger_type == "no_entry"

    def test_entry_window_allows_entry_within_window(self):
        """Entry succeeds when conditions are met within entry window."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # 2 green candles in first 2 minutes
        bars = [
            make_bar(base_time, open_=10.00, high=10.30, low=9.95, close=10.20, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.20, high=10.50, low=10.15, close=10.40, volume=100_000),
            # Entry bar
            make_bar(base_time + timedelta(minutes=2), open_=10.40, high=10.80, low=10.30, close=10.60, volume=100_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=2,
            min_candle_volume=50_000,
            take_profit_pct=20.0,
            stop_loss_pct=10.0,
            window_minutes=60,
            entry_window_minutes=5,  # Entry window is 5 minutes
        )

        result = run_single_backtest(announcement, bars, config)

        # Should enter because conditions met within entry window
        assert result.entered
        assert result.entry_price == 10.40  # Open of bar after signal

    def test_entry_window_limits_volume_trigger_mode(self):
        """Entry window limits search in volume/price trigger mode."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # First 3 bars have low volume, 4th bar has enough volume
        bars = [
            make_bar(base_time, open_=10.00, high=10.50, low=9.90, close=10.30, volume=30_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.30, high=10.60, low=10.20, close=10.50, volume=30_000),
            make_bar(base_time + timedelta(minutes=2), open_=10.50, high=10.80, low=10.40, close=10.70, volume=30_000),
            # This bar has enough volume but is outside entry window
            make_bar(base_time + timedelta(minutes=3), open_=10.70, high=11.00, low=10.60, close=10.90, volume=100_000),
        ]

        config = BacktestConfig(
            entry_trigger_pct=5.0,  # Need +5% from $10 = $10.50
            volume_threshold=75_000,  # Only met on bar at minute 3
            take_profit_pct=20.0,
            stop_loss_pct=10.0,
            window_minutes=60,
            entry_window_minutes=3,  # Only look for entry in first 3 minutes
        )

        result = run_single_backtest(announcement, bars, config)

        # Should NOT enter - volume condition only met after entry window
        assert not result.entered
        assert result.trigger_type == "no_entry"

    def test_entry_window_zero_uses_full_window(self):
        """Entry window of 0 should use full window_minutes."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # Green candles appear at minute 10
        bars = [make_bar(base_time + timedelta(minutes=i),
                        open_=10.0 - i*0.1, high=10.0 - i*0.1 + 0.1,
                        low=10.0 - i*0.1 - 0.1, close=10.0 - i*0.1 - 0.05,
                        volume=100_000) for i in range(10)]
        # Add green candles
        bars.append(make_bar(base_time + timedelta(minutes=10), open_=9.0, high=9.3, low=8.95, close=9.2, volume=100_000))
        bars.append(make_bar(base_time + timedelta(minutes=11), open_=9.2, high=9.5, low=9.15, close=9.4, volume=100_000))
        bars.append(make_bar(base_time + timedelta(minutes=12), open_=9.4, high=9.7, low=9.35, close=9.6, volume=100_000))

        config = BacktestConfig(
            entry_after_consecutive_candles=2,
            min_candle_volume=50_000,
            take_profit_pct=20.0,
            stop_loss_pct=10.0,
            window_minutes=60,
            entry_window_minutes=0,  # 0 means use full window
        )

        result = run_single_backtest(announcement, bars, config)

        # Should enter because entry_window_minutes=0 means full window
        assert result.entered
        assert result.entry_price == 9.4  # Open of bar after 2 green candles

    def test_entry_window_separate_from_hold_window(self):
        """Entry window and hold window (window_minutes) are independent."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time)

        # Entry within first 2 minutes, but hold for 60 minutes
        bars = [
            make_bar(base_time, open_=10.00, high=10.30, low=9.95, close=10.20, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.20, high=10.50, low=10.15, close=10.40, volume=100_000),
            # Entry bar
            make_bar(base_time + timedelta(minutes=2), open_=10.40, high=10.60, low=10.30, close=10.50, volume=100_000),
        ]
        # Add more bars for the hold period
        for i in range(3, 60):
            bars.append(make_bar(base_time + timedelta(minutes=i),
                                open_=10.50, high=10.60, low=10.40, close=10.50, volume=50_000))

        config = BacktestConfig(
            entry_after_consecutive_candles=2,
            min_candle_volume=50_000,
            take_profit_pct=50.0,  # Won't hit
            stop_loss_pct=20.0,  # Won't hit
            window_minutes=60,  # Hold for 60 minutes
            entry_window_minutes=5,  # Only look for entry in first 5 minutes
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_time == base_time + timedelta(minutes=2)
        assert result.trigger_type == "timeout"
        # Exit should be at the end of window_minutes (60 min), not entry_window_minutes
        assert result.exit_time == base_time + timedelta(minutes=59)
