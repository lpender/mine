"""Test that strategy save and restore preserves all configuration fields."""

import pytest
from src.strategy import StrategyConfig
from src.strategy_store import get_strategy_store, _config_from_dict


def test_strategy_config_roundtrip():
    """Test that StrategyConfig can be serialized and deserialized without data loss."""

    # Create a config with non-default values
    original_config = StrategyConfig(
        # Filters
        channels=["🍒-select-news"],
        directions=["up_right", "up"],
        price_min=3.5,
        price_max=100.0,
        sessions=["premarket", "market"],
        country_blacklist=["HK"],
        max_intraday_mentions=5,
        exclude_financing_headlines=True,
        exclude_biotech=True,
        max_prior_move_pct=20.0,
        max_market_cap_millions=100.0,
        # Entry rules
        consec_green_candles=1,
        min_candle_volume=100000,
        entry_window_minutes=5,
        # Exit rules
        take_profit_pct=10.0,
        stop_loss_pct=15.0,
        stop_loss_from_open=False,
        trailing_stop_pct=0.0,
        timeout_minutes=30,
        # Position sizing
        stake_mode="volume_pct",
        stake_amount=10000.0,
        volume_pct=2.0,
        max_stake=80000.0,
    )

    # Serialize to dict (as done when saving to DB)
    config_dict = original_config.to_dict()

    # Deserialize from dict (as done when loading from DB)
    restored_config = _config_from_dict(config_dict)

    # Verify all fields match
    assert restored_config.channels == original_config.channels, "channels mismatch"
    assert restored_config.directions == original_config.directions, "directions mismatch"
    assert restored_config.price_min == original_config.price_min, "price_min mismatch"
    assert restored_config.price_max == original_config.price_max, "price_max mismatch"
    assert restored_config.sessions == original_config.sessions, "sessions mismatch"
    assert restored_config.country_blacklist == original_config.country_blacklist, "country_blacklist mismatch"
    assert restored_config.max_intraday_mentions == original_config.max_intraday_mentions, "max_intraday_mentions mismatch"
    assert restored_config.exclude_financing_headlines == original_config.exclude_financing_headlines, "exclude_financing_headlines mismatch"
    assert restored_config.exclude_biotech == original_config.exclude_biotech, "exclude_biotech mismatch"
    assert restored_config.max_prior_move_pct == original_config.max_prior_move_pct, "max_prior_move_pct mismatch"
    assert restored_config.max_market_cap_millions == original_config.max_market_cap_millions, "max_market_cap_millions mismatch"

    # Entry rules
    assert restored_config.consec_green_candles == original_config.consec_green_candles, "consec_green_candles mismatch"
    assert restored_config.min_candle_volume == original_config.min_candle_volume, "min_candle_volume mismatch"
    assert restored_config.entry_window_minutes == original_config.entry_window_minutes, "entry_window_minutes mismatch"

    # Exit rules
    assert restored_config.take_profit_pct == original_config.take_profit_pct, "take_profit_pct mismatch"
    assert restored_config.stop_loss_pct == original_config.stop_loss_pct, "stop_loss_pct mismatch"
    assert restored_config.stop_loss_from_open == original_config.stop_loss_from_open, "stop_loss_from_open mismatch"
    assert restored_config.trailing_stop_pct == original_config.trailing_stop_pct, "trailing_stop_pct mismatch"
    assert restored_config.timeout_minutes == original_config.timeout_minutes, "timeout_minutes mismatch"

    # Position sizing
    assert restored_config.stake_mode == original_config.stake_mode, "stake_mode mismatch"
    assert restored_config.stake_amount == original_config.stake_amount, "stake_amount mismatch"
    assert restored_config.volume_pct == original_config.volume_pct, "volume_pct mismatch"
    assert restored_config.max_stake == original_config.max_stake, "max_stake mismatch"


