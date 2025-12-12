import argparse
import itertools
from typing import Dict, List, Tuple

import pandas as pd

from src.backtest import calculate_summary_stats, run_backtest
from src.massive_client import MassiveClient
from src.models import Announcement, BacktestConfig, TradeResult


def _entered_results(results: List[TradeResult]) -> List[TradeResult]:
    return [r for r in results if r.entered and r.return_pct is not None]


def _time_split_stats(results: List[TradeResult]) -> Tuple[dict, dict]:
    """
    Split entered trades by time (first half vs second half) and compute summary stats for each.
    """
    entered = _entered_results(results)
    entered = sorted(entered, key=lambda r: r.announcement.timestamp)
    if len(entered) < 2:
        return calculate_summary_stats(results), calculate_summary_stats(results)
    mid = len(entered) // 2
    first = entered[:mid]
    second = entered[mid:]
    return calculate_summary_stats(first), calculate_summary_stats(second)


def _filter_by_finbert_range(
    announcements: List[Announcement],
    fin_min: float,
    fin_max: float,
) -> List[Announcement]:
    return [
        a
        for a in announcements
        if a.finbert_score is not None and fin_min <= a.finbert_score <= fin_max
    ]


def _apply_extra_filters(
    announcements: List[Announcement],
    *,
    exclude_financing: bool,
    gap_min: float,
    gap_max: float,
    pre_dv_min_m: float,
) -> List[Announcement]:
    out = announcements
    if exclude_financing:
        out = [a for a in out if not bool(a.headline_is_financing)]
    if gap_min > -1000 or gap_max < 1000:
        out = [a for a in out if a.premarket_gap_pct is not None and gap_min <= a.premarket_gap_pct <= gap_max]
    if pre_dv_min_m > 0:
        thresh = pre_dv_min_m * 1e6
        out = [a for a in out if a.premarket_dollar_volume is not None and a.premarket_dollar_volume >= thresh]
    return out


