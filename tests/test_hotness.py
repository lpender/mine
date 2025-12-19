#!/usr/bin/env python3
"""Tests for hotness coefficient functionality.

Ensures parity between backtest and live trading implementations.
"""

import pytest
import sys
import os
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models import BacktestConfig, TradeResult, Announcement
from src.backtest import calculate_hotness, run_backtest
from src.strategy import StrategyConfig, StrategyEngine


class TestHotnessCalculation:
    """Test hotness multiplier calculation."""

    def test_hotness_no_history_returns_neutral(self):
        """With no trade history, hotness should return 1.0."""
        config = BacktestConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )
        result = calculate_hotness([], config)
        assert result == 1.0

    def test_hotness_all_winners(self):
        """100% win rate should return max multiplier."""
        config = BacktestConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=2.0,
        )
        # Create mock trade results (all winners)
        results = []
        for i in range(5):
            ann = Announcement(
                ticker="TEST",
                timestamp=datetime.now(),
                price_threshold=1.0,
                headline="Test",
                country="US",
            )
            r = TradeResult(announcement=ann)
            r.return_pct = 5.0  # Winner
            r.entry_price = 10.0
            results.append(r)

        mult = calculate_hotness(results, config)
        assert mult == 2.0

    def test_hotness_all_losers(self):
        """0% win rate should return min multiplier."""
        config = BacktestConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=2.0,
        )
        results = []
        for i in range(5):
            ann = Announcement(
                ticker="TEST",
                timestamp=datetime.now(),
                price_threshold=1.0,
                headline="Test",
                country="US",
            )
            r = TradeResult(announcement=ann)
            r.return_pct = -3.0  # Loser
            r.entry_price = 10.0
            results.append(r)

        mult = calculate_hotness(results, config)
        assert mult == 0.5

    def test_hotness_50_percent_winrate(self):
        """50% win rate should return midpoint multiplier."""
        config = BacktestConfig(
            hotness_enabled=True,
            hotness_window=4,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )
        results = []
        for i, ret in enumerate([5.0, -3.0, 5.0, -3.0]):  # 2 wins, 2 losses
            ann = Announcement(
                ticker="TEST",
                timestamp=datetime.now(),
                price_threshold=1.0,
                headline="Test",
                country="US",
            )
            r = TradeResult(announcement=ann)
            r.return_pct = ret
            r.entry_price = 10.0
            results.append(r)

        mult = calculate_hotness(results, config)
        # 50% win rate: 0.5 + 0.5 * (1.5 - 0.5) = 1.0
        assert mult == 1.0


class TestStrategyConfigGetShares:
    """Test position sizing with hotness in StrategyConfig (live trading)."""

    def test_fixed_stake_no_hotness(self):
        """Fixed stake mode without hotness."""
        config = StrategyConfig(
            stake_mode="fixed",
            stake_amount=1000.0,
            max_stake=10000.0,
            hotness_enabled=False,
        )
        shares = config.get_shares(price=10.0, hotness_multiplier=1.5)
        assert shares == 100  # $1000 / $10 = 100 shares (hotness ignored)

    def test_fixed_stake_with_hotness(self):
        """Fixed stake mode with hotness enabled."""
        config = StrategyConfig(
            stake_mode="fixed",
            stake_amount=1000.0,
            max_stake=10000.0,
            hotness_enabled=True,
        )
        shares = config.get_shares(price=10.0, hotness_multiplier=1.5)
        assert shares == 150  # $1000 / $10 * 1.5 = 150 shares

    def test_volume_pct_with_hotness(self):
        """Volume-based sizing with hotness."""
        config = StrategyConfig(
            stake_mode="volume_pct",
            volume_pct=1.0,  # 1% of volume
            max_stake=10000.0,
            hotness_enabled=True,
        )
        shares = config.get_shares(
            price=10.0,
            prev_candle_volume=10000,
            hotness_multiplier=2.0
        )
        # 1% of 10000 = 100, * 2.0 hotness = 200 shares
        assert shares == 200

    def test_max_stake_enforced_after_hotness(self):
        """max_stake should cap position AFTER hotness is applied."""
        config = StrategyConfig(
            stake_mode="fixed",
            stake_amount=1000.0,
            max_stake=500.0,  # Low cap
            hotness_enabled=True,
        )
        # Without cap: $1000 / $10 * 2.0 = 200 shares = $2000
        # With cap: $500 / $10 = 50 shares max
        shares = config.get_shares(price=10.0, hotness_multiplier=2.0)
        assert shares == 50  # Capped by max_stake

    def test_max_stake_enforced_volume_mode(self):
        """max_stake should cap volume-based sizing after hotness."""
        config = StrategyConfig(
            stake_mode="volume_pct",
            volume_pct=10.0,  # 10% of volume
            max_stake=500.0,  # Low cap
            hotness_enabled=True,
        )
        # 10% of 10000 = 1000 shares, * 2.0 = 2000 shares = $20000
        # Cap: $500 / $10 = 50 shares
        shares = config.get_shares(
            price=10.0,
            prev_candle_volume=10000,
            hotness_multiplier=2.0
        )
        assert shares == 50  # Capped by max_stake

    def test_hotness_disabled_ignores_multiplier(self):
        """When hotness_enabled=False, multiplier should be ignored."""
        config = StrategyConfig(
            stake_mode="fixed",
            stake_amount=1000.0,
            max_stake=10000.0,
            hotness_enabled=False,
        )
        # Even with a 10x multiplier, should be ignored
        shares = config.get_shares(price=10.0, hotness_multiplier=10.0)
        assert shares == 100  # Base amount only


