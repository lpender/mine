"""Tests for src/models.py"""

import pytest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.models import get_market_session

ET_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


class TestGetMarketSession:
    """Tests for get_market_session timezone handling."""

    def test_naive_datetime_assumed_utc(self):
        """
        Regression test: naive datetimes from database are in UTC.

        This tests the bug where 19:41 UTC (14:41 ET) was incorrectly
        classified as postmarket because it was treated as 19:41 ET.
        """
        # NCRA announcement: stored as 19:41:29 UTC = 14:41:29 ET
        utc_naive = datetime(2025, 12, 12, 19, 41, 29)

        # This should be regular market hours (14:41 ET)
        session = get_market_session(utc_naive)
        assert session == "market", f"19:41 UTC = 14:41 ET should be 'market', got '{session}'"

    def test_market_hours_utc_naive(self):
        """Test various times during market hours (stored as UTC naive)."""
        # Market hours: 9:30 AM - 4:00 PM ET = 14:30 - 21:00 UTC
        test_cases = [
            (datetime(2025, 12, 12, 14, 30, 0), "market"),   # 9:30 AM ET - market open
            (datetime(2025, 12, 12, 16, 0, 0), "market"),    # 11:00 AM ET
            (datetime(2025, 12, 12, 19, 0, 0), "market"),    # 2:00 PM ET
            (datetime(2025, 12, 12, 20, 59, 59), "market"),  # 3:59:59 PM ET - just before close
        ]
        for utc_time, expected in test_cases:
            session = get_market_session(utc_time)
            assert session == expected, f"{utc_time} UTC should be '{expected}', got '{session}'"

    def test_premarket_hours_utc_naive(self):
        """Test premarket hours (stored as UTC naive)."""
        # Premarket: 4:00 AM - 9:30 AM ET = 9:00 - 14:30 UTC
        test_cases = [
            (datetime(2025, 12, 12, 9, 0, 0), "premarket"),   # 4:00 AM ET
            (datetime(2025, 12, 12, 12, 0, 0), "premarket"),  # 7:00 AM ET
            (datetime(2025, 12, 12, 14, 29, 59), "premarket"), # 9:29:59 AM ET
        ]
        for utc_time, expected in test_cases:
            session = get_market_session(utc_time)
            assert session == expected, f"{utc_time} UTC should be '{expected}', got '{session}'"

    def test_postmarket_hours_utc_naive(self):
        """Test postmarket hours (stored as UTC naive)."""
        # Postmarket: 4:00 PM - 8:00 PM ET = 21:00 - 01:00 UTC (next day)
        test_cases = [
            (datetime(2025, 12, 12, 21, 0, 0), "postmarket"),   # 4:00 PM ET
            (datetime(2025, 12, 12, 23, 0, 0), "postmarket"),   # 6:00 PM ET
            (datetime(2025, 12, 13, 0, 59, 59), "postmarket"),  # 7:59:59 PM ET (next day UTC)
        ]
        for utc_time, expected in test_cases:
            session = get_market_session(utc_time)
            assert session == expected, f"{utc_time} UTC should be '{expected}', got '{session}'"

    def test_closed_hours_utc_naive(self):
        """Test closed hours (stored as UTC naive)."""
        # Closed: 8:00 PM - 4:00 AM ET = 01:00 - 09:00 UTC
        test_cases = [
            (datetime(2025, 12, 12, 1, 0, 0), "closed"),    # 8:00 PM ET (prev day)
            (datetime(2025, 12, 12, 5, 0, 0), "closed"),    # 12:00 AM ET
            (datetime(2025, 12, 12, 8, 59, 59), "closed"),  # 3:59:59 AM ET
        ]
        for utc_time, expected in test_cases:
            session = get_market_session(utc_time)
            assert session == expected, f"{utc_time} UTC should be '{expected}', got '{session}'"

    def test_timezone_aware_datetime(self):
        """Test that timezone-aware datetimes are handled correctly."""
        # 2:41 PM ET should be market hours
        et_time = datetime(2025, 12, 12, 14, 41, 29, tzinfo=ET_TZ)
        assert get_market_session(et_time) == "market"

        # Same time as UTC-aware
        utc_time = datetime(2025, 12, 12, 19, 41, 29, tzinfo=UTC_TZ)
        assert get_market_session(utc_time) == "market"
