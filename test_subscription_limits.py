#!/usr/bin/env python3
"""
Test script to verify subscription limit enforcement.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.quote_provider import InsightSentryQuoteProvider

def test_subscription_limits():
    """Test that subscription limits are enforced."""
    # Create quote provider
    provider = InsightSentryQuoteProvider()

    print(f"Max subscriptions allowed: {provider.max_subscriptions}")

    # Try to subscribe to more tickers than the limit
    test_tickers = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'NVDA', 'AMZN', 'META', 'NFLX', 'SPY', 'QQQ']

    print("\nTesting subscription limits...")
    for ticker in test_tickers:
        print(f"Attempting to subscribe to {ticker}...")
        initial_count = len(provider.subscribed_tickers)
        provider.subscribe_sync(ticker)
        current_subs = len(provider.subscribed_tickers)
        print(f"Current subscriptions: {current_subs}/{provider.max_subscriptions} - {list(provider.subscribed_tickers)}")

        # Check if the subscription was rejected
        if current_subs == initial_count and ticker not in provider.subscribed_tickers:
            print(f"✅ {ticker} was correctly rejected due to limit")
        elif current_subs > initial_count:
            print(f"✅ {ticker} was successfully added")
        else:
            print(f"⚠️  {ticker} was already subscribed")

    print(f"\nFinal state: {len(provider.subscribed_tickers)} subscriptions")
    print(f"Subscribed tickers: {list(provider.subscribed_tickers)}")

if __name__ == "__main__":
    test_subscription_limits()
