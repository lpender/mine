#!/usr/bin/env python3
"""
Test script to verify oversubscription handling.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.quote_provider import InsightSentryQuoteProvider

def test_oversubscription_handling():
    """Test that oversubscription is handled properly."""
    provider = InsightSentryQuoteProvider()

    print(f"Max subscriptions allowed: {provider.max_subscriptions}")

    # Subscribe to all 5 available slots
    initial_tickers = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'NVDA']
    for ticker in initial_tickers:
        provider.subscribe_sync(ticker)

    print(f"Initial subscriptions: {len(provider.subscribed_tickers)}/{provider.max_subscriptions}")

    # Try to subscribe to more - should be rejected but queued
    extra_tickers = ['AMZN', 'META', 'NFLX']
    print(f"\nTrying to subscribe to extra tickers: {extra_tickers}")

    for ticker in extra_tickers:
        print(f"Attempting to subscribe to {ticker}...")
        provider.subscribe_sync(ticker)
        current_subs = len(provider.subscribed_tickers)
        print(f"Current subscriptions: {current_subs}/{provider.max_subscriptions}")

    # Simulate what happens when slots free up
    print(f"\nSimulating slot freeing up (unsubscribing from NVDA)...")
    provider.unsubscribe_sync('NVDA')
    print(f"After unsubscribing NVDA: {len(provider.subscribed_tickers)}/{provider.max_subscriptions}")

    # Now try to subscribe to one of the queued tickers
    print(f"\nTrying to subscribe to AMZN now that slot is free...")
    provider.subscribe_sync('AMZN')
    print(f"Final subscriptions: {len(provider.subscribed_tickers)}/{provider.max_subscriptions}")

    print(f"Subscribed tickers: {sorted(list(provider.subscribed_tickers))}")

if __name__ == "__main__":
    test_oversubscription_handling()
