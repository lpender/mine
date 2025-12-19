import pytest
from datetime import datetime, timedelta
from src.models import Announcement, OHLCVBar, BacktestConfig, TradeResult
from src.backtest import run_single_backtest


def make_announcement(ticker: str = "TEST", timestamp: datetime = None) -> Announcement:
    """Helper to create test announcements."""
    if timestamp is None:
        # Use 09:29:30 so that ann_minute_end = 09:30:00 and bars at 09:30:00 are valid
        # (The backtest only considers bars starting AFTER the announcement minute)
        timestamp = datetime(2025, 1, 15, 9, 29, 30)
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


class TestConsecutiveGreenCandlesEntry:
    """Tests for consecutive green candles entry logic."""

    def test_consecutive_green_candles_entry(self):
        """Entry after X consecutive green candles enters at OPEN of next bar."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

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
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

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


class TestEntryWindowMinutes:
    """Tests for entry_window_minutes - limiting how long to look for entry."""

    def test_entry_window_limits_search_for_consecutive_candles(self):
        """Entry window limits how long we search for consecutive green candles."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

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
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

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

    def test_entry_window_zero_uses_full_window(self):
        """Entry window of 0 should use full window_minutes."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

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
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

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


class TestAnnouncementBarCounting:
    """Tests for counting the announcement bar toward consecutive green candles."""

    def test_announcement_bar_counts_toward_consecutive_candles(self):
        """
        The announcement bar (bar containing the announcement) should count
        toward the consecutive green candle requirement, even though we can't
        enter during it.

        Example: Announcement at 13:00:03, 3 green candles required
        - 13:00 bar (contains announcement): GREEN = counts as #1
        - 13:01 bar: GREEN = counts as #2
        - 13:02 bar: GREEN = counts as #3 (signal triggered)
        - Entry at 13:03 OPEN (bar after 3rd green closes)
        """
        from src.backtest import run_single_backtest
        from src.models import Announcement, OHLCVBar, BacktestConfig

        ann = Announcement(
            ticker='TEST',
            timestamp=datetime(2025, 9, 19, 13, 0, 3),  # 3 seconds into the 13:00 bar
            price_threshold=2.40,
            headline='Test',
            country='US',
            channel='select-news',
            direction='up',
        )

        # All green bars
        bars = [
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 0, 0), open=2.40, high=3.00, low=2.38, close=2.99, volume=100000),
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 1, 0), open=2.98, high=3.55, low=2.95, close=3.52, volume=100000),
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 2, 0), open=3.51, high=4.45, low=3.50, close=4.38, volume=100000),
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 3, 0), open=4.40, high=5.00, low=4.35, close=4.80, volume=100000),
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 4, 0), open=4.80, high=5.20, low=4.70, close=5.00, volume=100000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=3,
            min_candle_volume=0,
            window_minutes=30,
            take_profit_pct=50.0,
            stop_loss_pct=10.0,
        )

        result = run_single_backtest(ann, bars, config)

        # With announcement bar counting:
        # - 13:00 = green #1 (announcement bar)
        # - 13:01 = green #2
        # - 13:02 = green #3 (signal)
        # - Entry at 13:03 OPEN
        assert result.entry_time == datetime(2025, 9, 19, 13, 3, 0), (
            f"Expected entry at 13:03:00, got {result.entry_time}"
        )
        assert result.entry_price == 4.40  # Open of 13:03 bar

    def test_announcement_bar_not_counted_if_red(self):
        """
        If the announcement bar is red, it should reset the count.
        """
        from src.backtest import run_single_backtest
        from src.models import Announcement, OHLCVBar, BacktestConfig

        ann = Announcement(
            ticker='TEST',
            timestamp=datetime(2025, 9, 19, 13, 0, 3),
            price_threshold=2.40,
            headline='Test',
            country='US',
            channel='select-news',
            direction='up',
        )

        # Announcement bar is RED, subsequent bars are green
        bars = [
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 0, 0), open=3.00, high=3.00, low=2.38, close=2.40, volume=100000),  # RED
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 1, 0), open=2.40, high=3.00, low=2.35, close=2.90, volume=100000),  # GREEN
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 2, 0), open=2.90, high=3.50, low=2.85, close=3.40, volume=100000),  # GREEN
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 3, 0), open=3.40, high=4.00, low=3.35, close=3.90, volume=100000),  # GREEN
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 4, 0), open=3.90, high=4.50, low=3.85, close=4.40, volume=100000),  # GREEN
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=3,
            min_candle_volume=0,
            window_minutes=30,
            take_profit_pct=50.0,
            stop_loss_pct=10.0,
        )

        result = run_single_backtest(ann, bars, config)

        # Red announcement bar doesn't count, so:
        # - 13:00 = RED (doesn't count)
        # - 13:01 = green #1
        # - 13:02 = green #2
        # - 13:03 = green #3 (signal)
        # - Entry at 13:04 OPEN
        assert result.entry_time == datetime(2025, 9, 19, 13, 4, 0), (
            f"Expected entry at 13:04:00 (red announcement bar), got {result.entry_time}"
        )
        assert result.entry_price == 3.90  # Open of 13:04 bar

    def test_single_green_candle_enters_after_announcement_bar(self):
        """
        With only 1 green candle required, if the announcement bar is green,
        entry should be at the OPEN of the next bar (first post-announcement bar).
        """
        from src.backtest import run_single_backtest
        from src.models import Announcement, OHLCVBar, BacktestConfig

        ann = Announcement(
            ticker='TEST',
            timestamp=datetime(2025, 9, 19, 13, 0, 3),
            price_threshold=2.40,
            headline='Test',
            country='US',
            channel='select-news',
            direction='up',
        )

        bars = [
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 0, 0), open=2.40, high=3.00, low=2.38, close=2.99, volume=100000),  # GREEN
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 1, 0), open=2.98, high=3.55, low=2.95, close=3.52, volume=100000),  # GREEN
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 2, 0), open=3.51, high=4.45, low=3.50, close=4.38, volume=100000),  # GREEN
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=1,
            min_candle_volume=0,
            window_minutes=30,
            take_profit_pct=50.0,
            stop_loss_pct=10.0,
        )

        result = run_single_backtest(ann, bars, config)

        # With 1 green candle required and announcement bar is green:
        # - 13:00 = green #1 (signal triggered by announcement bar)
        # - Entry at 13:01 OPEN (first post-announcement bar)
        assert result.entry_time == datetime(2025, 9, 19, 13, 1, 0), (
            f"Expected entry at 13:01:00, got {result.entry_time}"
        )
        assert result.entry_price == 2.98  # Open of 13:01 bar

    def test_zero_green_candles_enters_at_first_bar_open(self):
        """
        With 0 green candles required, entry should be at the OPEN of the
        first post-announcement bar, regardless of candle color.

        This is the "enter immediately" mode - no waiting for green candles.
        """
        from src.backtest import run_single_backtest
        from src.models import Announcement, OHLCVBar, BacktestConfig

        ann = Announcement(
            ticker='TEST',
            timestamp=datetime(2025, 9, 19, 13, 0, 3),
            price_threshold=2.40,
            headline='Test',
            country='US',
            channel='select-news',
            direction='up',
        )

        # First post-announcement bar is RED - should still enter at its OPEN
        bars = [
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 0, 0), open=2.40, high=2.50, low=2.30, close=2.35, volume=100000),  # RED (announcement bar)
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 1, 0), open=2.35, high=2.40, low=2.20, close=2.25, volume=100000),  # RED (first post-ann bar)
            OHLCVBar(timestamp=datetime(2025, 9, 19, 13, 2, 0), open=2.25, high=2.30, low=2.15, close=2.20, volume=100000),  # RED
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,  # Enter immediately at first bar open
            min_candle_volume=0,
            window_minutes=30,
            take_profit_pct=50.0,
            stop_loss_pct=10.0,
        )

        result = run_single_backtest(ann, bars, config)

        # With 0 green candles required:
        # - Entry at 13:01 OPEN (first post-announcement bar), regardless of color
        assert result.entered, "Should have entered"
        assert result.entry_time == datetime(2025, 9, 19, 13, 1, 0), (
            f"Expected entry at 13:01:00 (first post-announcement bar), got {result.entry_time}"
        )
        assert result.entry_price == 2.35  # OPEN of 13:01 bar, NOT close

    def test_zero_green_candles_does_not_use_close(self):
        """
        Regression test: with 0 green candles, entry should be at OPEN, not CLOSE.

        This was a bug where entry_after_consecutive_candles=0 caused
        entry_at_candle_close=True, resulting in entry at bar close.
        """
        from src.backtest import run_single_backtest
        from src.models import Announcement, OHLCVBar, BacktestConfig

        ann = Announcement(
            ticker='DTIL',
            timestamp=datetime(2025, 1, 10, 7, 50, 28),  # 7:50:28 announcement
            price_threshold=7.0,
            headline='Test',
            country='US',
        )

        # Simulating the DTIL scenario from the screenshot
        # The "7:51 candle" ends at 7:51 (spans 7:50-7:51), contains announcement
        # The "7:52 candle" spans 7:51-7:52, this is first post-announcement bar
        bars = [
            OHLCVBar(timestamp=datetime(2025, 1, 10, 7, 50, 0), open=6.50, high=7.50, low=6.40, close=7.00, volume=100000),  # Contains announcement
            OHLCVBar(timestamp=datetime(2025, 1, 10, 7, 51, 0), open=7.16, high=7.50, low=6.80, close=6.90, volume=100000),  # First post-ann bar
            OHLCVBar(timestamp=datetime(2025, 1, 10, 7, 52, 0), open=6.90, high=7.20, low=6.70, close=7.10, volume=100000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,  # Enter immediately at bar open
            min_candle_volume=0,
            window_minutes=30,
            take_profit_pct=100.0,  # High TP to avoid exit
            stop_loss_pct=20.0,  # High SL to avoid exit
        )

        result = run_single_backtest(ann, bars, config)

        assert result.entered, "Should have entered"
        # Entry should be at OPEN of first post-announcement bar (7:51 timestamp)
        # NOT at the CLOSE (6.90)
        assert result.entry_time == datetime(2025, 1, 10, 7, 51, 0), (
            f"Expected entry at 07:51:00, got {result.entry_time}"
        )
        assert result.entry_price == 7.16, (
            f"Expected entry at OPEN (7.16), not CLOSE (6.90), got {result.entry_price}"
        )


class TestExitLogic:
    """Tests for exit conditions (stop loss, take profit, timeout)."""

    def test_stop_loss_triggers_on_entry_bar(self):
        """Stop loss can trigger on the entry bar if price drops."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

        # Entry at bar open, then price drops to hit stop loss within same bar
        bars = [
            make_bar(base_time, open_=10.00, high=10.10, low=9.00, close=9.50, volume=100_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,  # Enter immediately
            take_profit_pct=20.0,
            stop_loss_pct=5.0,  # Stop at $9.50
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.00  # Entry at bar open
        assert result.trigger_type == "stop_loss"
        assert result.exit_price == pytest.approx(9.50, rel=0.01)  # Stop price

    def test_take_profit_triggers(self):
        """Take profit triggers when price reaches target."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

        bars = [
            make_bar(base_time, open_=10.00, high=12.00, low=9.90, close=11.50, volume=100_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,
            take_profit_pct=10.0,  # TP at $11.00
            stop_loss_pct=5.0,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.entry_price == 10.00
        assert result.trigger_type == "take_profit"
        assert result.exit_price == pytest.approx(11.00, rel=0.01)

    def test_timeout_exit(self):
        """Position exits at timeout when neither TP nor SL is hit."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

        # Bars that don't trigger TP or SL
        bars = [
            make_bar(base_time, open_=10.00, high=10.20, low=9.90, close=10.10, volume=100_000),
            make_bar(base_time + timedelta(minutes=1), open_=10.10, high=10.30, low=10.00, close=10.15, volume=50_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,
            take_profit_pct=50.0,  # Won't hit
            stop_loss_pct=50.0,  # Won't hit
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "timeout"
        assert result.exit_price == 10.15  # Last bar's close

    def test_trailing_stop(self):
        """Trailing stop triggers when price drops from high."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

        bars = [
            # Price peaks at $12, then drops
            make_bar(base_time, open_=10.00, high=12.00, low=9.90, close=11.50, volume=100_000),
            # Price drops from peak
            make_bar(base_time + timedelta(minutes=1), open_=11.50, high=11.60, low=10.50, close=10.60, volume=50_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,
            take_profit_pct=50.0,  # Won't hit
            stop_loss_pct=20.0,  # Won't hit
            trailing_stop_pct=10.0,  # 10% trailing from $12 = $10.80
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "trailing_stop"
        assert result.exit_price == pytest.approx(10.80, rel=0.01)


class TestExitAfterRedCandles:
    """Tests for exit_after_red_candles exit condition."""

    def test_exit_after_consecutive_red_candles(self):
        """Exit after N consecutive red candles."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

        bars = [
            # Entry bar
            make_bar(base_time, open_=10.00, high=10.30, low=9.90, close=10.20, volume=100_000),
            # Red candle 1
            make_bar(base_time + timedelta(minutes=1), open_=10.20, high=10.25, low=10.00, close=10.05, volume=50_000),
            # Red candle 2 - triggers exit
            make_bar(base_time + timedelta(minutes=2), open_=10.05, high=10.10, low=9.90, close=9.95, volume=50_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,
            take_profit_pct=50.0,
            stop_loss_pct=50.0,
            exit_after_red_candles=2,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "red_candles"
        assert result.exit_price == 9.95  # Close of 2nd red candle

    def test_green_candle_resets_red_count(self):
        """A green candle resets the consecutive red candle count."""
        base_time = datetime(2025, 1, 15, 9, 30)
        announcement = make_announcement(timestamp=base_time - timedelta(seconds=30))

        bars = [
            # Entry bar
            make_bar(base_time, open_=10.00, high=10.30, low=9.90, close=10.20, volume=100_000),
            # Red candle 1
            make_bar(base_time + timedelta(minutes=1), open_=10.20, high=10.25, low=10.00, close=10.05, volume=50_000),
            # Green candle - resets count
            make_bar(base_time + timedelta(minutes=2), open_=10.05, high=10.20, low=10.00, close=10.15, volume=50_000),
            # Red candle 1 (after reset)
            make_bar(base_time + timedelta(minutes=3), open_=10.15, high=10.20, low=10.00, close=10.02, volume=50_000),
            # Red candle 2 - triggers exit
            make_bar(base_time + timedelta(minutes=4), open_=10.02, high=10.05, low=9.90, close=9.92, volume=50_000),
        ]

        config = BacktestConfig(
            entry_after_consecutive_candles=0,
            take_profit_pct=50.0,
            stop_loss_pct=50.0,
            exit_after_red_candles=2,
            window_minutes=30,
        )

        result = run_single_backtest(announcement, bars, config)

        assert result.entered
        assert result.trigger_type == "red_candles"
        # Exit on 5th bar (minute 4), not 3rd bar
        assert result.exit_time == base_time + timedelta(minutes=4)
        assert result.exit_price == 9.92
