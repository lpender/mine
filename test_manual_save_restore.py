"""Manual test for strategy save/restore - simulates the dashboard workflow."""

from src.strategy import StrategyConfig
from src.strategy_store import get_strategy_store
from src.database import init_db

# Initialize database
init_db()

# Simulate the dashboard "Save as Strategy" button
# This is what the dashboard creates from widget values
print("=" * 60)
print("Step 1: Simulating dashboard save with custom values")
print("=" * 60)

dashboard_config = StrategyConfig(
    # These are the values from the user's original URL
    channels=["🍒-select-news"],
    directions=["up_right", "up"],
    price_min=3.5,
    price_max=100.0,
    sessions=["premarket", "market"],
    country_blacklist=["HK"],
    max_intraday_mentions=None,  # max_mentions was empty
    exclude_financing_headlines=True,  # no_fin=1
    exclude_biotech=False,  # exclude_biotech=0
    max_prior_move_pct=20.0,  # max_prior_move=20.0
    consec_green_candles=1,
    min_candle_volume=100000,
    entry_window_minutes=5,
    take_profit_pct=10.0,
    stop_loss_pct=15.0,
    stop_loss_from_open=False,  # sl_open=0
    trailing_stop_pct=0.0,
    timeout_minutes=30,
    stake_mode="volume_pct",
    stake_amount=10000.0,
    volume_pct=2.0,
    max_stake=80000.0,
)

print("\nOriginal config values:")
print(f"  stop_loss_pct: {dashboard_config.stop_loss_pct}")
print(f"  take_profit_pct: {dashboard_config.take_profit_pct}")
print(f"  timeout_minutes: {dashboard_config.timeout_minutes}")
print(f"  consec_green_candles: {dashboard_config.consec_green_candles}")
print(f"  min_candle_volume: {dashboard_config.min_candle_volume}")
print(f"  entry_window_minutes: {dashboard_config.entry_window_minutes}")
print(f"  stop_loss_from_open: {dashboard_config.stop_loss_from_open}")
print(f"  exclude_financing_headlines: {dashboard_config.exclude_financing_headlines}")
print(f"  price_min: {dashboard_config.price_min}")
print(f"  price_max: {dashboard_config.price_max}")
print(f"  stake_mode: {dashboard_config.stake_mode}")
print(f"  stake_amount: {dashboard_config.stake_amount}")
print(f"  volume_pct: {dashboard_config.volume_pct}")
print(f"  max_stake: {dashboard_config.max_stake}")
print(f"  country_blacklist: {dashboard_config.country_blacklist}")
print(f"  max_prior_move_pct: {dashboard_config.max_prior_move_pct}")

# Save strategy
store = get_strategy_store()

# Delete if exists
existing = store.get_strategy_by_name("Test Save Restore")
if existing:
    print("\nDeleting existing 'Test Save Restore' strategy...")
    store.delete_strategy(existing.id)

print("\nSaving strategy to database...")
strategy_id = store.save_strategy("Test Save Restore", dashboard_config)
print(f"✅ Saved with ID: {strategy_id}")

# Simulate loading it back (like "Load in Backtest" button)
print("\n" + "=" * 60)
print("Step 2: Loading strategy back from database")
print("=" * 60)

loaded_strategy = store.get_strategy(strategy_id)
if not loaded_strategy:
    print("❌ ERROR: Strategy not found!")
    exit(1)

cfg = loaded_strategy.config
print("\nLoaded config values:")
print(f"  stop_loss_pct: {cfg.stop_loss_pct}")
print(f"  take_profit_pct: {cfg.take_profit_pct}")
print(f"  timeout_minutes: {cfg.timeout_minutes}")
print(f"  consec_green_candles: {cfg.consec_green_candles}")
print(f"  min_candle_volume: {cfg.min_candle_volume}")
print(f"  entry_window_minutes: {cfg.entry_window_minutes}")
print(f"  stop_loss_from_open: {cfg.stop_loss_from_open}")
print(f"  exclude_financing_headlines: {cfg.exclude_financing_headlines}")
print(f"  price_min: {cfg.price_min}")
print(f"  price_max: {cfg.price_max}")
print(f"  stake_mode: {cfg.stake_mode}")
print(f"  stake_amount: {cfg.stake_amount}")
print(f"  volume_pct: {cfg.volume_pct}")
print(f"  max_stake: {cfg.max_stake}")
print(f"  country_blacklist: {cfg.country_blacklist}")
print(f"  max_prior_move_pct: {cfg.max_prior_move_pct}")

