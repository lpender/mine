# Strategy Implementation Audit Summary
**Date**: December 18, 2024
**Status**: Plan verified, ready for implementation

## Quick Restart Prompt

Copy this to restart the task:

```
Continue the strategy audit from STRATEGY_AUDIT_2024_12_18.md. The plan has been verified by Opus and is ready for implementation. Skip Phase 0 (orphaned positions) since cleanup_orphaned_positions.py --sell already exists. Start with Bug 1.1 (backtest gap detection) using TDD approach.

Key refinements from verification:
1. Bug 1.1: Fix BOTH trailing stop AND fixed stop loss gap detection (change bar.high to bar.open)
2. Bug 1.2: Use asyncio.run_coroutine_threadsafe() instead of simple locks for thread safety
3. Target 80%+ test coverage with TDD approach (write test first, then fix)
4. Conservative refactoring: only deduplicate stores with BaseStore class
```

---

## Verified Bugs (8 Total)

### Bug 1.1: Backtest Gap Detection (CRITICAL)
**Files**: `src/backtest.py` lines 221-226, 235-238
**Issue**: Uses `bar.high < stop_price` but should use `bar.open < stop_price`
**Impact**: Overly optimistic backtest results when bar opens below stop but rallies above it
**Fix**: Change gap detection in BOTH trailing stop AND fixed stop loss:
```python
# OLD: if bar.high < trailing_stop_price:
# NEW: if bar.open < trailing_stop_price:
```
**Test needed**: Bar opens below stop ($9.40), high reaches above ($10.00), should fill at open not stop

### Bug 1.2: Threading Race Conditions (HIGH)
**Files**: `src/strategy.py`, `src/live_trading_service.py`, `src/trading/alpaca_stream.py`
**Issue**: Three threads access StrategyEngine dicts without synchronization:
- Thread 1: Main (Streamlit)
- Thread 2: TradingEngine (asyncio event loop) - calls on_quote()
- Thread 3: AlpacaTradeStream - calls on_buy_fill(), on_sell_fill()

**Fix approach**: Use `asyncio.run_coroutine_threadsafe()` to dispatch AlpacaTradeStream callbacks to TradingEngine's event loop. This ensures all strategy operations happen on the same thread.

**NOT recommended**: Simple RLock (conflicts with asyncio)

### Bug 1.3: Stop Loss from Open Edge Case (MEDIUM)
**File**: `src/strategy.py` lines 1046-1050
**Issue**: Check uses `>` but should use `>=` (stop == entry causes immediate stop-out)
```python
# OLD: if stop_loss_price > price:
# NEW: if stop_loss_price >= price:
```

### Bug 1.4: 422 "Cannot Be Sold Short" Errors (CRITICAL)
**File**: `src/strategy.py` _execute_exit()
**Issue**: Trying to sell shares we don't own (orphaned positions)
**Fix**: Verify broker position exists before submitting sell order

### Bug 1.5: Order Cancellation Race Condition (MEDIUM)
**File**: `src/trading/alpaca.py` cancel_order()
**Issue**: Order can fill between timeout detection and cancel attempt
**Fix**: Check order status via REST API before attempting cancel

### Bug 1.6: Database Constraint Violations (HIGH)
**File**: `src/active_trade_store.py` lines 35-69
**Issue**: UniqueViolation on (ticker, strategy_id) when duplicate trade saved
**Fix**: Implement upsert pattern - check for existing, update if found

### Bug 1.7: Sell Retry Double Counting (MEDIUM)
**File**: `src/strategy.py` lines 2098 and 2170
**Issue**: sell_attempts incremented twice when retry fails:
- Line 2098: increment in _cancel_pending_sell_order()
- Line 2170: increment again in exception handler
**Fix**: Only increment in one place

### Bug 1.8: Missing DB Update for Share Correction (MEDIUM)
**File**: `src/strategy.py` lines 1819-1822
**Issue**: When broker reports different share count, in-memory updated but DB not persisted
**Fix**: Call _active_trade_store.save_trade() after correcting shares

---

## Test Coverage Plan (Target: 80%+)

### New Test Files Needed
1. `tests/test_backtest_gap_scenarios.py` - Gap detection edge cases
2. `tests/test_strategy_threading.py` - Race condition scenarios
3. `tests/test_stop_loss_calculations.py` - Stop loss edge cases
4. `tests/test_exit_with_position_verification.py` - Position verification
5. `tests/test_order_cancellation_race.py` - Cancel race handling
6. `tests/test_database_stores.py` - Upsert and constraint handling
7. `tests/test_sell_retry_logic.py` - Retry counter accuracy
8. `tests/test_share_count_correction.py` - Share correction persistence
9. `tests/test_position_sizing.py` - Position sizing calculations
10. `tests/test_order_fills.py` - Order fill handling
11. `tests/test_exit_conditions.py` - Exit condition priority
12. `tests/test_strategy_filters.py` - Filter logic
13. `tests/test_error_scenarios.py` - Error handling paths
14. `tests/test_reconciliation.py` - Position recovery

### Existing Test Coverage (Good)
- `tests/test_backtest.py` (1329 lines) - Extensive backtest coverage
- `tests/test_trailing_stop.py` - Trailing stop mechanics
- `tests/test_strategy.py` - Volume aggregation, entry logic
- `tests/test_multi_strategy_positions.py` - Strategy isolation

---

## Refactoring Plan (Conservative)

### Create BaseStore Class
**File**: `src/base_store.py` (new)

Eliminate 400+ lines of duplicated session management:
```python
class BaseStore:
    @contextmanager
    def _db_session(self):
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

**Stores to refactor** (8 total):
- active_trade_store.py
- trade_store.py
- pending_entry_store.py
- order_store.py
- orphaned_order_store.py
- live_bar_store.py
- strategy_store.py
- trace_store.py

---

## Log Errors Summary (from logs/dev.log)

| Error Type | Count | Root Cause |
|------------|-------|------------|
| 422 "Cannot sell short" | 20+ | Selling positions we don't own |
| 422 "Order already filled" | 4 | Race between timeout and fill |
| DB Constraint violation | 1 | Duplicate (ticker, strategy_id) |
| Position limit exceeded | 30+ | Multiple buys before limit enforced |
| Orphaned positions | 20+ | Disabled strategies with positions |

---

## Existing Scripts

- `cleanup_orphaned_positions.py` - USE THIS for orphan cleanup
  - `python cleanup_orphaned_positions.py` - Dry run
  - `python cleanup_orphaned_positions.py --sell` - Sell and clean

- `scripts/close_orphaned_positions.py` - Duplicate, can be deleted

---

## Execution Order

1. ~~Phase 0: Clean orphaned positions~~ (use existing script)
2. **Phase 1**: Fix bugs 1.1-1.8 with TDD (write test, fix, commit)
3. **Phase 2**: Add remaining test suites for 80%+ coverage
4. **Phase 3**: Extract BaseStore class, refactor stores
5. **Phase 4**: Documentation cleanup

---

## Already Fixed This Session

1. Trailing stop persistence bug in `src/strategy.py` - highest_since_entry now saved to DB
   - Commit: e7bb7b3 "Fix trailing stop by persisting highest_since_entry to database"

---

## Files Modified/Created This Session

- `src/strategy.py` - Added update_price() call in _check_exit()
- `scripts/close_orphaned_positions.py` - Created (duplicate of existing)
- `/Users/lpender/.claude/plans/swirling-tickling-swan.md` - Detailed plan file
