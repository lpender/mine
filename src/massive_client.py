import json
import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
from .models import OHLCVBar, Announcement


class MassiveClient:
    """Client for fetching OHLCV data from Massive.com."""

    BASE_URL = "https://api.massive.com"

    def __init__(self, api_key: Optional[str] = None, cache_dir: str = "data/ohlcv", rate_limit_delay: float = 12.0):
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

        # Log the request with human-readable times
        print(f"Fetching {ticker} from {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")

        url = f"{self.BASE_URL}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start_ms}/{end_ms}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": self.api_key,
        }

        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                self._last_request_time = time.time()
                response = requests.get(url, params=params, timeout=30)

                # Build full URL for debugging
                full_url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

                # Handle rate limiting with retry
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)  # exponential backoff
                        print(f"Rate limited for {ticker}, waiting {wait_time}s...")
                        print(f"  URL: {full_url}")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"Rate limit exceeded for {ticker} after {max_retries} retries")
                        print(f"  URL: {full_url}")
                        return []

                if response.status_code != 200:
                    print(f"Error fetching OHLCV for {ticker}: {response.status_code} {response.reason}")
                    print(f"  URL: {full_url}")
                    print(f"  Response: {response.text[:500]}")
                    return []

                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK" or "results" not in data:
                    print(f"No data for {ticker}: status={data.get('status')}, results_count={data.get('resultsCount', 0)}")
                    print(f"  URL: {full_url}")
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
                full_url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
                if attempt < max_retries - 1 and "429" in str(e):
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"Rate limited for {ticker}, waiting {wait_time}s...")
                    print(f"  URL: {full_url}")
                    time.sleep(wait_time)
                    continue
                print(f"Error fetching OHLCV for {ticker}: {e}")
                print(f"  URL: {full_url}")
                return []

        return []

    def fetch_after_announcement(
        self,
        ticker: str,
        announcement_time: datetime,
        window_minutes: int = 120,
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

    def _get_announcements_path(self) -> Path:
        """Get path to the announcements JSON file."""
        return self.cache_dir / "announcements.json"

    def save_announcements(self, announcements: List[Announcement]):
        """Save announcements to a JSON file, merging with existing data."""
        existing = self.load_announcements()

        # Create a set of existing keys for deduplication
        existing_keys = {(a.ticker, a.timestamp.isoformat()) for a in existing}

        # Add new announcements
        for ann in announcements:
            key = (ann.ticker, ann.timestamp.isoformat())
            if key not in existing_keys:
                existing.append(ann)
                existing_keys.add(key)

        # Save to file
        data = []
        for ann in existing:
            data.append({
                'ticker': ann.ticker,
                'timestamp': ann.timestamp.isoformat(),
                'price_threshold': ann.price_threshold,
                'headline': ann.headline,
                'country': ann.country,
                'float_shares': ann.float_shares,
                'io_percent': ann.io_percent,
                'market_cap': ann.market_cap,
                'reg_sho': ann.reg_sho,
                'high_ctb': ann.high_ctb,
                'short_interest': ann.short_interest,
                'channel': ann.channel,
            })

        with open(self._get_announcements_path(), 'w') as f:
            json.dump(data, f, indent=2)

    def load_announcements(self) -> List[Announcement]:
        """Load all saved announcements from the JSON file."""
        path = self._get_announcements_path()
        if not path.exists():
            return []

        try:
            with open(path, 'r') as f:
                data = json.load(f)

            announcements = []
            for item in data:
                announcements.append(Announcement(
                    ticker=item['ticker'],
                    timestamp=datetime.fromisoformat(item['timestamp']),
                    price_threshold=item['price_threshold'],
                    headline=item.get('headline', ''),
                    country=item.get('country', 'UNKNOWN'),
                    float_shares=item.get('float_shares'),
                    io_percent=item.get('io_percent'),
                    market_cap=item.get('market_cap'),
                    reg_sho=item.get('reg_sho', False),
                    high_ctb=item.get('high_ctb', False),
                    short_interest=item.get('short_interest'),
                    channel=item.get('channel'),
                ))
            return announcements
        except Exception:
            return []

    def load_all_cached_data(self) -> Tuple[List[Announcement], dict]:
        """
        Load all cached announcements and their OHLCV data.

        Returns:
            Tuple of (announcements, bars_by_announcement)
        """
        announcements = self.load_announcements()
        bars_by_announcement = {}

        for ann in announcements:
            key = (ann.ticker, ann.timestamp)
            bars = self._load_from_cache(ann.ticker, ann.timestamp, ann.timestamp + timedelta(minutes=60))
            if bars:
                bars_by_announcement[key] = bars

        return announcements, bars_by_announcement


def create_client(api_key: Optional[str] = None, cache_dir: str = "data/ohlcv") -> MassiveClient:
    """Factory function to create a MassiveClient instance."""
    return MassiveClient(api_key=api_key, cache_dir=cache_dir)