class TestTradeResultPnlWithHotness:
    """Test P&L calculation with hotness in TradeResult (backtest)."""

    def test_pnl_without_hotness(self):
        """P&L calculation without hotness."""
        ann = Announcement(
            ticker="TEST",
            timestamp=datetime.now(),
            price_threshold=1.0,
            headline="Test",
            country="US",
        )
        result = TradeResult(announcement=ann)
        result.entry_price = 10.0
        result.exit_price = 11.0
        result.return_pct = 10.0
        result.hotness_multiplier = 1.5  # Should be ignored

        pnl = result.pnl_with_sizing(
            stake_mode="fixed",
            stake_amount=1000.0,
            use_hotness=False
        )
        # 100 shares * $10 * 10% = $100
        assert pnl == 100.0

    def test_pnl_with_hotness(self):
        """P&L calculation with hotness enabled."""
        ann = Announcement(
            ticker="TEST",
            timestamp=datetime.now(),
            price_threshold=1.0,
            headline="Test",
            country="US",
        )
        result = TradeResult(announcement=ann)
        result.entry_price = 10.0
        result.exit_price = 11.0
        result.return_pct = 10.0
        result.hotness_multiplier = 1.5

        pnl = result.pnl_with_sizing(
            stake_mode="fixed",
            stake_amount=1000.0,
            use_hotness=True
        )
        # 100 shares * 1.5 hotness = 150 shares
        # 150 shares * $10 * 10% = $150
        assert pnl == 150.0


class TestStrategyEngineHotness:
    """Test hotness multiplier in StrategyEngine (live trading)."""

    def test_get_hotness_multiplier_disabled(self):
        """Hotness disabled should return 1.0."""
        config = StrategyConfig(hotness_enabled=False)

        # Create a minimal mock trader
        class MockTrader:
            pass

        engine = StrategyEngine(
            config=config,
            trader=MockTrader(),
            paper=True,
        )
        assert engine.get_hotness_multiplier() == 1.0

    def test_get_hotness_multiplier_no_history(self):
        """No trade history should return 1.0."""
        config = StrategyConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )

        class MockTrader:
            pass

        engine = StrategyEngine(
            config=config,
            trader=MockTrader(),
            paper=True,
        )
        assert engine.get_hotness_multiplier() == 1.0

    def test_get_hotness_multiplier_with_in_memory_trades(self):
        """Hotness should be calculated from in-memory completed trades (no strategy_id)."""
        config = StrategyConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )

        class MockTrader:
            pass

        engine = StrategyEngine(
            config=config,
            trader=MockTrader(),
            paper=True,
            strategy_id=None,  # No strategy_id, will use in-memory
        )

        # Add some completed trades (3 wins, 2 losses = 60% win rate)
        engine.completed_trades = [
            {"return_pct": 5.0},   # Win
            {"return_pct": -3.0},  # Loss
            {"return_pct": 5.0},   # Win
            {"return_pct": -3.0},  # Loss
            {"return_pct": 5.0},   # Win
        ]

        # 60% win rate: 0.5 + 0.6 * (1.5 - 0.5) = 0.5 + 0.6 = 1.1
        mult = engine.get_hotness_multiplier()
        assert abs(mult - 1.1) < 0.01

    def test_get_hotness_multiplier_fewer_trades_than_window(self):
        """Should work correctly with fewer trades than the window size."""
        config = StrategyConfig(
            hotness_enabled=True,
            hotness_window=10,  # Window of 10
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )

        class MockTrader:
            pass

        engine = StrategyEngine(
            config=config,
            trader=MockTrader(),
            paper=True,
        )

        # Only 3 trades (less than window of 10)
        engine.completed_trades = [
            {"return_pct": 5.0},   # Win
            {"return_pct": 5.0},   # Win
            {"return_pct": -3.0},  # Loss
        ]

        # 2/3 = 66.7% win rate: 0.5 + 0.667 * 1.0 = 1.167
        mult = engine.get_hotness_multiplier()
        assert abs(mult - 1.167) < 0.01

    def test_get_hotness_multiplier_handles_none_return_pct(self):
        """Should handle trades with None return_pct."""
        config = StrategyConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )

        class MockTrader:
            pass

        engine = StrategyEngine(
            config=config,
            trader=MockTrader(),
            paper=True,
        )

        # Some trades with None return_pct (treated as losses)
        engine.completed_trades = [
            {"return_pct": 5.0},   # Win
            {"return_pct": None},  # Treated as loss
            {"return_pct": 5.0},   # Win
        ]

        # 2/3 = 66.7% win rate
        mult = engine.get_hotness_multiplier()
        assert abs(mult - 1.167) < 0.01


