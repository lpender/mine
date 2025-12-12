import json
import os
import time
import random
import requests
import pandas as pd
from datetime import datetime, timedelta, date as date_type, time as time_type
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from .models import (
    OHLCVBar,
    Announcement,
    get_market_session,
    MARKET_OPEN,
    ET_TZ,
)

try:
    import pandas_market_calendars as _mcal  # type: ignore
except Exception:  # pragma: no cover
    _mcal = None

_NYSE_CAL = None


def _is_weekend(d: date_type) -> bool:
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def _get_nyse_calendar():
    global _NYSE_CAL
    if _NYSE_CAL is not None:
        return _NYSE_CAL
    if _mcal is None:
        return None
    try:
        _NYSE_CAL = _mcal.get_calendar("NYSE")
        return _NYSE_CAL
    except Exception:
        return None


def _first_trading_day_on_or_after(d: date_type) -> date_type:
    """
    First NYSE trading day on/after `d`.
    Falls back to weekday-only logic if market calendar library isn't installed.
    """
    cal = _get_nyse_calendar()
    if cal is not None:
        # Look ahead up to 2 weeks to handle holiday clusters.
        days = cal.valid_days(start_date=d, end_date=d + timedelta(days=14))
        if len(days) > 0:
            # valid_days returns tz-aware timestamps (UTC). We only need the session date.
            return days[0].to_pydatetime().date()

    # Fallback: skip weekends only
    nd = d
    while _is_weekend(nd):
        nd = nd + timedelta(days=1)
    return nd


def _first_trading_day_after(d: date_type) -> date_type:
    """First NYSE trading day strictly after `d`."""
    return _first_trading_day_on_or_after(d + timedelta(days=1))


def _combine_et(d: date_type, t: time_type, naive_input: bool) -> datetime:
    dt = datetime.combine(d, t, tzinfo=ET_TZ)
    return dt.replace(tzinfo=None) if naive_input else dt


