# Strategy Save/Restore Fix

## Problem

When saving a strategy from the dashboard and then restoring it via "Load in Backtest", the URL parameters were not matching the original. Many parameters were being reset to default values or missing entirely.

### Example Issue

**Original URL (before save):**
```
sl=15.0&hold=30&consec=1&min_vol=100000&no_fin=1&has_hl=1&price_min=3.5&stake_mode=volume_pct&stake=10000.0&vol_pct=2.0&max_stake=80000.0&...
```

**Restored URL (after load):**
```
sl=5.0&hold=60&consec=0&min_vol=0&no_fin=0&price_min=0.0&stake_mode=fixed&stake=1000.0&vol_pct=1.0&max_stake=10000.0&...
```

Values were being reset to defaults instead of using the saved strategy configuration.

## Root Causes

### 1. Missing Fields in Strategy Save (app.py)

The `StrategyConfig` was not capturing several fields when saving:
- `exclude_financing_headlines` (no_fin checkbox)
- `exclude_biotech` (exclude biotech checkbox)
- `max_prior_move_pct` (prior move max filter)

### 2. Missing Fields in Strategy Restore (strategies.py)

The "Load in Backtest" button was not setting all StrategyConfig fields in the URL:
- `country_blacklist` - was in StrategyConfig but not being restored to URL
- `entry_window` - was in StrategyConfig but not being restored to URL
- Hardcoded `sample_pct=1` instead of leaving empty

### 3. Poor Empty Value Handling

Empty lists and None values were not being handled consistently.

## Fixes Applied

### app.py (Strategy Save)

**File:** `/Users/lpender/dev/trading/backtest/app.py`
**Lines:** 759-781

Added missing fields to StrategyConfig creation:
```python
strategy_config = StrategyConfig(
    # ... existing fields ...
    exclude_financing_headlines=exclude_financing_headlines,  # NEW
    exclude_biotech=exclude_biotech,                         # NEW
    max_prior_move_pct=prior_move_max if prior_move_max > 0 else None,  # NEW
    # ... rest of fields ...
)
```

### strategies.py (Strategy Restore)

**File:** `/Users/lpender/dev/trading/backtest/pages/strategies.py`
**Lines:** 370-407

Fixed the URL parameter building to include ALL StrategyConfig fields:

```python
params = {
    # Filters
    "channel": ",".join(cfg.channels) if cfg.channels else "",
    "direction": ",".join(cfg.directions) if cfg.directions else "",
    "sess": ",".join(cfg.sessions) if cfg.sessions else "premarket,market",
    "country_blacklist": ",".join(cfg.country_blacklist) if cfg.country_blacklist else "",  # FIXED
    "price_min": str(cfg.price_min),
    "price_max": str(cfg.price_max),
    "max_mentions": str(cfg.max_intraday_mentions) if cfg.max_intraday_mentions else "",
    "no_fin": "1" if cfg.exclude_financing_headlines else "0",
    "exclude_biotech": "1" if cfg.exclude_biotech else "0",
    "max_prior_move": str(cfg.max_prior_move_pct) if cfg.max_prior_move_pct else "",
    "max_mcap": str(cfg.max_market_cap_millions) if cfg.max_market_cap_millions else "",
    # Entry rules
    "consec": str(cfg.consec_green_candles),
    "min_vol": str(cfg.min_candle_volume),
    "entry_window": str(cfg.entry_window_minutes),  # FIXED
    # Exit rules
    "tp": str(cfg.take_profit_pct),
    "sl": str(cfg.stop_loss_pct),
    "trail": str(cfg.trailing_stop_pct),
    "sl_open": "1" if cfg.stop_loss_from_open else "0",
    "hold": str(cfg.timeout_minutes),
    # Position sizing
    "stake_mode": cfg.stake_mode,
    "stake": str(cfg.stake_amount),
    "vol_pct": str(cfg.volume_pct),
    "max_stake": str(cfg.max_stake),
}
```

Changes:
- ✅ Added `country_blacklist` parameter
- ✅ Added `entry_window` parameter
- ✅ Fixed empty string handling for list parameters
- ✅ Removed hardcoded `sample_pct=1`
- ✅ Organized params by category (filters, entry, exit, sizing)

## Dashboard-Only Filters (Not Saved in Strategy)

The following filters are **intentionally NOT saved** in strategies because they are for backtest analysis only, not for live trading rules:

### Filters NOT in StrategyConfig:
- `author` - Discord author filter
- `country` - Country whitelist (only blacklist is saved)
- `has_hl` - Require headline filter
- `no_hl` - Exclude headline filter
- `float_min`, `float_max` - Float range filters (backtest-only analysis)
- `mc_min`, `mc_max` - Market cap range filters (separate from max_mcap threshold)
- `nhod`, `nsh` - New high of day / session filters
- `rvol_min`, `rvol_max` - Relative volume filters
- `prior_move_min` - Minimum prior move filter (only max is saved)
- `exclude_financing` - List of specific financing types (only boolean no_fin is saved)
- `sort`, `asc` - Table sorting preferences
- `sample_pct`, `sample_seed` - Sampling parameters for testing

### Why Not Saved?

StrategyConfig is designed for **live trading rules** - which alerts to trade and how to trade them. The additional filters above are useful for **backtest analysis** but don't make sense for live trading strategies.

For example:
- `float_min/max` and `mc_min/mc_max` are ranges for analysis, but live trading uses thresholds
- `has_hl` / `no_hl` are mutually exclusive analysis filters
- `sort` and `sample_pct` are UI-only preferences
- `author` and `country` whitelist are too specific for reusable strategies

## Testing

To verify the fix:

1. Navigate to dashboard with specific URL parameters
2. Set all filters and trigger config to desired values
3. Click "Save as Strategy" with a test name
4. Navigate to Strategies page
5. Click "Load in Backtest" on the saved strategy
6. Verify URL parameters match original values (for StrategyConfig fields)

### Expected Behavior

All StrategyConfig fields should be restored to their saved values:
- Stop loss, take profit, hold time, trailing stop
- Consecutive candles, min volume, entry window
- Price range, sessions, directions, channels
- Country blacklist, max mentions
- Financing exclusion, biotech exclusion, prior move max
- Position sizing mode and parameters

Dashboard-only filters will use their defaults (as documented above).

## Files Changed

1. `/Users/lpender/dev/trading/backtest/app.py` - Fixed strategy save to include all fields
2. `/Users/lpender/dev/trading/backtest/pages/strategies.py` - Fixed strategy restore to set all URL params