def test_strategy_save_and_load(test_db_session):
    """Test that saving and loading a strategy preserves all fields."""
    store = get_strategy_store()

    # Create a config with non-default values (matching user's original URL)
    config = StrategyConfig(
        channels=["🍒-select-news"],
        directions=["up_right", "up"],
        price_min=3.5,
        price_max=100.0,
        sessions=["premarket", "market"],
        country_blacklist=["HK"],
        max_intraday_mentions=None,  # Empty in original URL
        exclude_financing_headlines=True,  # no_fin=1
        exclude_biotech=False,
        max_prior_move_pct=20.0,
        consec_green_candles=1,
        min_candle_volume=100000,
        entry_window_minutes=5,
        take_profit_pct=10.0,
        stop_loss_pct=15.0,
        stop_loss_from_open=False,
        trailing_stop_pct=0.0,
        timeout_minutes=30,
        stake_mode="volume_pct",
        stake_amount=10000.0,
        volume_pct=2.0,
        max_stake=80000.0,
    )

    # Save strategy
    strategy_id = store.save_strategy("Test Strategy Save", config)

    # Load strategy
    loaded_strategy = store.get_strategy(strategy_id)

    assert loaded_strategy is not None, "Strategy not found"
    cfg = loaded_strategy.config

    # Verify all fields match original
    assert cfg.channels == config.channels
    assert cfg.directions == config.directions
    assert cfg.price_min == config.price_min
    assert cfg.price_max == config.price_max
    assert cfg.sessions == config.sessions
    assert cfg.country_blacklist == config.country_blacklist
    assert cfg.max_intraday_mentions == config.max_intraday_mentions
    assert cfg.exclude_financing_headlines == config.exclude_financing_headlines
    assert cfg.exclude_biotech == config.exclude_biotech
    assert cfg.max_prior_move_pct == config.max_prior_move_pct
    assert cfg.consec_green_candles == config.consec_green_candles
    assert cfg.min_candle_volume == config.min_candle_volume
    assert cfg.entry_window_minutes == config.entry_window_minutes
    assert cfg.take_profit_pct == config.take_profit_pct
    assert cfg.stop_loss_pct == config.stop_loss_pct
    assert cfg.stop_loss_from_open == config.stop_loss_from_open
    assert cfg.trailing_stop_pct == config.trailing_stop_pct
    assert cfg.timeout_minutes == config.timeout_minutes
    assert cfg.stake_mode == config.stake_mode
    assert cfg.stake_amount == config.stake_amount
    assert cfg.volume_pct == config.volume_pct
    assert cfg.max_stake == config.max_stake

    # Clean up
    store.delete_strategy(strategy_id)


def test_url_param_generation():
    """Test that URL parameters are correctly generated from a StrategyConfig."""

    config = StrategyConfig(
        channels=["🍒-select-news"],
        directions=["up_right", "up"],
        price_min=3.5,
        price_max=100.0,
        sessions=["premarket", "market"],
        country_blacklist=["HK"],
        max_intraday_mentions=None,
        exclude_financing_headlines=True,
        exclude_biotech=False,
        max_prior_move_pct=20.0,
        consec_green_candles=1,
        min_candle_volume=100000,
        entry_window_minutes=5,
        take_profit_pct=10.0,
        stop_loss_pct=15.0,
        stop_loss_from_open=False,
        trailing_stop_pct=0.0,
        timeout_minutes=30,
        stake_mode="volume_pct",
        stake_amount=10000.0,
        volume_pct=2.0,
        max_stake=80000.0,
    )

    # This mimics what strategies.py does in "Load in Backtest"
    params = {
        "channel": ",".join(config.channels) if config.channels else "",
        "direction": ",".join(config.directions) if config.directions else "",
        "sess": ",".join(config.sessions) if config.sessions else "premarket,market",
        "country_blacklist": ",".join(config.country_blacklist) if config.country_blacklist else "",
        "price_min": str(config.price_min),
        "price_max": str(config.price_max),
        "max_mentions": str(config.max_intraday_mentions) if config.max_intraday_mentions else "",
        "no_fin": "1" if config.exclude_financing_headlines else "0",
        "exclude_biotech": "1" if config.exclude_biotech else "0",
        "max_prior_move": str(config.max_prior_move_pct) if config.max_prior_move_pct else "",
        "consec": str(config.consec_green_candles),
        "min_vol": str(config.min_candle_volume),
        "entry_window": str(config.entry_window_minutes),
        "tp": str(config.take_profit_pct),
        "sl": str(config.stop_loss_pct),
        "trail": str(config.trailing_stop_pct),
        "sl_open": "1" if config.stop_loss_from_open else "0",
        "hold": str(config.timeout_minutes),
        "stake_mode": config.stake_mode,
        "stake": str(config.stake_amount),
        "vol_pct": str(config.volume_pct),
        "max_stake": str(config.max_stake),
    }

    # Verify expected URL params
    assert params["sl"] == "15.0", "stop loss should be 15.0"
    assert params["tp"] == "10.0", "take profit should be 10.0"
    assert params["hold"] == "30", "hold time should be 30"
    assert params["consec"] == "1", "consec should be 1"
    assert params["min_vol"] == "100000", "min_vol should be 100000"
    assert params["entry_window"] == "5", "entry_window should be 5"
    assert params["sl_open"] == "0", "sl_open should be 0 (False)"
    assert params["no_fin"] == "1", "no_fin should be 1 (True)"
    assert params["price_min"] == "3.5", "price_min should be 3.5"
    assert params["price_max"] == "100.0", "price_max should be 100.0"
    assert params["stake_mode"] == "volume_pct", "stake_mode should be volume_pct"
    assert params["stake"] == "10000.0", "stake should be 10000.0"
    assert params["vol_pct"] == "2.0", "vol_pct should be 2.0"
    assert params["max_stake"] == "80000.0", "max_stake should be 80000.0"
    assert params["country_blacklist"] == "HK", "country_blacklist should be HK"
    assert params["max_prior_move"] == "20.0", "max_prior_move should be 20.0"