# Simulate URL param generation (like strategies.py does)
print("\n" + "=" * 60)
print("Step 3: Generating URL params (like 'Load in Backtest')")
print("=" * 60)

params = {
    "channel": ",".join(cfg.channels) if cfg.channels else "",
    "direction": ",".join(cfg.directions) if cfg.directions else "",
    "sess": ",".join(cfg.sessions) if cfg.sessions else "premarket,market",
    "country_blacklist": ",".join(cfg.country_blacklist) if cfg.country_blacklist else "",
    "price_min": str(cfg.price_min),
    "price_max": str(cfg.price_max),
    "max_mentions": str(cfg.max_intraday_mentions) if cfg.max_intraday_mentions else "",
    "no_fin": "1" if cfg.exclude_financing_headlines else "0",
    "exclude_biotech": "1" if cfg.exclude_biotech else "0",
    "max_prior_move": str(cfg.max_prior_move_pct) if cfg.max_prior_move_pct else "",
    "consec": str(cfg.consec_green_candles),
    "min_vol": str(cfg.min_candle_volume),
    "entry_window": str(cfg.entry_window_minutes),
    "tp": str(cfg.take_profit_pct),
    "sl": str(cfg.stop_loss_pct),
    "trail": str(cfg.trailing_stop_pct),
    "sl_open": "1" if cfg.stop_loss_from_open else "0",
    "hold": str(cfg.timeout_minutes),
    "stake_mode": cfg.stake_mode,
    "stake": str(cfg.stake_amount),
    "vol_pct": str(cfg.volume_pct),
    "max_stake": str(cfg.max_stake),
}

print("\nGenerated URL params:")
for key, value in params.items():
    if value and value != "":  # Only show non-empty params
        print(f"  {key}={value}")

# Verify critical params
print("\n" + "=" * 60)
print("Verification")
print("=" * 60)

errors = []
if params["sl"] != "15.0":
    errors.append(f"❌ sl should be '15.0', got '{params['sl']}'")
if params["tp"] != "10.0":
    errors.append(f"❌ tp should be '10.0', got '{params['tp']}'")
if params["hold"] != "30":
    errors.append(f"❌ hold should be '30', got '{params['hold']}'")
if params["consec"] != "1":
    errors.append(f"❌ consec should be '1', got '{params['consec']}'")
if params["min_vol"] != "100000":
    errors.append(f"❌ min_vol should be '100000', got '{params['min_vol']}'")
if params["entry_window"] != "5":
    errors.append(f"❌ entry_window should be '5', got '{params['entry_window']}'")
if params["sl_open"] != "0":
    errors.append(f"❌ sl_open should be '0', got '{params['sl_open']}'")
if params["no_fin"] != "1":
    errors.append(f"❌ no_fin should be '1', got '{params['no_fin']}'")
if params["price_min"] != "3.5":
    errors.append(f"❌ price_min should be '3.5', got '{params['price_min']}'")
if params["stake_mode"] != "volume_pct":
    errors.append(f"❌ stake_mode should be 'volume_pct', got '{params['stake_mode']}'")
if params["stake"] != "10000.0":
    errors.append(f"❌ stake should be '10000.0', got '{params['stake']}'")
if params["vol_pct"] != "2.0":
    errors.append(f"❌ vol_pct should be '2.0', got '{params['vol_pct']}'")
if params["max_stake"] != "80000.0":
    errors.append(f"❌ max_stake should be '80000.0', got '{params['max_stake']}'")
if params["country_blacklist"] != "HK":
    errors.append(f"❌ country_blacklist should be 'HK', got '{params['country_blacklist']}'")
if params["max_prior_move"] != "20.0":
    errors.append(f"❌ max_prior_move should be '20.0', got '{params['max_prior_move']}'")

if errors:
    print("\n⚠️  ERRORS FOUND:")
    for error in errors:
        print(error)
    exit(1)
else:
    print("\n✅ ALL CHECKS PASSED! Save/restore cycle works correctly.")
    print("\nThe backend logic is working. If you're still seeing wrong values in the")
    print("dashboard, the issue is likely in how Streamlit widgets read from URL params.")

# Clean up
print("\nCleaning up test strategy...")
store.delete_strategy(strategy_id)
print("Done!")

