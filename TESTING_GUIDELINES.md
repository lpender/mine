# Testing Guidelines

## ⚠️ CRITICAL: Never Test Against Production Database

**The project was previously running tests against the production database, which caused data loss.**

## Test Database Setup

### 1. Create Test Database

```bash
# Create the test database
createdb backtest_test

# Or using psql
psql -c "CREATE DATABASE backtest_test;"
```

### 2. Configure Environment

The test suite automatically uses `backtest_test` database via `tests/conftest.py`.

You can override this by setting:
```bash
export TEST_DATABASE_URL="postgresql://localhost/backtest_test"
```

### 3. Run Tests

```bash
# Run all tests (uses test database automatically)
pytest

# Run specific test file
pytest tests/test_order_events_timezone.py

# Run with verbose output
pytest -v -s
```

## How Test Isolation Works

1. **`tests/conftest.py`** configures test fixtures:
   - Creates a separate test database engine
   - Patches all database connections to use test DB
   - Automatically rolls back after each test

2. **Each test runs in a transaction** that's rolled back:
   - No test data persists in the test database
   - Tests can't interfere with each other
   - Production database is never touched

3. **Fixtures available**:
   - `test_engine` - Test database engine (session scope)
   - `test_db_session` - Test database session (function scope, auto-rollback)
   - `use_test_database` - Automatically patches DB connections (autouse)

## Writing Tests

### Good Example

```python
def test_create_order(test_db_session):
    """Test order creation."""
    from src.order_store import OrderStore

    store = OrderStore()
    order_id = store.create_order(
        ticker="TEST",
        side="buy",
        order_type="limit",
        requested_shares=100,
        limit_price=10.50,
    )

    assert order_id is not None
    # Data is automatically cleaned up after test
```

### Bad Example (DO NOT DO THIS)

```python
def test_something():
    from src.database import SessionLocal

    session = SessionLocal()
    try:
        # Manually delete data
        session.query(OrderDB).delete()  # ❌ DELETES PRODUCTION DATA
        session.commit()
    finally:
        session.close()
```

## Verifying Test Isolation

To verify tests are using the test database:

```bash
# In one terminal, watch the test database
watch -n 1 'psql -d backtest_test -c "SELECT COUNT(*) FROM orders;"'

# In another terminal, run tests
pytest tests/test_order_events_timezone.py -v

# You should see the count change during tests but return to 0 after
```

## Production Database Protection

Additional safeguards:

1. **Never import production SessionLocal in tests** - use fixtures
2. **Use transactions** - All test fixtures use transactions that rollback
3. **Separate DATABASE_URL** - Test config points to test database
4. **CI/CD** - Set `TEST_DATABASE_URL` in CI environment

## Recovering from Test Data Corruption

If tests corrupt production data:

1. **Stop all services immediately**
2. **Restore from backup** if available
3. **Check what data was affected**
4. **Fix the test** to use proper isolation
5. **Verify the fix** by running tests multiple times

## References

- pytest fixtures: https://docs.pytest.org/en/stable/fixture.html
- SQLAlchemy testing: https://docs.sqlalchemy.org/en/14/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites

