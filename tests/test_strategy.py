"""Tests for src/strategy.py"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from src.strategy import StrategyConfig, StrategyEngine, PendingEntry
from src.models import Announcement


class TestCandleVolumeAggregation:
    """Tests for volume aggregation in candle building."""

    def create_engine(self, consec_green_candles=1, min_candle_volume=1000):
        """Create a strategy engine for testing."""
        config = StrategyConfig(
            channels=["test-channel"],
            directions=["up", "up_right"],  # Accept both directions
            sessions=["market"],
            consec_green_candles=consec_green_candles,
            min_candle_volume=min_candle_volume,
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            timeout_minutes=60,
        )
        # Create mock trader with proper return values
        trader = Mock()
        trader.get_positions.return_value = []
        trader.get_open_orders.return_value = []
        trader.is_tradeable.return_value = (True, "tradeable")

        engine = StrategyEngine(
            strategy_id="test-strategy",
            config=config,
            trader=trader,
        )
        # Mock callbacks
        engine.on_subscribe = Mock()
        engine.on_unsubscribe = Mock()
        return engine

    def create_announcement(self, ticker="TEST"):
        """Create a test announcement."""
        return Announcement(
            ticker=ticker,
            timestamp=datetime(2025, 12, 12, 15, 0, 0),  # 10am ET = market hours
            price_threshold=5.0,
            headline="Test announcement",
            country="US",
            channel="test-channel",
            direction="up",
        )

    def test_volume_is_summed_not_overwritten(self):
        """
        Regression test: Volume should be SUMMED across all 1-second bars
        within a minute candle, not overwritten with the latest value.

        Bug: Line 481 in strategy.py was doing:
            pending.current_candle_data["volume"] = volume  # Only keeps last!

        Should be:
            pending.current_candle_data["volume"] += volume  # Sum all bars
        """
        engine = self.create_engine(consec_green_candles=1, min_candle_volume=5000)
        ann = self.create_announcement()

        # Add pending entry
        engine.on_alert(ann)
        assert "TEST" in engine.pending_entries

        # Simulate 1-second bars within the same minute
        # Each bar has volume of 1000
        base_time = datetime(2025, 12, 12, 15, 30, 0)  # 10:30:00

        # Send 5 quotes at different seconds, each with volume=1000
        for sec in range(5):
            ts = base_time.replace(second=sec)
            engine.on_quote("TEST", price=5.10, volume=1000, timestamp=ts)

        pending = engine.pending_entries["TEST"]

        # The current candle should have volume = 5000 (5 * 1000)
        # NOT volume = 1000 (just the last one)
        assert pending.current_candle_data is not None
        actual_volume = pending.current_candle_data["volume"]

        assert actual_volume == 5000, (
            f"Volume should be summed (5000), but got {actual_volume}. "
            "Bug: volume is being overwritten instead of accumulated!"
        )

    def test_volume_resets_on_new_candle(self):
        """Volume should reset when a new minute candle starts."""
        engine = self.create_engine(consec_green_candles=2, min_candle_volume=1000)
        ann = self.create_announcement()
        engine.on_alert(ann)

        # First minute: 3 quotes with volume 1000 each
        minute1 = datetime(2025, 12, 12, 15, 30, 0)
        for sec in range(3):
            engine.on_quote("TEST", price=5.10, volume=1000, timestamp=minute1.replace(second=sec))

        # Verify first candle volume is summed
        pending = engine.pending_entries["TEST"]
        assert pending.current_candle_data["volume"] == 3000

        # Second minute: 2 quotes with volume 500 each
        minute2 = datetime(2025, 12, 12, 15, 31, 0)
        for sec in range(2):
            engine.on_quote("TEST", price=5.20, volume=500, timestamp=minute2.replace(second=sec))

        # First candle should be finalized and stored
        assert len(pending.candles) == 1
        assert pending.candles[0].volume == 3000

        # Current (second) candle should have new volume
        assert pending.current_candle_data["volume"] == 1000  # 2 * 500

    def test_green_candle_volume_threshold(self):
        """Entry should only trigger when candle meets volume threshold."""
        engine = self.create_engine(consec_green_candles=1, min_candle_volume=5000)
        ann = self.create_announcement()
        engine.on_alert(ann)

        # First candle: green but insufficient volume (only 3000)
        minute1 = datetime(2025, 12, 12, 15, 30, 0)
        engine.on_quote("TEST", price=5.00, volume=1000, timestamp=minute1.replace(second=0))
        engine.on_quote("TEST", price=5.10, volume=1000, timestamp=minute1.replace(second=30))
        engine.on_quote("TEST", price=5.20, volume=1000, timestamp=minute1.replace(second=59))

        # Move to next candle to finalize first
        minute2 = datetime(2025, 12, 12, 15, 31, 0)
        engine.on_quote("TEST", price=5.25, volume=1000, timestamp=minute2)

        # Should still be pending - first candle was green but only had 3000 volume
        assert "TEST" in engine.pending_entries
        assert "TEST" not in engine.active_trades

        pending = engine.pending_entries["TEST"]
        assert len(pending.candles) == 1
        assert pending.candles[0].volume == 3000
        assert pending.candles[0].is_green  # close > open

    def test_entry_triggers_with_sufficient_volume(self):
        """Entry should trigger when green candle meets volume threshold."""
        engine = self.create_engine(consec_green_candles=1, min_candle_volume=5000)

        # Mock the trader.buy to return an order (pending orders flow)
        engine.trader.buy = Mock(return_value=Mock(
            order_id="test-order-123",
            ticker="TEST",
            side="buy",
            shares=100,
            order_type="limit",
            status="new",
        ))
        engine.trader.is_tradeable = Mock(return_value=(True, "tradeable"))

        ann = self.create_announcement()
        engine.on_alert(ann)

        # First candle: green with sufficient volume (6000)
        minute1 = datetime(2025, 12, 12, 15, 30, 0)
        for sec in range(6):
            engine.on_quote("TEST", price=5.00 + sec * 0.05, volume=1000, timestamp=minute1.replace(second=sec * 10))

        # Move to next candle to finalize first and check entry
        minute2 = datetime(2025, 12, 12, 15, 31, 0)
        engine.on_quote("TEST", price=5.30, volume=1000, timestamp=minute2)

        # Should have triggered entry - buy order submitted, now in pending_orders
        # (ActiveTrade is created when fill is confirmed)
        assert engine.trader.buy.called
        assert "test-order-123" in engine.pending_orders


class TestVolumeExtrapolation:
    """Tests for volume extrapolation in early entry position sizing."""

    def create_engine(self, stake_mode="volume_pct", volume_pct=2.0, max_stake=40000.0):
        """Create a strategy engine with volume-based position sizing."""
        config = StrategyConfig(
            channels=["test-channel"],
            directions=["up", "up_right"],
            sessions=["market"],
            consec_green_candles=0,  # Early entry enabled
            min_candle_volume=0,  # No volume threshold
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            timeout_minutes=60,
            stake_mode=stake_mode,
            volume_pct=volume_pct,
            max_stake=max_stake,
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
        engine.on_subscribe = Mock()
        engine.on_unsubscribe = Mock()
        # Mock order store to avoid database calls
        engine._order_store = Mock()
        engine._order_store.create_order.return_value = 1
        return engine

    def create_announcement(self, ticker="TEST"):
        """Create a test announcement."""
        return Announcement(
            ticker=ticker,
            timestamp=datetime(2025, 12, 12, 15, 0, 0),
            price_threshold=5.0,
            headline="Test announcement",
            country="US",
            channel="test-channel",
            direction="up",
        )

    def test_volume_extrapolation_15_seconds(self):
        """
        Test volume extrapolation for early entry when building candle triggers entry.

        Scenario: consec_green_candles=1, min_candle_volume=1000
        - Building candle reaches 1000 volume at 15 seconds
        - This triggers EARLY ENTRY (line 556 in strategy.py)
        - We extrapolate: 1000 * (60/15) = 4000 estimated full-minute volume
        - 2% of 4000 = 80 shares
        """
        engine = self.create_engine(stake_mode="volume_pct", volume_pct=2.0)
        # Set up for early entry: need 1 green candle with min 1000 volume
        engine.config.consec_green_candles = 1
        engine.config.min_candle_volume = 1000

        buy_calls = []
        def mock_buy(ticker, shares, limit_price=None):
            buy_calls.append({"ticker": ticker, "shares": shares, "price": limit_price})
            return Mock(
                order_id="test-order-123",
                ticker=ticker,
                side="buy",
                shares=shares,
                order_type="limit",
                status="new",
            )
        engine.trader.buy = mock_buy

        ann = self.create_announcement()
        engine.on_alert(ann)

        # Build candle that's green and hits volume threshold mid-candle
        base_time = datetime(2025, 12, 12, 15, 30, 0)
        engine.on_quote("TEST", price=5.00, volume=500, timestamp=base_time)

        # At second 15, another 500 shares (total 1000) - hits volume threshold
        # Candle is green (5.10 > 5.00 open), volume >= 1000 → EARLY ENTRY
        trigger_time = base_time.replace(second=15)
        engine.on_quote("TEST", price=5.10, volume=500, timestamp=trigger_time)

        # Check that buy was called
        assert len(buy_calls) == 1, f"Expected 1 buy call, got {len(buy_calls)}"

        # Extrapolated volume: 1000 * (60/15) = 4000
        # 2% of 4000 = 80 shares
        expected_shares = 80
        actual_shares = buy_calls[0]["shares"]

        assert actual_shares == expected_shares, (
            f"Expected {expected_shares} shares (2% of 4000 extrapolated volume), "
            f"got {actual_shares}"
        )

    def test_volume_extrapolation_30_seconds(self):
        """
        Test extrapolation at 30 seconds.

        2000 shares in 30 seconds → 2000 * (60/30) = 4000 extrapolated
        2% of 4000 = 80 shares
        """
        engine = self.create_engine(stake_mode="volume_pct", volume_pct=2.0)
        # Set up for early entry
        engine.config.consec_green_candles = 1
        engine.config.min_candle_volume = 2000

        buy_calls = []
        def mock_buy(ticker, shares, limit_price=None):
            buy_calls.append({"ticker": ticker, "shares": shares, "price": limit_price})
            return Mock(order_id="test-order", ticker=ticker, side="buy", shares=shares, order_type="limit", status="new")
        engine.trader.buy = mock_buy

        ann = self.create_announcement()
        engine.on_alert(ann)

        base_time = datetime(2025, 12, 12, 15, 30, 0)
        engine.on_quote("TEST", price=5.00, volume=1000, timestamp=base_time)

        # At 30 seconds, total 2000 volume, candle is green → early entry
        trigger_time = base_time.replace(second=30)
        engine.on_quote("TEST", price=5.10, volume=1000, timestamp=trigger_time)

        assert len(buy_calls) == 1
        # 2000 vol in 30s → 4000 extrapolated → 2% = 80 shares
        assert buy_calls[0]["shares"] == 80

    def test_early_entry_extrapolates_building_candle(self):
        """
        Verify early entry extrapolates building candle volume.

        When building candle meets criteria mid-minute, early entry triggers
        with extrapolated volume based on elapsed time.

        10000 volume at 30 seconds → 20000 extrapolated → 2% = 400 shares
        """
        engine = self.create_engine(
            stake_mode="volume_pct",
            volume_pct=2.0,
        )
        engine.config.consec_green_candles = 1
        engine.config.min_candle_volume = 5000

        buy_calls = []
        def mock_buy(ticker, shares, limit_price=None):
            buy_calls.append({"ticker": ticker, "shares": shares, "price": limit_price})
            return Mock(order_id="test-order", ticker=ticker, side="buy", shares=shares, order_type="limit", status="new")
        engine.trader.buy = mock_buy

        ann = self.create_announcement()
        engine.on_alert(ann)

        # Build candle that triggers early entry at 30 seconds
        minute1 = datetime(2025, 12, 12, 15, 30, 0)
        engine.on_quote("TEST", price=5.00, volume=5000, timestamp=minute1.replace(second=0))
        engine.on_quote("TEST", price=5.10, volume=5000, timestamp=minute1.replace(second=30))
        # At 30s: green candle, 10000 vol >= 5000 threshold → EARLY ENTRY

        # Entry should trigger mid-candle with extrapolated volume
        assert len(buy_calls) == 1
        # 10000 vol at 30s → extrapolate to 20000 for full minute
        # 2% of 20000 = 400 shares
        assert buy_calls[0]["shares"] == 400, (
            f"Expected 400 shares (2% of 20000 extrapolated volume), "
            f"got {buy_calls[0]['shares']}"
        )

    def test_max_stake_caps_extrapolated_volume(self):
        """
        max_stake should cap position size even with high extrapolated volume.
        """
        engine = self.create_engine(
            stake_mode="volume_pct",
            volume_pct=2.0,
            max_stake=100.0,  # Cap at $100
        )
        # Set up for early entry with high volume
        engine.config.consec_green_candles = 1
        engine.config.min_candle_volume = 50000

        buy_calls = []
        def mock_buy(ticker, shares, limit_price=None):
            buy_calls.append({"ticker": ticker, "shares": shares, "price": limit_price})
            return Mock(order_id="test-order", ticker=ticker, side="buy", shares=shares, order_type="limit", status="new")
        engine.trader.buy = mock_buy

        ann = self.create_announcement()
        engine.on_alert(ann)

        # High volume: 50000 in 30s → 100000 extrapolated
        # 2% of 100000 = 2000 shares @ $5 = $10,000 (way over max_stake)
        base_time = datetime(2025, 12, 12, 15, 30, 0)
        engine.on_quote("TEST", price=5.00, volume=25000, timestamp=base_time)
        engine.on_quote("TEST", price=5.10, volume=25000, timestamp=base_time.replace(second=30))

        assert len(buy_calls) == 1
        # max_stake $100 / $5.10 = 19 shares
        actual_shares = buy_calls[0]["shares"]
        max_allowed = int(100.0 / 5.10)  # 19

        assert actual_shares == max_allowed, (
            f"Expected {max_allowed} shares (capped by $100 max_stake), "
            f"got {actual_shares}"
        )
