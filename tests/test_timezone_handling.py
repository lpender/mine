"""Tests for timezone handling across the codebase.

Key invariants:
1. Announcements are stored in UTC (naive timestamps)
2. OHLCV bars from Alpaca are stored in ET (naive timestamps)
3. All conversions must account for this difference
"""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.models import get_market_session
from src.massive_client import MassiveClient

ET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


class TestGetMarketSession:
    """Test that get_market_session correctly handles UTC naive timestamps."""

    def test_postmarket_friday_evening_utc(self):
        """Friday 7:40 PM ET = Saturday 00:40 UTC should be postmarket."""
        # This is the LCUT case: announcement at 19:40 ET stored as 00:40 UTC next day
        utc_naive = datetime(2025, 12, 13, 0, 40, 4)  # UTC
        session = get_market_session(utc_naive)
        assert session == "postmarket", f"00:40 UTC (= 19:40 ET Friday) should be postmarket, got {session}"

    def test_market_hours_utc(self):
        """14:30 UTC = 9:30 AM ET should be market."""
        utc_naive = datetime(2025, 12, 12, 14, 30, 0)  # UTC
        session = get_market_session(utc_naive)
        assert session == "market", f"14:30 UTC (= 9:30 ET) should be market, got {session}"

    def test_premarket_utc(self):
        """12:00 UTC = 7:00 AM ET should be premarket."""
        utc_naive = datetime(2025, 12, 12, 12, 0, 0)  # UTC
        session = get_market_session(utc_naive)
        assert session == "premarket", f"12:00 UTC (= 7:00 ET) should be premarket, got {session}"

    def test_closed_overnight_utc(self):
        """05:00 UTC = midnight ET should be closed."""
        utc_naive = datetime(2025, 12, 12, 5, 0, 0)  # UTC
        session = get_market_session(utc_naive)
        assert session == "closed", f"05:00 UTC (= midnight ET) should be closed, got {session}"


class TestEffectiveStartTime:
    """Test that get_effective_start_time correctly handles UTC naive timestamps."""

    @pytest.fixture
    def client(self):
        return MassiveClient()

    def test_postmarket_friday_returns_monday_open(self, client):
        """Postmarket Friday 7:40 PM ET should return Monday 9:30 AM ET.

        This is the LCUT bug: announcement at 00:40 UTC (= 19:40 ET Friday)
        should start from Monday 9:30 AM ET (not Saturday 9:30 AM).
        """
        # Friday Dec 12, 2025 at 7:40 PM ET = Saturday Dec 13 at 00:40 UTC
        utc_naive = datetime(2025, 12, 13, 0, 40, 4)

        effective = client.get_effective_start_time(utc_naive)

        # Should be Monday Dec 15, 2025 at 9:30 AM ET
        # In ET naive: datetime(2025, 12, 15, 9, 30, 0)
        assert effective.year == 2025
        assert effective.month == 12
        assert effective.day == 15, f"Expected Monday 15th, got day {effective.day}"
        assert effective.hour == 9, f"Expected 9:30 AM, got hour {effective.hour}"
        assert effective.minute == 30, f"Expected 9:30 AM, got minute {effective.minute}"

    def test_market_hours_returns_et_time(self, client):
        """Market hours announcement (UTC) should return equivalent ET time.

        BUG: Previously returned UTC time, but OHLCV bars are stored in ET.
        """
        # Friday Dec 12, 2025 at 10:30 AM ET = 15:30 UTC
        utc_naive = datetime(2025, 12, 12, 15, 30, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should return 10:30 AM ET (not 15:30 UTC)
        # The effective time should be usable to query OHLCV bars stored in ET
        assert effective.hour == 10, f"Expected 10:30 AM ET, got hour {effective.hour}"
        assert effective.minute == 30, f"Expected 10:30 AM ET, got minute {effective.minute}"

    def test_premarket_returns_market_open(self, client):
        """Premarket announcement should return same-day market open."""
        # Friday Dec 12, 2025 at 7:00 AM ET = 12:00 UTC
        utc_naive = datetime(2025, 12, 12, 12, 0, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be same day at 9:30 AM ET
        # The function returns ET naive, so: datetime(2025, 12, 12, 9, 30, 0)
        assert effective.day == 12
        assert effective.hour == 9
        assert effective.minute == 30

    def test_weekend_rolls_to_monday(self, client):
        """Weekend timestamp should roll to Monday open."""
        # Saturday Dec 13, 2025 at 10:00 AM ET = 15:00 UTC
        utc_naive = datetime(2025, 12, 13, 15, 0, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be Monday Dec 15 at 9:30 AM
        assert effective.weekday() == 0, f"Expected Monday (0), got {effective.weekday()}"
        assert effective.day == 15

    def test_closed_early_morning_returns_same_day_open(self, client):
        """Early morning (before premarket) should return same-day market open."""
        # Friday Dec 12, 2025 at 3:00 AM ET = 08:00 UTC
        utc_naive = datetime(2025, 12, 12, 8, 0, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be same day at 9:30 AM ET
        assert effective.day == 12
        assert effective.hour == 9
        assert effective.minute == 30


class TestOHLCVTimezoneAlignment:
    """Test that OHLCV queries use correct timezone for bars stored in ET."""

    def test_announcement_utc_to_ohlcv_et_conversion(self):
        """Verify the conversion from UTC announcement to ET OHLCV query."""
        # Announcement stored in UTC
        announcement_utc = datetime(2025, 12, 13, 0, 40, 4)  # 00:40 UTC = 19:40 ET Friday

        # Convert to ET for OHLCV query (this is what app.py now does)
        timestamp_utc = announcement_utc.replace(tzinfo=UTC_TZ)
        timestamp_et = timestamp_utc.astimezone(ET_TZ).replace(tzinfo=None)

        # Should be Friday 19:40 ET
        assert timestamp_et.day == 12, f"Expected Friday 12th, got day {timestamp_et.day}"
        assert timestamp_et.hour == 19, f"Expected 19:40, got hour {timestamp_et.hour}"
        assert timestamp_et.minute == 40, f"Expected 19:40, got minute {timestamp_et.minute}"

    def test_minute_floor_captures_bar(self):
        """Verify minute floor captures the correct bar."""
        # Announcement at 19:40:04 should query from 19:40:00 to capture the bar
        timestamp_et = datetime(2025, 12, 12, 19, 40, 4)

        start = timestamp_et.replace(second=0, microsecond=0)

        assert start.second == 0
        assert start.microsecond == 0
        assert start.minute == 40
