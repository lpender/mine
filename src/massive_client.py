import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from .models import OHLCVBar


class MassiveClient:
    """Client for fetching OHLCV data from Massive.com (formerly Polygon.io)."""

    BASE_URL = "https://api.polygon.io"  # Massive uses same API structure

    def __init__(self, api_key: Optional[str] = None, cache_dir: str = "data/ohlcv", rate_limit_delay: float = 0.25):
        self.api_key = api_key or os.getenv("MASSIVE_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limit_delay = rate_limit_delay  # seconds between API calls
        self._last_request_time = 0.0

    def _get_cache_path(self, ticker: str, start: datetime, end: datetime) -> Path:
        """Generate cache file path for a specific ticker and time range."""
        date_str = start.strftime("%Y%m%d_%H%M")
        return self.cache_dir / f"{ticker}_{date_str}.parquet"

    def _load_from_cache(self, ticker: str, start: datetime, end: datetime) -> Optional[List[OHLCVBar]]:
        """Try to load data from cache."""
        cache_path = self._get_cache_path(ticker, start, end)
        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                return self._df_to_bars(df)
            except Exception:
                return None
        return None

    def _save_to_cache(self, ticker: str, start: datetime, end: datetime, bars: List[OHLCVBar]):
        """Save data to cache."""
        if not bars:
            return

        cache_path = self._get_cache_path(ticker, start, end)
        df = pd.DataFrame([{
            'timestamp': bar.timestamp,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume,
            'vwap': bar.vwap,
        } for bar in bars])
        df.to_parquet(cache_path, index=False)

    def _df_to_bars(self, df: pd.DataFrame) -> List[OHLCVBar]:
        """Convert DataFrame to list of OHLCVBar objects."""
        bars = []
        for _, row in df.iterrows():
            bars.append(OHLCVBar(
                timestamp=pd.to_datetime(row['timestamp']),
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=int(row['volume']),
                vwap=float(row['vwap']) if pd.notna(row.get('vwap')) else None,
            ))
        return bars

    def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        multiplier: int = 1,
        timespan: str = "minute",
        use_cache: bool = True,
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV bars from Massive.com API.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            start: Start datetime
            end: End datetime
            multiplier: Size of timespan multiplier
            timespan: Time window (minute, hour, day, etc.)
            use_cache: Whether to use cached data

        Returns:
            List of OHLCVBar objects
        """
        # Try cache first
        if use_cache:
            cached = self._load_from_cache(ticker, start, end)
            if cached:
                return cached

        if not self.api_key:
            raise ValueError("MASSIVE_API_KEY not set. Please set it in .env or pass to constructor.")

        # Rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)

        # Convert to millisecond timestamps
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        url = f"{self.BASE_URL}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start_ms}/{end_ms}"
        params = {
            "apiKey": self.api_key,
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
        }

        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                self._last_request_time = time.time()
                response = requests.get(url, params=params, timeout=30)

                # Handle rate limiting with retry
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)  # exponential backoff
                        print(f"Rate limited for {ticker}, waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"Rate limit exceeded for {ticker} after {max_retries} retries")
                        return []

                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK" or "results" not in data:
                    return []

                bars = []
                for result in data["results"]:
                    bars.append(OHLCVBar(
                        timestamp=datetime.fromtimestamp(result["t"] / 1000),
                        open=result["o"],
                        high=result["h"],
                        low=result["l"],
                        close=result["c"],
                        volume=result["v"],
                        vwap=result.get("vw"),
                    ))

                # Cache the results
                if use_cache and bars:
                    self._save_to_cache(ticker, start, end, bars)

                return bars

            except requests.RequestException as e:
                if attempt < max_retries - 1 and "429" in str(e):
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Rate limited for {ticker}, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                print(f"Error fetching OHLCV for {ticker}: {e}")
                return []

        return []

    def fetch_after_announcement(
        self,
        ticker: str,
        announcement_time: datetime,
        window_minutes: int = 30,
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV data for a window after an announcement.

        Args:
            ticker: Stock ticker symbol
            announcement_time: When the announcement was made
            window_minutes: How many minutes of data to fetch

        Returns:
            List of OHLCVBar objects
        """
        end_time = announcement_time + timedelta(minutes=window_minutes)
        return self.fetch_ohlcv(ticker, announcement_time, end_time)


def create_client(api_key: Optional[str] = None, cache_dir: str = "data/ohlcv") -> MassiveClient:
    """Factory function to create a MassiveClient instance."""
    return MassiveClient(api_key=api_key, cache_dir=cache_dir)
