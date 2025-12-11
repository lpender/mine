"""
InsightSentry API client for symbol search and real-time quotes.

Usage:
    from src.insightsentry import get_quote

    price = get_quote("AAPL")  # Returns current price or None
"""

import os
import requests
from typing import Optional
from functools import lru_cache

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
BASE_URL = "https://insightsentry.p.rapidapi.com/v3"
HEADERS = {
    "x-rapidapi-host": "insightsentry.p.rapidapi.com",
    "x-rapidapi-key": RAPIDAPI_KEY or "",
}


@lru_cache(maxsize=100)
def search_symbol(ticker: str) -> Optional[str]:
    """
    Search for a symbol and return the exchange:symbol code.

    Args:
        ticker: Stock ticker (e.g., "AAPL")

    Returns:
        Exchange:symbol code (e.g., "NASDAQ:AAPL") or None
    """
    if not RAPIDAPI_KEY:
        print("Warning: RAPIDAPI_KEY not set")
        return None

    try:
        resp = requests.get(
            f"{BASE_URL}/symbols/search",
            headers=HEADERS,
            params={"query": ticker, "type": "stock", "country": "US", "page": 1},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        # Get first STOCK result with matching symbol name
        results = data.get("symbols", [])
        for result in results:
            # Prefer exact match on major exchange
            if result.get("name") == ticker and result.get("type") == "STOCK":
                code = result.get("code")
                if code and not code.startswith("BOATS:"):  # Skip dark pools
                    return code
        return None
    except Exception as e:
        print(f"InsightSentry search error: {e}")
        return None


def get_quote(ticker: str) -> Optional[float]:
    """
    Get the current price for a ticker.

    Args:
        ticker: Stock ticker (e.g., "AAPL")

    Returns:
        Current price or None if unavailable
    """
    details = get_quote_details(ticker)
    if details:
        return details.get("last_price")
    return None


def get_quote_details(ticker: str) -> Optional[dict]:
    """
    Get detailed quote info for a ticker.

    Args:
        ticker: Stock ticker (e.g., "AAPL")

    Returns:
        Quote dict with last_price, bid, ask, volume, etc. or None
    """
    code = search_symbol(ticker)
    if not code:
        print(f"Could not find exchange for {ticker}")
        return None

    try:
        resp = requests.get(
            f"{BASE_URL}/symbols/quotes",
            headers=HEADERS,
            params={"codes": code},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        # Data is an array, find matching code
        quotes = data.get("data", [])
        for quote in quotes:
            if quote.get("code") == code:
                return quote
        return None
    except Exception as e:
        print(f"InsightSentry quote error: {e}")
        return None
