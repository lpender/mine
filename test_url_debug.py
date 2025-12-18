"""Debug script to test URL parameter handling."""

def test_get_param():
    """Test the get_param function logic."""

    def get_param_mock(key: str, default, param_type=str, url_params=None):
        """Mock of get_param from app.py"""
        val = url_params.get(key, default) if url_params else default
        if param_type == float:
            return float(val) if val and val != "" else default
        elif param_type == int:
            return int(val) if val and val != "" else default
        elif param_type == bool:
            return val == "1" or val == "true"
        elif param_type == list:
            if not val or val == "":
                return []
            return [x for x in val.split(",") if x]
        return val

    # Simulate URL params from user's original URL
    url_params = {
        "sl": "15.0",
        "tp": "10.0",
        "hold": "30",
        "consec": "1",
        "min_vol": "100000",
        "entry_window": "5",
        "sl_open": "0",
        "no_fin": "1",
        "price_min": "3.5",
        "price_max": "100.0",
        "stake_mode": "volume_pct",
        "stake": "10000.0",
        "vol_pct": "2.0",
        "max_stake": "80000.0",
        "country_blacklist": "HK",
        "max_prior_move": "20.0",
    }

    # Test each param
    sl = get_param_mock("sl", 5.0, float, url_params)
    assert sl == 15.0, f"Expected sl=15.0, got {sl}"

    tp = get_param_mock("tp", 10.0, float, url_params)
    assert tp == 10.0, f"Expected tp=10.0, got {tp}"

    hold = get_param_mock("hold", 60, int, url_params)
    assert hold == 30, f"Expected hold=30, got {hold}"

    consec = get_param_mock("consec", 0, int, url_params)
    assert consec == 1, f"Expected consec=1, got {consec}"

    min_vol = get_param_mock("min_vol", 0, int, url_params)
    assert min_vol == 100000, f"Expected min_vol=100000, got {min_vol}"

    entry_window = get_param_mock("entry_window", 5, int, url_params)
    assert entry_window == 5, f"Expected entry_window=5, got {entry_window}"

    sl_open = get_param_mock("sl_open", False, bool, url_params)
    assert sl_open == False, f"Expected sl_open=False, got {sl_open}"

    no_fin = get_param_mock("no_fin", False, bool, url_params)
    assert no_fin == True, f"Expected no_fin=True, got {no_fin}"

    price_min = get_param_mock("price_min", 0.0, float, url_params)
    assert price_min == 3.5, f"Expected price_min=3.5, got {price_min}"

    stake_mode = get_param_mock("stake_mode", "fixed", str, url_params)
    assert stake_mode == "volume_pct", f"Expected stake_mode=volume_pct, got {stake_mode}"

    stake = get_param_mock("stake", 1000.0, float, url_params)
    assert stake == 10000.0, f"Expected stake=10000.0, got {stake}"

    vol_pct = get_param_mock("vol_pct", 1.0, float, url_params)
    assert vol_pct == 2.0, f"Expected vol_pct=2.0, got {vol_pct}"

    max_stake = get_param_mock("max_stake", 10000.0, float, url_params)
    assert max_stake == 80000.0, f"Expected max_stake=80000.0, got {max_stake}"

    print("✅ All URL param parsing tests passed!")


if __name__ == "__main__":
    test_get_param()