def main():
    parser = argparse.ArgumentParser(description="Grid search FinBERT score ranges + backtest params for alpha.")
    parser.add_argument("--min-trades", type=int, default=25, help="Minimum entered trades required (default: 25).")
    parser.add_argument(
        "--robust-only",
        action="store_true",
        help="Only print robust configs (expectancy > 0 in both time halves).",
    )
    parser.add_argument(
        "--exclude-financing",
        action="store_true",
        help="Exclude headlines flagged as financing/dilution.",
    )
    parser.add_argument("--gap-min", type=float, default=-1000.0, help="Premarket gap min %% (default: -1000).")
    parser.add_argument("--gap-max", type=float, default=1000.0, help="Premarket gap max %% (default: 1000).")
    parser.add_argument(
        "--premkt-dv-min-m",
        type=float,
        default=0.0,
        help="Minimum premarket dollar volume in $M (default: 0).",
    )
    args = parser.parse_args()

    client = MassiveClient()
    all_announcements, bars_by = client.load_all_cached_data()

    # Only evaluate announcements with actual bar data present and non-empty.
    announcements = []
    for a in all_announcements:
        bars = bars_by.get((a.ticker, a.timestamp))
        if bars:  # non-empty list
            announcements.append(a)

    announcements = _apply_extra_filters(
        announcements,
        exclude_financing=bool(args.exclude_financing),
        gap_min=float(args.gap_min),
        gap_max=float(args.gap_max),
        pre_dv_min_m=float(args.premkt_dv_min_m),
    )

    print(f"Announcements total: {len(all_announcements)}")
    print(f"Announcements w/ bars: {len(announcements)}")

    # Search space
    fin_thresholds = [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    fin_ranges = [(lo, hi) for lo, hi in itertools.product(fin_thresholds, fin_thresholds) if lo <= hi]

    entry_triggers = [0.0, 0.5, 1.0, 2.0, 3.0]
    take_profits = [5.0, 7.0, 10.0, 12.0, 15.0]
    stop_losses = [3.0, 5.0, 7.0, 10.0]
    window_minutes = [120]

    # Entry modes:
    # - default logic (trigger-based)
    # - message-second fill (only meaningful when entry_trigger=0 and volume_threshold=0)
    entry_modes = [
        {"entry_at_candle_close": False, "entry_by_message_second": False, "name": "open/trigger"},
        {"entry_at_candle_close": False, "entry_by_message_second": True, "name": "msg_second"},
    ]

    min_trades = int(args.min_trades)  # guardrail against tiny-sample "alpha"

    # Baseline (no FinBERT filter) for reference
    baseline_rows = []
    for mode in entry_modes:
        for et, tp, sl, wm in itertools.product(entry_triggers, take_profits, stop_losses, window_minutes):
            if mode["entry_by_message_second"] and not (et == 0.0):
                continue
            cfg = BacktestConfig(
                entry_trigger_pct=et,
                take_profit_pct=tp,
                stop_loss_pct=sl,
                volume_threshold=0,
                window_minutes=wm,
                entry_at_candle_close=mode["entry_at_candle_close"],
                entry_by_message_second=mode["entry_by_message_second"],
            )
            summary = run_backtest(announcements, bars_by, cfg)
            stats = calculate_summary_stats(summary.results)
            if stats["total_trades"] < min_trades:
                continue
            baseline_rows.append(
                {
                    "mode": mode["name"],
                    "fin_min": None,
                    "fin_max": None,
                    "entry_trigger": et,
                    "tp": tp,
                    "sl": sl,
                    "window": wm,
                    **{k: stats[k] for k in ["total_trades", "win_rate", "avg_return", "expectancy", "profit_factor"]},
                }
            )

    if baseline_rows:
        df_base = pd.DataFrame(baseline_rows).sort_values(["expectancy", "total_trades"], ascending=[False, False])
        print("\n=== Baseline (no FinBERT filter), top 10 by expectancy ===")
        print(df_base.head(10).to_string(index=False))

    # Full search with FinBERT range
    rows: List[Dict] = []

    for mode in entry_modes:
        for (fin_min, fin_max) in fin_ranges:
            filtered = _filter_by_finbert_range(announcements, fin_min, fin_max)
            if len(filtered) < min_trades:
                continue

            for et, tp, sl, wm in itertools.product(entry_triggers, take_profits, stop_losses, window_minutes):
                if mode["entry_by_message_second"] and not (et == 0.0):
                    continue

                cfg = BacktestConfig(
                    entry_trigger_pct=et,
                    take_profit_pct=tp,
                    stop_loss_pct=sl,
                    volume_threshold=0,
                    window_minutes=wm,
                    entry_at_candle_close=mode["entry_at_candle_close"],
                    entry_by_message_second=mode["entry_by_message_second"],
                )
                summary = run_backtest(filtered, bars_by, cfg)
                stats = calculate_summary_stats(summary.results)
                if stats["total_trades"] < min_trades:
                    continue

                # Time-split sanity check
                s1, s2 = _time_split_stats(summary.results)

                rows.append(
                    {
                        "mode": mode["name"],
                        "fin_min": fin_min,
                        "fin_max": fin_max,
                        "filtered_announcements": len(filtered),
                        "entry_trigger": et,
                        "tp": tp,
                        "sl": sl,
                        "window": wm,
                        "trades": stats["total_trades"],
                        "win_rate": stats["win_rate"],
                        "avg_return": stats["avg_return"],
                        "expectancy": stats["expectancy"],
                        "profit_factor": stats["profit_factor"],
                        "expectancy_first_half": s1["expectancy"],
                        "expectancy_second_half": s2["expectancy"],
                        "trades_first_half": s1["total_trades"],
                        "trades_second_half": s2["total_trades"],
                    }
                )

    if not rows:
        print("\nNo configurations met minimum trade count; try lowering min_trades.")
        return

    df = pd.DataFrame(rows)

    # Round for display
    for c in ["win_rate", "avg_return", "expectancy", "expectancy_first_half", "expectancy_second_half"]:
        df[c] = df[c].round(3)
    df["profit_factor"] = df["profit_factor"].round(3)

    # Robust set: positive expectancy in BOTH halves
    df_robust = df[(df["expectancy_first_half"] > 0) & (df["expectancy_second_half"] > 0)].copy()
    if args.robust_only:
        print("\n=== Robust-only (expectancy > 0 in both halves), top 50 by expectancy ===")
        if df_robust.empty:
            print("None found with the current grid/min_trades.")
        else:
            df_robust = df_robust.sort_values(["expectancy", "trades"], ascending=[False, False]).head(50)
            print(df_robust.to_string(index=False))
        return

    print("\n=== FinBERT range search, top 20 by expectancy (overall) ===")
    df_top = df.sort_values(["expectancy", "trades"], ascending=[False, False]).head(20)
    print(df_top.to_string(index=False))

    print("\n=== Robust (expectancy > 0 in both halves), top 20 by expectancy ===")
    if df_robust.empty:
        print("None found with the current grid/min_trades.")
    else:
        df_robust = df_robust.sort_values(["expectancy", "trades"], ascending=[False, False]).head(20)
        print(df_robust.to_string(index=False))

    # Also show “simple” filters: score >= t and score <= t (easier to use)
    sweep_rows = []
    thresholds = [-0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8]
    for mode in entry_modes:
        for t in thresholds:
            for direction in ["ge", "le"]:
                if direction == "ge":
                    fin_min, fin_max = t, 1.0
                else:
                    fin_min, fin_max = -1.0, t
                subset = _filter_by_finbert_range(announcements, fin_min, fin_max)
                if len(subset) < min_trades:
                    continue

                # Use the best strategy for that subset/mode from our main df
                sub_df = df[(df["mode"] == mode["name"]) & (df["fin_min"] == fin_min) & (df["fin_max"] == fin_max)]
                if sub_df.empty:
                    continue
                best = sub_df.sort_values(["expectancy", "trades"], ascending=[False, False]).iloc[0].to_dict()
                sweep_rows.append(
                    {
                        "mode": mode["name"],
                        "filter": f"score {'>=' if direction=='ge' else '<='} {t:+.1f}",
                        "filtered_announcements": int(best["filtered_announcements"]),
                        "trades": int(best["trades"]),
                        "entry_trigger": best["entry_trigger"],
                        "tp": best["tp"],
                        "sl": best["sl"],
                        "expectancy": best["expectancy"],
                        "avg_return": best["avg_return"],
                        "win_rate": best["win_rate"],
                        "profit_factor": best["profit_factor"],
                        "exp_half1": best["expectancy_first_half"],
                        "exp_half2": best["expectancy_second_half"],
                    }
                )

    if sweep_rows:
        df_sweep = pd.DataFrame(sweep_rows)
        for c in ["expectancy", "avg_return", "win_rate", "exp_half1", "exp_half2"]:
            df_sweep[c] = df_sweep[c].round(3)
        df_sweep["profit_factor"] = df_sweep["profit_factor"].round(3)
        df_sweep = df_sweep.sort_values(["expectancy", "trades"], ascending=[False, False])
        print("\n=== Simple threshold sweep (best params per threshold), top 25 ===")
        print(df_sweep.head(25).to_string(index=False))


if __name__ == "__main__":
    main()

