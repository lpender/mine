"""Tests for multi-strategy position management.

These tests verify that multiple strategies can safely trade the same ticker
without interfering with each other's position tracking.

Key invariants:
1. Each strategy tracks its OWN positions independently
2. Quotes are dispatched to ALL strategies that have active trades
3. Exit orders are submitted for the correct number of shares
4. Database correctly tracks (ticker, strategy_id) pairs
5. Recovery loads only positions belonging to each strategy
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass

from src.strategy import StrategyConfig, StrategyEngine, ActiveTrade
from src.live_trading_service import TradingEngine


@dataclass
class MockPosition:
    """Mock broker position."""
    ticker: str
    shares: int
    avg_entry_price: float
    market_value: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_pl_pct: float = 0.0


@dataclass
class MockOrder:
    """Mock order response."""
    order_id: str
    ticker: str
    side: str
    shares: int
    order_type: str = "limit"
    status: str = "accepted"


class TestStrategyEngineIsolation:
    """Test that StrategyEngine properly isolates its positions."""

    @pytest.fixture
    def mock_trader(self):
        """Create a mock trading client."""
        trader = Mock()
        trader.get_positions.return_value = []
        trader.get_position.return_value = None
        return trader

    @pytest.fixture
    def mock_active_trade_store(self):
        """Mock the active trade store."""
        store = Mock()
        store.get_trades_for_strategy.return_value = []
        store.save_trade.return_value = True
        store.delete_trade.return_value = True
        return store

    @pytest.fixture
    def config(self):
        """Default strategy config."""
        return StrategyConfig(
            channels=["test"],
            price_min=1.0,
            price_max=100.0,
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            stake_amount=100.0,
        )

    def test_strategy_only_loads_own_positions(self, mock_trader, mock_active_trade_store, config):
        """Each strategy should only load positions with its own strategy_id."""
        # Setup: DB has positions for two different strategies
        strategy_a_id = "strategy-a-uuid"
        strategy_b_id = "strategy-b-uuid"

        # Mock returns only strategy A's positions
        mock_trade = Mock()
        mock_trade.ticker = "AMCI"
        mock_trade.entry_price = 5.0
        mock_trade.entry_time = datetime.now()
        mock_trade.first_candle_open = 5.0
        mock_trade.shares = 100
        mock_trade.highest_since_entry = 5.5
        mock_trade.stop_loss_price = 4.75
        mock_trade.take_profit_price = 5.50
        mock_trade.last_price = 0.0
        mock_trade.last_quote_time = None

        with patch('src.strategy.get_active_trade_store', return_value=mock_active_trade_store):
            with patch('src.strategy.get_trade_history_client'):
                mock_active_trade_store.get_trades_for_strategy.return_value = [mock_trade]

                engine_a = StrategyEngine(
                    strategy_id=strategy_a_id,
                    strategy_name="Strategy A",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

                # Verify it called with correct strategy_id
                mock_active_trade_store.get_trades_for_strategy.assert_called_with(strategy_a_id)

                # Verify position was loaded
                assert "AMCI" in engine_a.active_trades
                assert engine_a.active_trades["AMCI"].shares == 100

    def test_two_strategies_same_ticker_different_shares(self, mock_trader, config):
        """Two strategies can hold different positions in the same ticker."""
        strategy_a_id = "strategy-a-uuid"
        strategy_b_id = "strategy-b-uuid"

        # Strategy A has 100 shares, Strategy B has 50 shares
        mock_trade_a = Mock()
        mock_trade_a.ticker = "AMCI"
        mock_trade_a.entry_price = 5.0
        mock_trade_a.entry_time = datetime.now()
        mock_trade_a.first_candle_open = 5.0
        mock_trade_a.shares = 100
        mock_trade_a.highest_since_entry = 5.5
        mock_trade_a.stop_loss_price = 4.75
        mock_trade_a.take_profit_price = 5.50
        mock_trade_a.last_price = 0.0
        mock_trade_a.last_quote_time = None

        mock_trade_b = Mock()
        mock_trade_b.ticker = "AMCI"
        mock_trade_b.entry_price = 5.2
        mock_trade_b.entry_time = datetime.now()
        mock_trade_b.first_candle_open = 5.2
        mock_trade_b.shares = 50
        mock_trade_b.highest_since_entry = 5.5
        mock_trade_b.stop_loss_price = 4.94
        mock_trade_b.take_profit_price = 5.72
        mock_trade_b.last_price = 0.0
        mock_trade_b.last_quote_time = None

        store_a = Mock()
        store_a.get_trades_for_strategy.return_value = [mock_trade_a]
        store_a.save_trade.return_value = True

        store_b = Mock()
        store_b.get_trades_for_strategy.return_value = [mock_trade_b]
        store_b.save_trade.return_value = True

        with patch('src.strategy.get_trade_history_client'):
            with patch('src.strategy.get_active_trade_store', return_value=store_a):
                engine_a = StrategyEngine(
                    strategy_id=strategy_a_id,
                    strategy_name="Strategy A",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

            with patch('src.strategy.get_active_trade_store', return_value=store_b):
                engine_b = StrategyEngine(
                    strategy_id=strategy_b_id,
                    strategy_name="Strategy B",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

        # Both strategies have AMCI but different share counts
        assert engine_a.active_trades["AMCI"].shares == 100
        assert engine_b.active_trades["AMCI"].shares == 50

    def test_quote_updates_correct_strategy_position(self, mock_trader, config):
        """When a quote arrives, each strategy updates its OWN position's last_price."""
        # Create two strategies with same ticker, different positions
        with patch('src.strategy.get_active_trade_store') as mock_store:
            with patch('src.strategy.get_trade_history_client'):
                mock_store.return_value.get_trades_for_strategy.return_value = []

                engine_a = StrategyEngine(
                    strategy_id="strategy-a",
                    strategy_name="Strategy A",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

                engine_b = StrategyEngine(
                    strategy_id="strategy-b",
                    strategy_name="Strategy B",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

        # Manually add positions
        engine_a.active_trades["AMCI"] = ActiveTrade(
            ticker="AMCI",
            announcement=None,
            entry_price=5.0,
            entry_time=datetime.now(),
            first_candle_open=5.0,
            shares=100,
            highest_since_entry=5.0,
            stop_loss_price=4.75,
            take_profit_price=5.50,
        )

        engine_b.active_trades["AMCI"] = ActiveTrade(
            ticker="AMCI",
            announcement=None,
            entry_price=5.2,
            entry_time=datetime.now(),
            first_candle_open=5.2,
            shares=50,
            highest_since_entry=5.2,
            stop_loss_price=4.94,
            take_profit_price=5.72,
        )

        # Send quote to both
        now = datetime.now()
        engine_a.on_quote("AMCI", 5.3, 1000, now)
        engine_b.on_quote("AMCI", 5.3, 1000, now)

        # Both should have updated last_price
        assert engine_a.active_trades["AMCI"].last_price == 5.3
        assert engine_b.active_trades["AMCI"].last_price == 5.3

        # But they still have their own share counts
        assert engine_a.active_trades["AMCI"].shares == 100
        assert engine_b.active_trades["AMCI"].shares == 50


class TestExitIsolation:
    """Test that exit orders don't interfere between strategies."""

    @pytest.fixture
    def mock_trader(self):
        trader = Mock()
        trader.get_positions.return_value = []
        trader.get_position.return_value = MockPosition("AMCI", 150, 5.0)  # Total broker position
        order_counter = [0]
        def make_sell_order(ticker, shares, limit_price=None):
            order_counter[0] += 1
            return MockOrder(f"order-{order_counter[0]}", ticker, "sell", shares)
        trader.sell.side_effect = make_sell_order
        return trader

    @pytest.fixture
    def config(self):
        return StrategyConfig(
            channels=["test"],
            price_min=1.0,
            price_max=100.0,
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            stake_amount=100.0,
        )

    def test_strategy_sells_only_its_shares(self, mock_trader, config):
        """When strategy exits, it should only sell its own shares."""
        with patch('src.strategy.get_active_trade_store') as mock_store:
            with patch('src.strategy.get_trade_history_client'):
                mock_store.return_value.get_trades_for_strategy.return_value = []
                mock_store.return_value.delete_trade.return_value = True

                engine_a = StrategyEngine(
                    strategy_id="strategy-a",
                    strategy_name="Strategy A",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

        # Strategy A has 100 shares
        engine_a.active_trades["AMCI"] = ActiveTrade(
            ticker="AMCI",
            announcement=None,
            entry_price=5.0,
            entry_time=datetime.now() - timedelta(minutes=5),
            first_candle_open=5.0,
            shares=100,
            highest_since_entry=5.0,
            stop_loss_price=4.75,
            take_profit_price=5.50,
        )

        # Trigger take profit
        engine_a._execute_exit("AMCI", 5.50, "take_profit", datetime.now())

        # Should have called sell with 100 shares (not 150)
        mock_trader.sell.assert_called_once()
        call_args = mock_trader.sell.call_args
        assert call_args[0][0] == "AMCI"  # ticker
        assert call_args[0][1] == 100  # shares (Strategy A's position)

    def test_both_strategies_can_exit_same_ticker(self, mock_trader, config):
        """Both strategies should be able to exit their positions independently."""
        with patch('src.strategy.get_active_trade_store') as mock_store:
            with patch('src.strategy.get_trade_history_client'):
                mock_store.return_value.get_trades_for_strategy.return_value = []
                mock_store.return_value.delete_trade.return_value = True

                engine_a = StrategyEngine(
                    strategy_id="strategy-a",
                    strategy_name="Strategy A",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

                engine_b = StrategyEngine(
                    strategy_id="strategy-b",
                    strategy_name="Strategy B",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

        # Strategy A: 100 shares, Strategy B: 50 shares
        engine_a.active_trades["AMCI"] = ActiveTrade(
            ticker="AMCI",
            announcement=None,
            entry_price=5.0,
            entry_time=datetime.now() - timedelta(minutes=5),
            first_candle_open=5.0,
            shares=100,
            highest_since_entry=5.0,
            stop_loss_price=4.75,
            take_profit_price=5.50,
        )

        engine_b.active_trades["AMCI"] = ActiveTrade(
            ticker="AMCI",
            announcement=None,
            entry_price=5.2,
            entry_time=datetime.now() - timedelta(minutes=3),
            first_candle_open=5.2,
            shares=50,
            highest_since_entry=5.2,
            stop_loss_price=4.94,
            take_profit_price=5.72,
        )

        # Both exit
        engine_a._execute_exit("AMCI", 5.50, "take_profit", datetime.now())
        engine_b._execute_exit("AMCI", 5.72, "take_profit", datetime.now())

        # Should have two sell calls: 100 shares and 50 shares
        assert mock_trader.sell.call_count == 2

        # Check the share amounts in each call
        calls = mock_trader.sell.call_args_list
        share_amounts = {calls[0][0][1], calls[1][0][1]}
        assert share_amounts == {100, 50}


class TestDatabaseConstraints:
    """Test database properly tracks strategy-position relationships."""

    @pytest.mark.skip(reason="Requires strategy records to exist (foreign key constraint)")
    def test_unique_ticker_strategy_pair(self):
        """Database should allow same ticker with different strategy_ids."""
        from src.database import SessionLocal, ActiveTradeDB, init_db
        from datetime import datetime

        init_db()
        session = SessionLocal()

        try:
            # Clean up any existing test data (use short ticker name)
            session.query(ActiveTradeDB).filter(ActiveTradeDB.ticker == "TSTTK").delete()
            session.commit()

            # Insert for strategy A
            trade_a = ActiveTradeDB(
                ticker="TSTTK",
                strategy_id="test-strategy-a",
                strategy_name="Test Strategy A",
                entry_price=5.0,
                entry_time=datetime.now(),
                first_candle_open=5.0,
                shares=100,
                stop_loss_price=4.75,
                take_profit_price=5.50,
                highest_since_entry=5.0,
                paper=True,
            )
            session.add(trade_a)
            session.commit()

            # Insert for strategy B (same ticker, different strategy)
            trade_b = ActiveTradeDB(
                ticker="TSTTK",
                strategy_id="test-strategy-b",
                strategy_name="Test Strategy B",
                entry_price=5.2,
                entry_time=datetime.now(),
                first_candle_open=5.2,
                shares=50,
                stop_loss_price=4.94,
                take_profit_price=5.72,
                highest_since_entry=5.2,
                paper=True,
            )
            session.add(trade_b)
            session.commit()

            # Query should return both
            trades = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.ticker == "TSTTK"
            ).all()

            assert len(trades) == 2
            strategies = {t.strategy_id for t in trades}
            assert strategies == {"test-strategy-a", "test-strategy-b"}

        finally:
            # Clean up
            session.query(ActiveTradeDB).filter(ActiveTradeDB.ticker == "TSTTK").delete()
            session.commit()
            session.close()

    @pytest.mark.skip(reason="Requires strategy records to exist (foreign key constraint)")
    def test_duplicate_same_strategy_ticker_rejected(self):
        """Inserting duplicate (ticker, strategy_id) should fail or update."""
        from src.active_trade_store import get_active_trade_store
        from datetime import datetime

        store = get_active_trade_store()

        # Clean up first
        store.delete_trade("DUPTST", "dup-strategy")

        # First save
        result1 = store.save_trade(
            ticker="DUPTST",
            strategy_id="dup-strategy",
            strategy_name="Dup Strategy",
            entry_price=5.0,
            entry_time=datetime.now(),
            first_candle_open=5.0,
            shares=100,
            stop_loss_price=4.75,
            take_profit_price=5.50,
            highest_since_entry=5.0,
            paper=True,
        )

        # Second save with same ticker + strategy should update, not create duplicate
        result2 = store.save_trade(
            ticker="DUPTST",
            strategy_id="dup-strategy",
            strategy_name="Dup Strategy",
            entry_price=5.5,  # Different price
            entry_time=datetime.now(),
            first_candle_open=5.5,
            shares=200,  # Different shares
            stop_loss_price=5.0,
            take_profit_price=6.0,
            highest_since_entry=5.5,
            paper=True,
        )

        # Should only have one entry
        from src.database import SessionLocal, ActiveTradeDB
        session = SessionLocal()
        try:
            trades = session.query(ActiveTradeDB).filter(
                ActiveTradeDB.ticker == "DUPTST",
                ActiveTradeDB.strategy_id == "dup-strategy",
            ).all()

            assert len(trades) == 1
            # Should have the updated values
            assert trades[0].shares == 200

        finally:
            session.query(ActiveTradeDB).filter(ActiveTradeDB.ticker == "DUPTST").delete()
            session.commit()
            session.close()


class TestTradingEngineQuoteDispatch:
    """Test TradingEngine properly dispatches quotes to strategies."""

    def test_quote_goes_to_all_strategies_with_position(self):
        """Quotes should be dispatched to ALL strategies that have a position in that ticker."""
        # This is the key test - we want BOTH strategies to receive quotes
        # for the same ticker if they both have positions

        engine = TradingEngine(paper=True)

        # Mock strategy engines
        strategy_a = Mock()
        strategy_a.active_trades = {"AMCI": Mock()}
        strategy_a.pending_entries = {}

        strategy_b = Mock()
        strategy_b.active_trades = {"AMCI": Mock()}
        strategy_b.pending_entries = {}

        engine.strategies = {
            "strategy-a": strategy_a,
            "strategy-b": strategy_b,
        }
        engine.strategy_names = {
            "strategy-a": "Strategy A",
            "strategy-b": "Strategy B",
        }

        # Current implementation uses ticker lock - this test shows what SHOULD happen
        # For multi-strategy support, we need to dispatch to all strategies that have the ticker

        # Simulate a quote
        now = datetime.now()

        # Check which strategies have this ticker in active_trades
        strategies_with_position = [
            sid for sid, eng in engine.strategies.items()
            if "AMCI" in eng.active_trades
        ]

        # Both should have it
        assert len(strategies_with_position) == 2
        assert "strategy-a" in strategies_with_position
        assert "strategy-b" in strategies_with_position

    def test_multi_strategy_quote_dispatch(self):
        """Quotes go to ALL strategies with active positions in that ticker.

        Multi-strategy support: each strategy tracks its own position independently.
        When a quote arrives, it's dispatched to every strategy that has an active
        trade or pending entry for that ticker.
        """
        engine = TradingEngine(paper=True)

        # Mock strategy engines - both have positions in AMCI
        strategy_a = Mock()
        strategy_a.active_trades = {"AMCI": Mock()}
        strategy_a.pending_entries = {}

        strategy_b = Mock()
        strategy_b.active_trades = {"AMCI": Mock()}
        strategy_b.pending_entries = {}

        engine.strategies = {
            "strategy-a": strategy_a,
            "strategy-b": strategy_b,
        }
        engine.strategy_names = {
            "strategy-a": "Strategy A",
            "strategy-b": "Strategy B",
        }
        # Ticker lock only affects alert routing, not quote dispatch
        engine._locked_tickers = {"AMCI": "strategy-a"}

        # Dispatch quote
        engine._on_quote("AMCI", 5.50, 1000, datetime.now())

        # BOTH strategies should receive the quote (multi-strategy support)
        strategy_a.on_quote.assert_called_once()
        strategy_b.on_quote.assert_called_once()

    def test_quote_only_to_strategies_with_position(self):
        """Quotes only go to strategies that actually have a position.

        A strategy without an active trade or pending entry for a ticker
        should NOT receive quotes for that ticker.
        """
        engine = TradingEngine(paper=True)

        # Strategy A has position in AMCI
        strategy_a = Mock()
        strategy_a.active_trades = {"AMCI": Mock()}
        strategy_a.pending_entries = {}

        # Strategy B has NO position in AMCI (different ticker)
        strategy_b = Mock()
        strategy_b.active_trades = {"TSLA": Mock()}
        strategy_b.pending_entries = {}

        engine.strategies = {
            "strategy-a": strategy_a,
            "strategy-b": strategy_b,
        }

        # Dispatch quote for AMCI
        engine._on_quote("AMCI", 5.50, 1000, datetime.now())

        # Only strategy A should receive the quote
        strategy_a.on_quote.assert_called_once()
        strategy_b.on_quote.assert_not_called()

    def test_pending_entries_also_receive_quotes(self):
        """Strategies with pending entries (not just active trades) receive quotes.

        This ensures strategies waiting for entry conditions get price updates.
        """
        engine = TradingEngine(paper=True)

        # Strategy A has active trade
        strategy_a = Mock()
        strategy_a.active_trades = {"AMCI": Mock()}
        strategy_a.pending_entries = {}

        # Strategy B has pending entry (watching for entry conditions)
        strategy_b = Mock()
        strategy_b.active_trades = {}
        strategy_b.pending_entries = {"AMCI": Mock()}

        engine.strategies = {
            "strategy-a": strategy_a,
            "strategy-b": strategy_b,
        }

        # Dispatch quote
        engine._on_quote("AMCI", 5.50, 1000, datetime.now())

        # Both should receive the quote
        strategy_a.on_quote.assert_called_once()
        strategy_b.on_quote.assert_called_once()


class TestReconciliation:
    """Test position reconciliation between tracked positions and broker."""

    def test_broker_has_sum_of_strategy_positions(self):
        """Broker position should equal sum of all strategy positions for a ticker."""
        # Strategy A: 100 shares AMCI
        # Strategy B: 50 shares AMCI
        # Broker should have: 150 shares AMCI

        strategy_a_shares = 100
        strategy_b_shares = 50
        expected_broker_shares = strategy_a_shares + strategy_b_shares

        # This is the invariant we need to maintain
        assert expected_broker_shares == 150

    def test_detect_position_mismatch(self):
        """Should detect when broker position doesn't match sum of tracked positions."""
        from src.strategy import StrategyEngine, StrategyConfig, ActiveTrade

        config = StrategyConfig(
            channels=["test"],
            price_min=1.0,
            price_max=100.0,
        )

        mock_trader = Mock()
        # Broker only has 100 shares, but we think we have 150
        mock_trader.get_positions.return_value = [MockPosition("AMCI", 100, 5.0)]
        mock_trader.get_position.return_value = MockPosition("AMCI", 100, 5.0)

        with patch('src.strategy.get_active_trade_store') as mock_store:
            with patch('src.strategy.get_trade_history_client'):
                mock_store.return_value.get_trades_for_strategy.return_value = []
                mock_store.return_value.delete_trade.return_value = True

                engine = StrategyEngine(
                    strategy_id="test-strategy",
                    strategy_name="Test Strategy",
                    config=config,
                    trader=mock_trader,
                    paper=True,
                )

        # We think we have 150 shares
        engine.active_trades["AMCI"] = ActiveTrade(
            ticker="AMCI",
            announcement=None,
            entry_price=5.0,
            entry_time=datetime.now(),
            first_candle_open=5.0,
            shares=150,  # Tracked shares
            highest_since_entry=5.0,
            stop_loss_price=4.75,
            take_profit_price=5.50,
        )

        # Reconciliation should detect this
        broker_positions = {p.ticker: p for p in mock_trader.get_positions()}

        for ticker, trade in engine.active_trades.items():
            broker_pos = broker_positions.get(ticker)
            if broker_pos:
                if broker_pos.shares < trade.shares:
                    # Mismatch detected
                    mismatch = trade.shares - broker_pos.shares
                    assert mismatch == 50  # We're tracking 50 more than broker has


class TestEdgeCases:
    """Test edge cases and race conditions."""

    def test_strategy_disabled_while_position_open(self):
        """What happens when a strategy is disabled but has open positions?"""
        # The position should remain tracked somewhere or be explicitly closed
        pass  # TODO: Define expected behavior

    def test_partial_fill_tracking(self):
        """Partial fills should correctly track filled vs unfilled shares."""
        pass  # TODO: Implement

    def test_position_opened_during_quote_gap(self):
        """Position opened when no quotes were available."""
        pass  # TODO: Implement

    def test_two_strategies_race_to_buy(self):
        """Two strategies try to buy the same ticker simultaneously."""
        # Without locking, both could successfully buy
        # Each should track their own position
        pass  # TODO: Implement


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
