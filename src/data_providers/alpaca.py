import os
import time
import random
import requests
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from zoneinfo import ZoneInfo
from ..models import OHLCVBar
from .base import OHLCVDataProvider

# Assume naive timestamps are Eastern Time
ET_TZ = ZoneInfo("America/New_York")


class AlpacaProvider(OHLCVDataProvider):
    """OHLCV data provider using Alpaca Markets API."""

    BASE_URL = "https://data.alpaca.markets"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        rate_limit_delay: float = 0.3,
        max_retries: int = 5,
        timeout_s: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self._rate_limit_delay = float(os.getenv("ALPACA_RATE_LIMIT_DELAY", rate_limit_delay))
        self.max_retries = int(os.getenv("ALPACA_MAX_RETRIES", max_retries))
        self.timeout_s = float(os.getenv("ALPACA_TIMEOUT_S", timeout_s))
        self.backoff_base_s = float(os.getenv("ALPACA_BACKOFF_BASE_S", "1.0"))
        self.backoff_cap_s = float(os.getenv("ALPACA_BACKOFF_CAP_S", "60.0"))

        self._session = requests.Session()
        self._next_allowed_time = 0.0

    @property
    def rate_limit_delay(self) -> float:
        return self._rate_limit_delay

    def supports_extended_hours(self) -> bool:
        return True

    def _sleep_for_rate_limit(self):
        now = time.time()
        if now < self._next_allowed_time:
            time.sleep(self._next_allowed_time - now)

    def _bump_next_allowed_time(self, extra_delay_s: float = 0.0):
        now = time.time()
        base = now + self._rate_limit_delay
        extra = now + max(0.0, float(extra_delay_s))
        self._next_allowed_time = max(self._next_allowed_time, base, extra)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timespan: str = "minute",
    ) -> Optional[List[OHLCVBar]]:
        if not self.api_key or not self.secret_key:
            raise ValueError("Alpaca API credentials not set. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")

        # Convert timespan to Alpaca format
        timeframe_map = {
            "minute": "1Min",
            "hour": "1Hour",
            "day": "1Day",
        }
        timeframe = timeframe_map.get(timespan, "1Min")

        # Convert naive timestamps (assumed ET) to UTC for Alpaca API
        if start.tzinfo is None:
            start_utc = start.replace(tzinfo=ET_TZ).astimezone(timezone.utc)
        else:
            start_utc = start.astimezone(timezone.utc)

        if end.tzinfo is None:
            end_utc = end.replace(tzinfo=ET_TZ).astimezone(timezone.utc)
        else:
            end_utc = end.astimezone(timezone.utc)

        # Format timestamps as RFC3339
        start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        print(f"[Alpaca] Fetching {ticker} from {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")

        url = f"{self.BASE_URL}/v2/stocks/{ticker}/bars"
        params = {
            "start": start_str,
            "end": end_str,
            "timeframe": timeframe,
            "adjustment": "split",  # Split-adjusted data
            "limit": 10000,
            "sort": "asc",
        }

        all_bars = []
        next_page_token = None

        for attempt in range(self.max_retries):
            try:
                self._sleep_for_rate_limit()

                if next_page_token:
                    params["page_token"] = next_page_token
                elif "page_token" in params:
                    del params["page_token"]

                response = self._session.get(
                    url,
                    params=params,
                    headers=self._get_headers(),
                    timeout=self.timeout_s,
                )
                self._bump_next_allowed_time()

                if response.status_code == 429:
                    exp_backoff = min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                    wait_time = exp_backoff + random.uniform(0.0, 1.0)

                    if attempt < self.max_retries - 1:
                        print(f"[Alpaca] Rate limited for {ticker}, waiting {wait_time:.1f}s... (attempt {attempt+1}/{self.max_retries})")
                        self._bump_next_allowed_time(wait_time)
                        continue

                    print(f"[Alpaca] Rate limit exceeded for {ticker} after {self.max_retries} retries")
                    return None  # None = retry later

                if response.status_code != 200:
                    print(f"[Alpaca] Error fetching {ticker}: {response.status_code} {response.reason}")
                    if response.text:
                        print(f"[Alpaca] Response: {response.text[:200]}")
                    return None  # None = retry later

                data = response.json()
                bars_data = data.get("bars", [])

                if not bars_data:
                    if not all_bars:
                        print(f"[Alpaca] No data for {ticker}")
                    return all_bars

                for bar in bars_data:
                    # Parse ISO timestamp (Alpaca returns UTC)
                    ts_str = bar["t"]
                    if ts_str.endswith("Z"):
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    else:
                        ts = datetime.fromisoformat(ts_str)

                    # Convert from UTC to ET, then strip timezone for consistency
                    if ts.tzinfo is not None:
                        ts = ts.astimezone(ET_TZ).replace(tzinfo=None)

                    all_bars.append(OHLCVBar(
                        timestamp=ts,
                        open=bar["o"],
                        high=bar["h"],
                        low=bar["l"],
                        close=bar["c"],
                        volume=bar["v"],
                        vwap=bar.get("vw"),
                    ))

                # Check for pagination
                next_page_token = data.get("next_page_token")
                if next_page_token:
                    attempt = 0  # Reset retry counter for pagination
                    continue
                else:
                    return all_bars

            except requests.RequestException as e:
                if attempt < self.max_retries - 1:
                    exp_backoff = min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                    wait_time = exp_backoff + random.uniform(0.0, 1.0)
                    print(f"[Alpaca] Request error for {ticker}: {e} (retrying in {wait_time:.1f}s)")
                    self._bump_next_allowed_time(wait_time)
                    continue
                print(f"[Alpaca] Error fetching {ticker}: {e}")
                return None  # None = retry later

        return None  # None = retry later (exhausted retries)
