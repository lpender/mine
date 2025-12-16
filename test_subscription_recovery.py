#!/usr/bin/env python3
"""
Test script to verify subscription recovery with limits.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.quote_provider import InsightSentryQuoteProvider
from src.live_trading_service import TradingEngine
from src.strategy import StrategyConfig, StrategyEngine

def test_subscription_recovery():
    """Test that subscription recovery respects limits."""
    # Create quote provider
    provider = InsightSentryQuoteProvider()

    print(f"Max subscriptions allowed: {provider.max_subscriptions}")

    # Subscribe to 3 initially (leaving room for 2 more)
    initial_tickers = ['AAPL', 'GOOGL', 'MSFT']
    for ticker in initial_tickers:
        provider.subscribe_sync(ticker)

    print(f"Initial subscriptions: {len(provider.subscribed_tickers)}/{provider.max_subscriptions}")

    # Simulate a trading engine with more tickers than the limit
    # This would happen during recovery when strategies need more subscriptions
    class MockStrategy:
        def __init__(self, pending_entries, active_trades):
            self.pending_entries = pending_entries
            self.active_trades = active_trades

    class MockEngine:
        def __init__(self, strategies):
            self.strategies = strategies
            self.quote_provider = provider
            self._loop = None  # Simulate no event loop for sync testing

        def _reconcile_subscriptions(self):
            """Copy of the reconciliation logic for testing."""
            if not self.quote_provider:
                return

            # Calculate what we actually need
            needed_tickers = set()
            for strategy_id, strategy in self.strategies.items():
                needed_tickers.update(strategy.pending_entries.keys())
                needed_tickers.update(strategy.active_trades.keys())

            # What does the quote provider think it's subscribed to?
            current_subs = self.quote_provider.subscribed_tickers

            # Find missing (needed but not subscribed)
            missing = needed_tickers - current_subs
            if missing:
                # Check how many more subscriptions we can add
                current_count = len(current_subs)
                max_allowed = self.quote_provider.max_subscriptions
                available_slots = max_allowed - current_count

                if available_slots <= 0:
                    print(f"Subscription drift detected but at limit ({current_count}/{max_allowed}). Cannot subscribe to: {missing}")
                    return

                # Prioritize: active trades first, then pending entries
                active_trade_tickers = set()
                pending_entry_tickers = set()

                for strategy_id, strategy in self.strategies.items():
                    active_trade_tickers.update(strategy.active_trades.keys())
                    pending_entry_tickers.update(strategy.pending_entries.keys())

                # Active trades get priority
                prioritized_missing = []
                for ticker in missing:
                    if ticker in active_trade_tickers:
                        prioritized_missing.insert(0, ticker)  # Add to front (higher priority)
                    elif ticker in pending_entry_tickers:
                        prioritized_missing.append(ticker)  # Add to end (lower priority)

                # Only subscribe to what we can afford
                to_subscribe = prioritized_missing[:available_slots]

                print(f"Subscription drift detected - subscribing to {len(to_subscribe)}/{len(missing)} missing tickers (prioritized): {to_subscribe}")

                for ticker in to_subscribe:
                    self.quote_provider.subscribe_sync(ticker)

                # Log what we couldn't subscribe to due to limits
                skipped = len(missing) - len(to_subscribe)
                if skipped > 0:
                    skipped_tickers = prioritized_missing[available_slots:]
                    print(f"Skipped {skipped} subscriptions due to {max_allowed} symbol limit: {skipped_tickers}")

    # Create mock strategies that need more subscriptions than available
    strategies = {
        'strategy1': MockStrategy(
            pending_entries={'AMZN': {}, 'META': {}},  # Pending entries
            active_trades={'NFLX': {}, 'SPY': {}}      # Active trades (higher priority)
        ),
        'strategy2': MockStrategy(
            pending_entries={'QQQ': {}},                # More pending
            active_trades={'GOOGL': {}}                 # GOOGL is already subscribed
        )
    }

    engine = MockEngine(strategies)
    print(f"\nNeeded tickers: {['AMZN', 'META', 'NFLX', 'SPY', 'QQQ']}")
    print(f"Active trades (priority): {['NFLX', 'SPY']}")
    print(f"Pending entries: {['AMZN', 'META', 'QQQ']}")

    # Run reconciliation
    engine._reconcile_subscriptions()

    print(f"\nFinal subscriptions: {len(provider.subscribed_tickers)}/{provider.max_subscriptions}")
    print(f"Subscribed tickers: {sorted(list(provider.subscribed_tickers))}")

if __name__ == "__main__":
    test_subscription_recovery()