class TestConfigValidation:
    """Test config validation for hotness parameters."""

    def test_strategy_config_caps_max_mult_at_10(self):
        """StrategyConfig should cap max_mult at 10."""
        config = StrategyConfig(
            hotness_enabled=True,
            hotness_max_mult=15.0,  # Too high
        )
        assert config.hotness_max_mult == 10.0

    def test_strategy_config_swaps_min_max_if_inverted(self):
        """StrategyConfig should swap min/max if inverted."""
        config = StrategyConfig(
            hotness_enabled=True,
            hotness_min_mult=2.0,
            hotness_max_mult=0.5,
        )
        assert config.hotness_min_mult == 0.5
        assert config.hotness_max_mult == 2.0


class TestParityBetweenBacktestAndLive:
    """Test that backtest and live trading calculate hotness the same way."""

    def test_same_win_rate_same_multiplier(self):
        """Same win rate should produce same multiplier in both systems."""
        # Backtest config
        backtest_config = BacktestConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=2.0,
        )

        # Live config
        live_config = StrategyConfig(
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=2.0,
        )

        # Create 3 winners, 2 losers (60% win rate)
        backtest_results = []
        live_trades = []
        for i, ret in enumerate([5.0, -3.0, 5.0, -3.0, 5.0]):
            # Backtest format
            ann = Announcement(
                ticker="TEST",
                timestamp=datetime.now(),
                price_threshold=1.0,
                headline="Test",
                country="US",
            )
            r = TradeResult(announcement=ann)
            r.return_pct = ret
            r.entry_price = 10.0
            backtest_results.append(r)

            # Live format
            live_trades.append({"return_pct": ret})

        # Calculate multiplier from backtest
        backtest_mult = calculate_hotness(backtest_results, backtest_config)

        # Calculate multiplier from live engine
        class MockTrader:
            pass

        engine = StrategyEngine(
            config=live_config,
            trader=MockTrader(),
            paper=True,
        )
        engine.completed_trades = live_trades
        live_mult = engine.get_hotness_multiplier()

        # They should be equal
        assert abs(backtest_mult - live_mult) < 0.001
        # Both should be 0.5 + 0.6 * 1.5 = 1.4
        assert abs(backtest_mult - 1.4) < 0.001

    def test_same_shares_calculation(self):
        """Same inputs should produce same shares in both systems."""
        price = 10.0
        prev_volume = 10000
        hotness_mult = 1.5

        # Live trading
        live_config = StrategyConfig(
            stake_mode="volume_pct",
            volume_pct=1.0,
            max_stake=10000.0,
            hotness_enabled=True,
        )
        live_shares = live_config.get_shares(price, prev_volume, hotness_mult)

        # Backtest (pnl_with_sizing approach)
        ann = Announcement(
            ticker="TEST",
            timestamp=datetime.now(),
            price_threshold=1.0,
            headline="Test",
            country="US",
        )
        result = TradeResult(announcement=ann)
        result.entry_price = price
        result.return_pct = 10.0
        result.pre_entry_volume = prev_volume
        result.hotness_multiplier = hotness_mult

        # Calculate shares from backtest approach
        # 1% of 10000 = 100 shares, * 1.5 = 150 shares
        expected_shares = int(prev_volume * 1.0 / 100 * hotness_mult)

        assert live_shares == expected_shares
        assert live_shares == 150


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
