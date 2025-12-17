# ⚠️ CRITICAL WARNING: Test Database Issue

## The Problem

**Tests are currently running against the production database** (`backtest`), which is **EXTREMELY DANGEROUS**.

On Dec 17, 2025, running tests accidentally deleted all production order data because the test cleanup was too aggressive.

## Immediate Status

✅ **Fixed**: Test cleanup now only deletes orders created during tests (not all orders)
❌ **Not Fixed**: Tests still run against production database
⚠️  **Lost Data**: All order_events prior to Dec 17, 2025 were deleted

## What Was Lost

- All entries in `orders` table
- All entries in `order_events` table
- Trade history (`trade_history` table) is **intact** - only the detailed order events were lost

## Going Forward

### For New Trades
- Order tracking is working correctly
- New trades will have full order event history with proper timezone handling

### For Running Tests
**BE EXTREMELY CAREFUL** when running tests:

```bash
# DANGEROUS - runs against production
pytest

# The test cleanup is now safe (only deletes test data), but you're still
# writing test data to production which could interfere with running services
```

## Proper Solution (TODO)

1. Set up separate test database:
   ```bash
   createdb backtest_test
   export DATABASE_URL="postgresql://localhost/backtest_test"
   pytest
   ```

2. Or use the conftest.py that was created (needs more work to fully isolate)

3. Or add database name check to prevent production writes:
   ```python
   if "backtest_test" not in DATABASE_URL:
       raise RuntimeError("Tests must use backtest_test database!")
   ```

## Lessons Learned

1. **Never test against production data**
2. **Always use separate test databases**
3. **Test cleanup should be surgical, not broad**
4. **Add safeguards to prevent production writes in tests**
5. **Document testing procedures clearly**

## References

- See `TESTING_GUIDELINES.md` for proper test isolation approach
- See `tests/conftest.py` for test database configuration (partially implemented)

