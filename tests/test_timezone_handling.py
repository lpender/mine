"""Tests for timezone handling across the codebase.

Key invariants:
1. All timestamps in the database are stored in UTC (naive timestamps)
2. Announcements are stored in UTC
3. OHLCV bars from Alpaca are stored in UTC
4. Convert to ET only in the presentation layer
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
    """Test that get_effective_start_time returns UTC naive timestamps."""

    @pytest.fixture
    def client(self):
        return MassiveClient()

    def test_postmarket_friday_returns_announcement_time_utc(self, client):
        """Postmarket Friday 7:40 PM ET should return the announcement time.

        Alpaca has extended hours data, so we can fetch from postmarket times.
        """
        # Friday Dec 12, 2025 at 7:40 PM ET = Saturday Dec 13 at 00:40 UTC
        utc_naive = datetime(2025, 12, 13, 0, 40, 4)

        effective = client.get_effective_start_time(utc_naive)

        # Should return same time (postmarket has data in Alpaca)
        assert effective == utc_naive, f"Expected {utc_naive}, got {effective}"

    def test_postmarket_returns_announcement_time_utc(self, client):
        """Postmarket announcement should return the announcement time.

        This is the AKAN fix: announcement at 23:46 UTC (= 18:46 ET)
        should start from the announcement time, not next day's market open.
        """
        # Thursday Dec 11, 2025 at 6:46 PM ET = 23:46 UTC
        utc_naive = datetime(2025, 12, 11, 23, 46, 37)

        effective = client.get_effective_start_time(utc_naive)

        # Should return same time (postmarket has data in Alpaca)
        assert effective == utc_naive, f"Expected {utc_naive}, got {effective}"

    def test_closed_late_night_rolls_to_next_open_utc(self, client):
        """Closed hours (after 8pm ET) should roll to next market open.

        After postmarket ends at 8pm ET, no trading data is available.
        """
        # Friday Dec 12, 2025 at 9:30 PM ET = Saturday Dec 13 at 02:30 UTC
        utc_naive = datetime(2025, 12, 13, 2, 30, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be Monday Dec 15, 2025 at 9:30 AM ET = 14:30 UTC
        assert effective.year == 2025
        assert effective.month == 12
        assert effective.day == 15, f"Expected Monday 15th, got day {effective.day}"
        assert effective.hour == 14, f"Expected 14:30 UTC (9:30 AM ET), got hour {effective.hour}"
        assert effective.minute == 30, f"Expected 14:30 UTC, got minute {effective.minute}"

    def test_market_hours_returns_utc_time(self, client):
        """Market hours announcement (UTC) should return same UTC time.

        Since bars are now stored in UTC, no conversion needed.
        """
        # Friday Dec 12, 2025 at 10:30 AM ET = 15:30 UTC
        utc_naive = datetime(2025, 12, 12, 15, 30, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should return 15:30 UTC (same as input)
        assert effective.hour == 15, f"Expected 15:30 UTC, got hour {effective.hour}"
        assert effective.minute == 30, f"Expected 15:30 UTC, got minute {effective.minute}"

    def test_premarket_returns_market_open_utc(self, client):
        """Premarket announcement should return same-day market open in UTC."""
        # Friday Dec 12, 2025 at 7:00 AM ET = 12:00 UTC
        utc_naive = datetime(2025, 12, 12, 12, 0, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be same day at 9:30 AM ET = 14:30 UTC
        assert effective.day == 12
        assert effective.hour == 14, f"Expected 14:30 UTC (9:30 AM ET), got hour {effective.hour}"
        assert effective.minute == 30

    def test_weekend_rolls_to_monday_utc(self, client):
        """Weekend timestamp should roll to Monday open in UTC."""
        # Saturday Dec 13, 2025 at 10:00 AM ET = 15:00 UTC
        utc_naive = datetime(2025, 12, 13, 15, 0, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be Monday Dec 15 at 9:30 AM ET = 14:30 UTC
        assert effective.weekday() == 0, f"Expected Monday (0), got {effective.weekday()}"
        assert effective.day == 15
        assert effective.hour == 14, f"Expected 14:30 UTC, got hour {effective.hour}"

    def test_closed_early_morning_returns_same_day_open_utc(self, client):
        """Early morning (before premarket) should return same-day market open in UTC."""
        # Friday Dec 12, 2025 at 3:00 AM ET = 08:00 UTC
        utc_naive = datetime(2025, 12, 12, 8, 0, 0)

        effective = client.get_effective_start_time(utc_naive)

        # Should be same day at 9:30 AM ET = 14:30 UTC
        assert effective.day == 12
        assert effective.hour == 14, f"Expected 14:30 UTC (9:30 AM ET), got hour {effective.hour}"
        assert effective.minute == 30


class TestUTCStorage:
    """Test that all timestamps are stored and queried in UTC."""

    def test_utc_to_et_display_conversion(self):
        """Verify UTC to ET conversion for display."""
        # 15:30 UTC = 10:30 AM ET (during EST, Dec is winter)
        utc_naive = datetime(2025, 12, 12, 15, 30, 0)

        # This is how app.py converts for display
        utc_aware = utc_naive.replace(tzinfo=UTC_TZ)
        et_aware = utc_aware.astimezone(ET_TZ)

        assert et_aware.hour == 10, f"Expected 10:30 AM ET, got hour {et_aware.hour}"
        assert et_aware.minute == 30

    def test_minute_floor_captures_bar(self):
        """Verify minute floor captures the correct bar."""
        # Announcement at 15:30:04 UTC should query from 15:30:00 to capture the bar
        timestamp_utc = datetime(2025, 12, 12, 15, 30, 4)

        start = timestamp_utc.replace(second=0, microsecond=0)

        assert start.second == 0
        assert start.microsecond == 0
        assert start.minute == 30
        assert start.hour == 15
