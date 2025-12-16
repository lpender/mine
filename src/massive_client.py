from datetime import datetime, timedelta, date as date_type, time as time_type
from typing import List, Optional
from .models import (
    OHLCVBar,
    get_market_session,
    MARKET_OPEN,
    ET_TZ,
    UTC_TZ,
)
from .data_providers import get_provider, OHLCVDataProvider

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
    """Combine date and time in ET, return as naive ET or aware."""
    dt = datetime.combine(d, t, tzinfo=ET_TZ)
    return dt.replace(tzinfo=None) if naive_input else dt


def _combine_et_to_utc(d: date_type, t: time_type) -> datetime:
    """Combine date and time in ET, convert to naive UTC."""
    dt = datetime.combine(d, t, tzinfo=ET_TZ)
    return dt.astimezone(UTC_TZ).replace(tzinfo=None)


def _floor_to_minute(dt: datetime) -> datetime:
    """Round datetime down to the start of the minute."""
    return dt.replace(second=0, microsecond=0)


class MassiveClient:
    """Client for fetching OHLCV data with market session logic.

    Supports multiple backends via DATA_BACKEND env var (polygon, alpaca, ib).
    Note: This client does not cache data. Use PostgresClient for PostgreSQL caching.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend: Optional[str] = None,
        provider: Optional[OHLCVDataProvider] = None,
    ):
        # For backwards compatibility, accept api_key but don't require it
        # (providers manage their own credentials)
        self._api_key = api_key

        # Use provided provider or create one from factory
        self._provider = provider or get_provider(backend)
        print(f"[MassiveClient] Using {self._provider.name} backend")

    @property
    def api_key(self) -> Optional[str]:
        """For backwards compatibility."""
        return self._api_key

    @property
    def rate_limit_delay(self) -> float:
        """Delegate to provider's rate limit."""
        return self._provider.rate_limit_delay

    def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        multiplier: int = 1,
        timespan: str = "minute",
        use_cache: bool = True,  # Deprecated, kept for backwards compat
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV bars using the configured data provider.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            start: Start datetime
            end: End datetime
            multiplier: Size of timespan multiplier (for compatibility, not used by all providers)
            timespan: Time window (minute, hour, day, etc.)
            use_cache: Deprecated, ignored. Use PostgresClient for caching.

        Returns:
            List of OHLCVBar objects
        """
        # Delegate to provider (no local caching - use PostgresClient for that)
        return self._provider.fetch_ohlcv(ticker, start, end, timespan)

    def fetch_after_announcement(
        self,
        ticker: str,
        announcement_time: datetime,
        window_minutes: int = 120,
        use_cache: bool = True,  # Deprecated, kept for backwards compat
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV data for a window after an announcement.

        For postmarket announcements, starts from the announcement time.
        For premarket announcements, starts from market open of the same day.
        For market announcements, starts from the announcement time.

        Args:
            ticker: Stock ticker symbol
            announcement_time: When the announcement was made
            window_minutes: How many minutes of data to fetch
            use_cache: Deprecated, ignored. Use PostgresClient for caching.

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

        # Clip end time based on provider's data availability
        # (e.g., Alpaca: 15 min delay, Polygon free tier: end of previous day)
        min_delay = self._provider.min_delay_minutes
        earliest_available = now_cmp - timedelta(minutes=min_delay)
        if end_time > earliest_available:
            end_time = earliest_available

        # If the entire window is too recent for the provider, skip
        if start_time >= earliest_available:
            print(
                f"Skipping OHLCV fetch for {ticker}: data not yet available "
                f"(provider {self._provider.name} has {min_delay} min delay)."
            )
            return []

        # Don't request beyond "now" (helps when market is open but window extends into the future)
        if end_time > now_cmp:
            end_time = now_cmp

        return self.fetch_ohlcv(ticker, start_time, end_time)

    def get_effective_start_time(self, announcement_time: datetime) -> datetime:
        """
        Compute the effective OHLCV start time for an announcement.

        Returns naive datetime in UTC (to match OHLCV storage format).

        Args:
            announcement_time: Naive datetime assumed to be UTC (from database),
                              or timezone-aware datetime.

        Returns:
            Naive datetime in UTC representing:
            - Market: announcement time (already UTC)
            - Premarket: same-day market open (in UTC)
            - Postmarket: announcement time (Alpaca has extended hours data)
            - Closed: next market open (in UTC)
        """
        # Convert to Eastern Time for session logic, keep UTC for return
        if announcement_time.tzinfo is None:
            utc_time = announcement_time.replace(tzinfo=UTC_TZ)
            et_time = utc_time.astimezone(ET_TZ).replace(tzinfo=None)
        else:
            utc_time = announcement_time.astimezone(UTC_TZ)
            et_time = announcement_time.astimezone(ET_TZ).replace(tzinfo=None)

        # If the calendar day (in ET) isn't a trading day, roll forward to next session open.
        trading_day = _first_trading_day_on_or_after(et_time.date())
        if trading_day != et_time.date():
            return _combine_et_to_utc(trading_day, MARKET_OPEN)

        # Use the original timestamp for session check (handles UTC correctly)
        session = get_market_session(announcement_time)

        if session == "market":
            # Return the UTC time (naive), floored to minute start for Alpaca API
            if announcement_time.tzinfo is None:
                return _floor_to_minute(announcement_time)
            return _floor_to_minute(announcement_time.astimezone(UTC_TZ).replace(tzinfo=None))

        if session == "premarket":
            # Start from market open of the same day (in UTC)
            return _combine_et_to_utc(et_time.date(), MARKET_OPEN)

        # For postmarket, return announcement time floored to minute (Alpaca has extended hours data)
        if session == "postmarket":
            if announcement_time.tzinfo is None:
                return _floor_to_minute(announcement_time)
            return _floor_to_minute(announcement_time.astimezone(UTC_TZ).replace(tzinfo=None))

        # For closed times, determine next market open
        if session == "closed":
            t = et_time.time()
            if t < MARKET_OPEN:
                # Overnight before the bell: same-day open (in UTC)
                day = _first_trading_day_on_or_after(et_time.date())
                return _combine_et_to_utc(day, MARKET_OPEN)

            # Late evening: next weekday open (in UTC)
            next_day = _first_trading_day_after(et_time.date())
            return _combine_et_to_utc(next_day, MARKET_OPEN)

        # Fallback: next market open (in UTC)
        next_day = _first_trading_day_after(et_time.date())
        return _combine_et_to_utc(next_day, MARKET_OPEN)


def create_client(api_key: Optional[str] = None) -> MassiveClient:
    """Factory function to create a MassiveClient instance."""
    return MassiveClient(api_key=api_key)