class MassiveClient:
    """Client for fetching OHLCV data from Massive.com."""

    BASE_URL = "https://api.massive.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: str = "data/ohlcv",
        rate_limit_delay: float = 12.0,
        max_retries: int = 8,
        timeout_s: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("MASSIVE_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Minimum seconds between *all* HTTP attempts (including retries)
        self.rate_limit_delay = float(os.getenv("MASSIVE_RATE_LIMIT_DELAY", rate_limit_delay))
        self.max_retries = int(os.getenv("MASSIVE_MAX_RETRIES", max_retries))
        self.timeout_s = float(os.getenv("MASSIVE_TIMEOUT_S", timeout_s))

        self._session = requests.Session()
        self._next_allowed_time = 0.0  # epoch seconds

    def _get_cache_path(self, ticker: str, start: datetime, end: datetime) -> Path:
        """Generate cache file path for a specific ticker and time range."""
        date_str = start.strftime("%Y%m%d_%H%M")
        return self.cache_dir / f"{ticker}_{date_str}.parquet"

    def _sleep_for_rate_limit(self):
        now = time.time()
        if now < self._next_allowed_time:
            time.sleep(self._next_allowed_time - now)

    def _bump_next_allowed_time(self, extra_delay_s: float = 0.0):
        """
        Ensure there's at least `rate_limit_delay` between HTTP attempts, plus optional extra delay.
        """
        now = time.time()
        base = now + self.rate_limit_delay
        extra = now + max(0.0, float(extra_delay_s))
        self._next_allowed_time = max(self._next_allowed_time, base, extra)

    def _parse_retry_after_seconds(self, response: requests.Response) -> Optional[float]:
        ra = response.headers.get("Retry-After")
        if not ra:
            return None
        ra = ra.strip()
        # Retry-After can be seconds or HTTP-date
        try:
            return float(ra)
        except ValueError:
            try:
                dt = parsedate_to_datetime(ra)
                # parsedate_to_datetime returns aware datetime (usually UTC)
                return max(0.0, (dt - datetime.now(tz=dt.tzinfo)).total_seconds())
            except Exception:
                return None

    def _redact_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        redacted = dict(params)
        if "apiKey" in redacted:
            redacted["apiKey"] = "REDACTED"
        return redacted

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
        """Save data to cache (including empty results to distinguish from unfetched)."""
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
            if cached is not None:
                return cached

        if not self.api_key:
            raise ValueError("MASSIVE_API_KEY not set. Please set it in .env or pass to constructor.")

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

        safe_params = self._redact_params(params)

        # Exponential backoff baseline (in addition to the global min interval)
        backoff_base_s = float(os.getenv("MASSIVE_BACKOFF_BASE_S", "2.0"))
        backoff_cap_s = float(os.getenv("MASSIVE_BACKOFF_CAP_S", "120.0"))

        for attempt in range(self.max_retries):
            try:
                self._sleep_for_rate_limit()
                response = self._session.get(url, params=params, timeout=self.timeout_s)
                # After any attempt, enforce the base spacing before the next one
                self._bump_next_allowed_time()

                # Handle rate limiting with retry
                if response.status_code == 429:
                    retry_after = self._parse_retry_after_seconds(response)
                    exp_backoff = min(backoff_cap_s, backoff_base_s * (2 ** attempt))
                    wait_time = max(self.rate_limit_delay, retry_after or 0.0, exp_backoff)
                    # jitter to avoid synchronized retries
                    wait_time = min(backoff_cap_s, wait_time + random.uniform(0.0, min(1.0, wait_time * 0.25)))

                    if attempt < self.max_retries - 1:
                        print(f"Rate limited for {ticker}, waiting {wait_time:.1f}s... (attempt {attempt+1}/{self.max_retries})")
                        print(f"  URL: {url}")
                        print(f"  Params: {safe_params}")
                        self._bump_next_allowed_time(wait_time)
                        continue

                    print(f"Rate limit exceeded for {ticker} after {self.max_retries} retries")
                    print(f"  URL: {url}")
                    print(f"  Params: {safe_params}")
                    return []

                if response.status_code != 200:
                    print(f"Error fetching OHLCV for {ticker}: {response.status_code} {response.reason}")
                    print(f"  URL: {url}")
                    print(f"  Params: {safe_params}")
                    print(f"  Response: {response.text[:500]}")
                    return []

                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK" or "results" not in data:
                    print(f"No data for {ticker}: status={data.get('status')}, results_count={data.get('resultsCount', 0)}")
                    print(f"  URL: {url}")
                    print(f"  Params: {safe_params}")
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

                # Cache the results (including empty results to distinguish from unfetched)
                if use_cache:
                    self._save_to_cache(ticker, start, end, bars)

                return bars

            except requests.RequestException as e:
                if attempt < self.max_retries - 1:
                    exp_backoff = min(backoff_cap_s, backoff_base_s * (2 ** attempt))
                    wait_time = max(self.rate_limit_delay, exp_backoff)
                    wait_time = min(backoff_cap_s, wait_time + random.uniform(0.0, min(1.0, wait_time * 0.25)))
                    print(f"Request error for {ticker}: {e} (retrying in {wait_time:.1f}s, attempt {attempt+1}/{self.max_retries})")
                    print(f"  URL: {url}")
                    print(f"  Params: {safe_params}")
                    self._bump_next_allowed_time(wait_time)
                    continue
                print(f"Error fetching OHLCV for {ticker}: {e}")
                print(f"  URL: {url}")
                print(f"  Params: {safe_params}")
                return []

        return []

    def fetch_after_announcement(
        self,
        ticker: str,
        announcement_time: datetime,
        window_minutes: int = 120,
        use_cache: bool = True,
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV data for a window after an announcement.

        For postmarket announcements, starts from the next market open.
        For premarket announcements, starts from market open of the same day.
        For market announcements, starts from the announcement time.

        Args:
            ticker: Stock ticker symbol
            announcement_time: When the announcement was made
            window_minutes: How many minutes of data to fetch
            use_cache: Whether to use cached parquet data

        Returns:
            List of OHLCVBar objects
        """

        start_time = self.get_effective_start_time(announcement_time)

        # If the effective start time is in the future (e.g. premarket before 9:30,
        # postmarket pointing to next session, weekend), skip the API call.
        now = datetime.now(tz=ET_TZ)
        now_cmp = now.replace(tzinfo=None) if start_time.tzinfo is None else now
        if start_time >= now_cmp:
            print(
                f"Skipping OHLCV fetch for {ticker}: effective start {start_time} is in the future "
                f"(market likely closed)."
            )
            return []

        end_time = start_time + timedelta(minutes=window_minutes)
        # Don't request beyond "now" (helps when market is open but window extends into the future)
        if end_time > now_cmp:
            end_time = now_cmp

        return self.fetch_ohlcv(ticker, start_time, end_time, use_cache=use_cache)

    def get_effective_start_time(self, announcement_time: datetime) -> datetime:
        """
        Compute the effective OHLCV start time for an announcement.

        - Postmarket: next market open
        - Premarket: same-day market open
        - Market: announcement time
        """
        # Convert to Eastern Time if needed (and remember whether input was naive)
        naive_input = announcement_time.tzinfo is None
        et_time = announcement_time if naive_input else announcement_time.astimezone(ET_TZ)

        # If the calendar day isn't a trading day (weekend/holiday), roll forward to next session open.
        # If pandas_market_calendars is installed, this handles NYSE holidays; otherwise weekends only.
        trading_day = _first_trading_day_on_or_after(et_time.date())
        if trading_day != et_time.date():
            return _combine_et(trading_day, MARKET_OPEN, naive_input)

        session = get_market_session(et_time)

        if session == "market":
            return announcement_time

        if session == "premarket":
            # Start from market open of the same day (may still be in the future; fetch will skip)
            return _combine_et(et_time.date(), MARKET_OPEN, naive_input)

        # For postmarket, and for "closed" (overnight) times, start from the next market open.
        # - "closed" includes 20:00-04:00; before 09:30 we want same-day open, after 16:00 we want next day open.
        if session in ("postmarket", "closed"):
            t = et_time.time()
            if t < MARKET_OPEN:
                # Overnight before the bell: same-day open
                day = _first_trading_day_on_or_after(et_time.date())
                return _combine_et(day, MARKET_OPEN, naive_input)

            # After-hours or late evening: next weekday open
            next_day = _first_trading_day_after(et_time.date())
            return _combine_et(next_day, MARKET_OPEN, naive_input)

        # Fallback: treat unknown session as "next open"
        next_day = _first_trading_day_after(et_time.date())
        return _combine_et(next_day, MARKET_OPEN, naive_input)

    def _get_announcements_path(self) -> Path:
        """Get path to the announcements JSON file."""
        return self.cache_dir / "announcements.json"

    def save_announcements(self, announcements: List[Announcement]):
        """Save announcements to a JSON file, merging with existing data (clobbers duplicates)."""
        existing = self.load_announcements()

        # Use dict keyed by (ticker, timestamp) to allow updates/clobber
        by_key = {(a.ticker, a.timestamp.isoformat()): a for a in existing}

        # Merge: new announcements clobber existing ones with same key
        for ann in announcements:
            key = (ann.ticker, ann.timestamp.isoformat())
            by_key[key] = ann

        # Save to file
        data = []
        for ann in by_key.values():
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
                'author': ann.author,
                'finbert_label': ann.finbert_label,
                'finbert_score': ann.finbert_score,
                'finbert_pos': ann.finbert_pos,
                'finbert_neg': ann.finbert_neg,
                'finbert_neu': ann.finbert_neu,
                'headline_is_financing': ann.headline_is_financing,
                'headline_financing_type': ann.headline_financing_type,
                'headline_financing_tags': ann.headline_financing_tags,
                'prev_close': ann.prev_close,
                'regular_open': ann.regular_open,
                'premarket_gap_pct': ann.premarket_gap_pct,
                'premarket_volume': ann.premarket_volume,
                'premarket_dollar_volume': ann.premarket_dollar_volume,
                # Scanner fields
                'scanner_gain_pct': ann.scanner_gain_pct,
                'is_nhod': ann.is_nhod,
                'is_nsh': ann.is_nsh,
                'rvol': ann.rvol,
                'mention_count': ann.mention_count,
                'has_news': ann.has_news,
                'green_bars': ann.green_bars,
                'bar_minutes': ann.bar_minutes,
                'scanner_test': ann.scanner_test,
                'scanner_after_lull': ann.scanner_after_lull,
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
                    author=item.get('author'),
                    finbert_label=item.get('finbert_label'),
                    finbert_score=item.get('finbert_score'),
                    finbert_pos=item.get('finbert_pos'),
                    finbert_neg=item.get('finbert_neg'),
                    finbert_neu=item.get('finbert_neu'),
                    headline_is_financing=item.get('headline_is_financing'),
                    headline_financing_type=item.get('headline_financing_type'),
                    headline_financing_tags=item.get('headline_financing_tags'),
                    prev_close=item.get('prev_close'),
                    regular_open=item.get('regular_open'),
                    premarket_gap_pct=item.get('premarket_gap_pct'),
                    premarket_volume=item.get('premarket_volume'),
                    premarket_dollar_volume=item.get('premarket_dollar_volume'),
                    # Scanner fields
                    scanner_gain_pct=item.get('scanner_gain_pct'),
                    is_nhod=item.get('is_nhod', False),
                    is_nsh=item.get('is_nsh', False),
                    rvol=item.get('rvol'),
                    mention_count=item.get('mention_count'),
                    has_news=item.get('has_news', True),
                    green_bars=item.get('green_bars'),
                    bar_minutes=item.get('bar_minutes'),
                    scanner_test=item.get('scanner_test', False),
                    scanner_after_lull=item.get('scanner_after_lull', False),
                ))
            return announcements
        except Exception:
            return []

    def load_all_cached_data(self, window_minutes: int = 120) -> Tuple[List[Announcement], dict]:
        """
        Load all cached announcements and their OHLCV data.

        Returns:
            Tuple of (announcements, bars_by_announcement)
        """
        announcements = self.load_announcements()
        bars_by_announcement = {}

        for ann in announcements:
            key = (ann.ticker, ann.timestamp)
            start = self.get_effective_start_time(ann.timestamp)
            end = start + timedelta(minutes=window_minutes)
            bars = self._load_from_cache(ann.ticker, start, end)
            # Store result if cache file exists (even if empty)
            # None means unfetched, [] means fetched but no data
            if bars is not None:
                bars_by_announcement[key] = bars

        return announcements, bars_by_announcement


def create_client(api_key: Optional[str] = None, cache_dir: str = "data/ohlcv") -> MassiveClient:
    """Factory function to create a MassiveClient instance."""
    return MassiveClient(api_key=api_key, cache_dir=cache_dir)
