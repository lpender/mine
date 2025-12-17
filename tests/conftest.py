"""Pytest configuration and fixtures for test isolation."""

import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from src.database import Base


# Use a separate test database
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://localhost/backtest_test")


@pytest.fixture(scope="session")
def test_engine():
    """Create a test database engine."""
    engine = create_engine(TEST_DATABASE_URL)

    # Drop all tables first to ensure clean state (handles stale indexes)
    Base.metadata.drop_all(bind=engine)

    # Create all tables fresh
    Base.metadata.create_all(bind=engine)

    yield engine

    # Drop all tables after all tests complete
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def use_test_database(monkeypatch, test_engine):
    """Automatically patch all database connections to use test database with transaction rollback."""
    from src import database

    # Create a connection that will be used for the entire test
    connection = test_engine.connect()
    transaction = connection.begin()

    # Create a session factory that binds to this connection
    # This ensures all sessions in this test share the same transaction
    TestSessionLocal = sessionmaker(bind=connection)

    # Patch the engine
    monkeypatch.setattr(database, "engine", test_engine)

    # Patch SessionLocal in the main database module
    monkeypatch.setattr(database, "SessionLocal", TestSessionLocal)

    # Patch all modules that import SessionLocal directly at module level
    # This is needed because they capture the reference before this fixture runs
    from src import order_store, strategy_store, live_bar_store
    from src import pending_entry_store, trade_history, active_trade_store
    from src import postgres_client

    for module in [order_store, strategy_store, live_bar_store,
                   pending_entry_store, trade_history, active_trade_store,
                   postgres_client]:
        monkeypatch.setattr(module, "SessionLocal", TestSessionLocal)

    yield

    # Rollback the transaction after the test - this cleans up all test data
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def test_db_session(test_engine):
    """Create a new database session for each test with automatic rollback.

    Use this fixture when you need direct access to a session in your test.
    """
    connection = test_engine.connect()
    transaction = connection.begin()

    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    # Rollback everything after the test
    session.close()
    transaction.rollback()
    connection.close()
