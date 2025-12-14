import os
import time
import random
import requests
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict, Any
from ..models import OHLCVBar
from .base import OHLCVDataProvider


class PolygonProvider(OHLCVDataProvider):
    """OHLCV data provider using Polygon.io (Massive.com) API."""

    BASE_URL = "https://api.polygon.io"

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limit_delay: float = 12.0,
        max_retries: int = 8,
        timeout_s: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")
        self._rate_limit_delay = float(os.getenv("MASSIVE_RATE_LIMIT_DELAY", rate_limit_delay))
        self.max_retries = int(os.getenv("MASSIVE_MAX_RETRIES", max_retries))
        self.timeout_s = float(os.getenv("MASSIVE_TIMEOUT_S", timeout_s))
        self.backoff_base_s = float(os.getenv("MASSIVE_BACKOFF_BASE_S", "2.0"))
        self.backoff_cap_s = float(os.getenv("MASSIVE_BACKOFF_CAP_S", "120.0"))

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

    def _parse_retry_after_seconds(self, response: requests.Response) -> Optional[float]:
        ra = response.headers.get("Retry-After")
        if not ra:
            return None
        ra = ra.strip()
        try:
            return float(ra)
        except ValueError:
            try:
                dt = parsedate_to_datetime(ra)
                return max(0.0, (dt - datetime.now(tz=dt.tzinfo)).total_seconds())
            except Exception:
                return None

    def _redact_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        redacted = dict(params)
        if "apiKey" in redacted:
            redacted["apiKey"] = "REDACTED"
        return redacted

    def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timespan: str = "minute",
    ) -> Optional[List[OHLCVBar]]:
        if not self.api_key:
            raise ValueError("Polygon API key not set. Set POLYGON_API_KEY or MASSIVE_API_KEY in .env")

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        print(f"[Polygon] Fetching {ticker} from {start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}")

        url = f"{self.BASE_URL}/v2/aggs/ticker/{ticker}/range/1/{timespan}/{start_ms}/{end_ms}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": self.api_key,
        }

        safe_params = self._redact_params(params)

        for attempt in range(self.max_retries):
            try:
                self._sleep_for_rate_limit()
                response = self._session.get(url, params=params, timeout=self.timeout_s)
                self._bump_next_allowed_time()

                if response.status_code == 429:
                    retry_after = self._parse_retry_after_seconds(response)
                    exp_backoff = min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                    wait_time = max(self._rate_limit_delay, retry_after or 0.0, exp_backoff)
                    wait_time = min(self.backoff_cap_s, wait_time + random.uniform(0.0, min(1.0, wait_time * 0.25)))

                    if attempt < self.max_retries - 1:
                        print(f"[Polygon] Rate limited for {ticker}, waiting {wait_time:.1f}s... (attempt {attempt+1}/{self.max_retries})")
                        self._bump_next_allowed_time(wait_time)
                        continue

                    print(f"[Polygon] Rate limit exceeded for {ticker} after {self.max_retries} retries")
                    return None  # None = retry later

                if response.status_code != 200:
                    print(f"[Polygon] Error fetching {ticker}: {response.status_code} {response.reason}")
                    return None  # None = retry later

                data = response.json()

                if data.get("status") != "OK" or "results" not in data:
                    print(f"[Polygon] No data for {ticker}: status={data.get('status')}, results_count={data.get('resultsCount', 0)}")
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

                return bars

            except requests.RequestException as e:
                if attempt < self.max_retries - 1:
                    exp_backoff = min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                    wait_time = max(self._rate_limit_delay, exp_backoff)
                    wait_time = min(self.backoff_cap_s, wait_time + random.uniform(0.0, min(1.0, wait_time * 0.25)))
                    print(f"[Polygon] Request error for {ticker}: {e} (retrying in {wait_time:.1f}s)")
                    self._bump_next_allowed_time(wait_time)
                    continue
                print(f"[Polygon] Error fetching {ticker}: {e}")
                return None  # None = retry later

        return None  # None = retry later (exhausted retries)
