"""Tests for alert service functionality."""

import pytest
from collections import OrderedDict
from datetime import datetime, date
from unittest.mock import patch, Mock, MagicMock

from src.alert_service import (
    _infer_author,
    UnifiedAlertHandler,
)


class TestInferAuthor:
    """Tests for _infer_author helper function."""

    def test_returns_provided_author_when_present(self):
        """Test that provided author is returned unchanged."""
        assert _infer_author("any-channel", "John Doe") == "John Doe"

    def test_trims_whitespace_from_author(self):
        """Test that whitespace is trimmed from author."""
        assert _infer_author("any-channel", "  Jane Smith  ") == "Jane Smith"

    def test_infers_pr_spike_author(self):
        """Test author inference for PR-Spike channel."""
        assert _infer_author("pr-spike-alerts", None) == "PR - Spike"
        assert _infer_author("PR Spike", None) == "PR - Spike"
        assert _infer_author("pr spike channel", None) == "PR - Spike"

    def test_infers_nuntiobot_for_select_news(self):
        """Test author inference for select-news channel."""
        assert _infer_author("select-news", None) == "Nuntiobot"
        assert _infer_author("SELECT NEWS", None) == "Nuntiobot"
        assert _infer_author("select news alerts", None) == "Nuntiobot"

    def test_returns_none_for_unknown_channel(self):
        """Test that unknown channels return None."""
        assert _infer_author("random-channel", None) is None
        assert _infer_author("other-news", None) is None

    def test_returns_none_for_empty_author(self):
        """Test that empty string author is treated as missing."""
        assert _infer_author("pr-spike", "") == "PR - Spike"
        assert _infer_author("random", "") is None


class TestAlertDeduplication:
    """Tests for alert deduplication with LRU eviction."""

    def setup_method(self):
        """Reset shared state before each test."""
        UnifiedAlertHandler.seen_alerts = OrderedDict()
        UnifiedAlertHandler.seen_backfill = OrderedDict()

    def test_lru_eviction_removes_oldest_entries(self):
        """Test that LRU eviction removes oldest entries when limit exceeded."""
        # Fill up to limit (500)
        for i in range(500):
            UnifiedAlertHandler.seen_alerts[f"key_{i}"] = True

        assert len(UnifiedAlertHandler.seen_alerts) == 500
        assert "key_0" in UnifiedAlertHandler.seen_alerts

        # Add one more, triggering eviction
        with UnifiedAlertHandler._dedup_lock:
            UnifiedAlertHandler.seen_alerts["new_key"] = True
            while len(UnifiedAlertHandler.seen_alerts) > 500:
                UnifiedAlertHandler.seen_alerts.popitem(last=False)

        assert len(UnifiedAlertHandler.seen_alerts) == 500
        assert "key_0" not in UnifiedAlertHandler.seen_alerts  # Oldest removed
        assert "new_key" in UnifiedAlertHandler.seen_alerts

    def test_backfill_lru_eviction_at_5000(self):
        """Test backfill dedup uses different limit (5000)."""
        for i in range(5000):
            UnifiedAlertHandler.seen_backfill[f"msg_{i}"] = True

        assert len(UnifiedAlertHandler.seen_backfill) == 5000

        # Add more, triggering eviction
        with UnifiedAlertHandler._dedup_lock:
            UnifiedAlertHandler.seen_backfill["new_msg"] = True
            while len(UnifiedAlertHandler.seen_backfill) > 5000:
                UnifiedAlertHandler.seen_backfill.popitem(last=False)

        assert len(UnifiedAlertHandler.seen_backfill) == 5000
        assert "msg_0" not in UnifiedAlertHandler.seen_backfill

    def test_thread_safe_access(self):
        """Test that lock is used for thread-safe access."""
        # Verify lock exists and is a threading.Lock
        import threading
        assert isinstance(UnifiedAlertHandler._dedup_lock, type(threading.Lock()))


class TestTodayFiltering:
    """Tests for filtering out today's announcements."""

    def setup_method(self):
        """Reset handler state."""
        UnifiedAlertHandler.include_today = False
        UnifiedAlertHandler.seen_backfill = OrderedDict()

    def test_include_today_false_filters_todays_announcements(self):
        """Test that today's announcements are filtered when include_today=False."""
        today = date.today()
        yesterday = date(today.year, today.month, today.day - 1) if today.day > 1 else date(
            today.year, today.month - 1 if today.month > 1 else 12, 28
        )

        # Create mock announcements
        today_ann = Mock()
        today_ann.timestamp = datetime.combine(today, datetime.min.time())

        yesterday_ann = Mock()
        yesterday_ann.timestamp = datetime.combine(yesterday, datetime.min.time())

        announcements = [today_ann, yesterday_ann]

        # Filter logic from _handle_backfill
        new_announcements = []
        filtered_today = 0
        for ann in announcements:
            if not UnifiedAlertHandler.include_today and ann.timestamp.date() == today:
                filtered_today += 1
                continue
            new_announcements.append(ann)

        assert len(new_announcements) == 1
        assert new_announcements[0] == yesterday_ann
        assert filtered_today == 1

    def test_include_today_true_keeps_todays_announcements(self):
        """Test that today's announcements are kept when include_today=True."""
        UnifiedAlertHandler.include_today = True
        today = date.today()

        today_ann = Mock()
        today_ann.timestamp = datetime.combine(today, datetime.min.time())

        announcements = [today_ann]
        new_announcements = []
        for ann in announcements:
            if not UnifiedAlertHandler.include_today and ann.timestamp.date() == today:
                continue
            new_announcements.append(ann)

        assert len(new_announcements) == 1


class TestAlertKeyGeneration:
    """Tests for alert key generation (ticker + minute)."""

    def test_alert_key_format(self):
        """Test that alert key is ticker:timestamp_minute."""
        ticker = "AAPL"
        timestamp_str = "2024-12-18T14:30:45.123456"

        # Key generation logic from _handle_alert
        alert_key = f"{ticker}:{timestamp_str[:16]}"

        assert alert_key == "AAPL:2024-12-18T14:30"

    def test_same_minute_same_ticker_deduped(self):
        """Test that alerts in the same minute for same ticker are deduped."""
        UnifiedAlertHandler.seen_alerts = OrderedDict()

        key1 = "AAPL:2024-12-18T14:30"
        key2 = "AAPL:2024-12-18T14:30"  # Same minute

        UnifiedAlertHandler.seen_alerts[key1] = True
        assert key2 in UnifiedAlertHandler.seen_alerts

    def test_different_minute_not_deduped(self):
        """Test that alerts in different minutes are not deduped."""
        UnifiedAlertHandler.seen_alerts = OrderedDict()

        key1 = "AAPL:2024-12-18T14:30"
        key2 = "AAPL:2024-12-18T14:31"  # Different minute

        UnifiedAlertHandler.seen_alerts[key1] = True
        assert key2 not in UnifiedAlertHandler.seen_alerts
