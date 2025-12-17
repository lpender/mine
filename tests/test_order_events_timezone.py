"""Test timezone handling in order events.

NOTE: Tests use a separate test database (backtest_test) via conftest.py
to avoid corrupting production data.
"""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src import database
from src.database import OrderDB, OrderEventDB
from src.order_store import get_order_store


def test_order_event_timestamps_are_utc():
    """Verify that order and event timestamps are stored as naive UTC."""
    store = get_order_store()

    # Create a timestamp - 10:05 AM EST = 15:05 UTC
    est_tz = ZoneInfo("America/New_York")
    utc_tz = ZoneInfo("UTC")

    # 10:05 AM EST on Dec 17, 2025
    est_time = datetime(2025, 12, 17, 10, 5, 0, tzinfo=est_tz)
    utc_time = est_time.astimezone(utc_tz).replace(tzinfo=None)  # Naive UTC

    print(f"\nEST time: {est_time}")
    print(f"UTC time (naive): {utc_time}")
    assert utc_time.hour == 15  # 10 AM EST = 3 PM UTC (15:00)

    # Create an order
    order_id = store.create_order(
        ticker="TEST",
        side="buy",
        order_type="limit",
        requested_shares=100,
        limit_price=10.50,
        broker_order_id="broker123",
        paper=True,
    )

    assert order_id is not None

    # Record a SUBMITTED event with UTC timestamp
    event_id = store.record_event(
        event_type="submitted",
        event_timestamp=utc_time,  # Should be naive UTC
        order_id=order_id,
        broker_order_id="broker123",
    )

    assert event_id is not None

    # Retrieve and verify - use database.SessionLocal to get patched version
    db = database.SessionLocal()
    try:
        order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
        event = db.query(OrderEventDB).filter(OrderEventDB.id == event_id).first()

        # Verify order created_at is UTC (should be close to now)
        assert order.created_at.tzinfo is None  # Naive
        now_utc = datetime.utcnow()
        assert abs((order.created_at - now_utc).total_seconds()) < 5  # Within 5 seconds

        # Verify event timestamp is the UTC time we passed
        assert event.event_timestamp.tzinfo is None  # Naive
        assert event.event_timestamp == utc_time

        print(f"\nOrder created_at (UTC): {order.created_at}")
        print(f"Event timestamp (UTC): {event.event_timestamp}")

        # Note: In this test, the event timestamp is from the past (our test data),
        # while created_at is "now". In real trading, they would be seconds apart.

    finally:
        db.close()


def test_display_conversion_to_est():
    """Test that display properly converts UTC to EST."""
    from pages.trades import to_est_display

    # Create a naive UTC time
    utc_time = datetime(2025, 12, 17, 15, 5, 0)  # 3:05 PM UTC

    # Convert to EST for display
    est_display = to_est_display(utc_time)

    # Should be 10:05 AM EST
    assert "10:05:00" in est_display
    assert "2025-12-17" in est_display

    print(f"\nUTC: {utc_time} -> EST display: {est_display}")


def test_submitted_vs_fill_timestamps():
    """Test that SUBMITTED and FILL events both use UTC consistently."""
    store = get_order_store()

    # Simulate the flow:
    # 1. on_quote() receives timestamp from quote provider (UTC)
    # 2. _execute_entry() calls create_order() and record_event("submitted")
    # 3. Later, on_buy_fill() calls record_event("fill")

    quote_time = datetime(2025, 12, 17, 15, 5, 0)  # 10:05 AM EST = 15:05 UTC (naive)

    # Create order (simulates create_order in _execute_entry)
    order_id = store.create_order(
        ticker="TEST",
        side="buy",
        order_type="limit",
        requested_shares=100,
        limit_price=10.50,
        broker_order_id="broker123",
        paper=True,
    )

    # Record SUBMITTED event (simulates record_event in _execute_entry)
    submitted_event_id = store.record_event(
        event_type="submitted",
        event_timestamp=quote_time,  # Uses the quote timestamp
        order_id=order_id,
        broker_order_id="broker123",
    )

    # Simulate a fill 2 seconds later (from Alpaca)
    fill_time = quote_time + timedelta(seconds=2)

    fill_event_id = store.record_event(
        event_type="fill",
        event_timestamp=fill_time,
        order_id=order_id,
        broker_order_id="broker123",
        filled_shares=100,
        fill_price=10.49,
        cumulative_filled=100,
    )

    # Verify - use database.SessionLocal to get patched version
    db = database.SessionLocal()
    try:
        submitted_event = db.query(OrderEventDB).filter(OrderEventDB.id == submitted_event_id).first()
        fill_event = db.query(OrderEventDB).filter(OrderEventDB.id == fill_event_id).first()

        # Both should be naive UTC
        assert submitted_event.event_timestamp.tzinfo is None
        assert fill_event.event_timestamp.tzinfo is None

        # Should be 2 seconds apart
        diff = (fill_event.event_timestamp - submitted_event.event_timestamp).total_seconds()
        assert diff == 2.0

        print(f"\nSUBMITTED: {submitted_event.event_timestamp} UTC")
        print(f"FILL:      {fill_event.event_timestamp} UTC")
        print(f"Difference: {diff} seconds")

    finally:
        db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

