#!/usr/bin/env python3
"""
Parameter optimization script for finding best backtest settings.

Usage:
    python optimize.py [--top N]
"""

import argparse
import itertools
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from src.postgres_client import PostgresClient
from src.backtest import run_backtest, BacktestConfig
from src.models import Announcement, OHLCVBar


def calc_total_volume(bars: List[OHLCVBar]) -> int:
    """Calculate total share volume from OHLCV bars."""
    if not bars:
        return 0
    return sum(bar.volume for bar in bars)


@dataclass
class OptResult:
    """Result of an optimization run."""
    config: dict
    total_trades: int
    win_rate: float
    avg_return: float
    total_return: float
    expectancy: float
    profit_factor: float

    def weekly_return(self, weeks: float = 4.0) -> float:
        """Estimate weekly return assuming data spans given weeks."""
        return self.total_return / weeks if weeks > 0 else 0

    def __repr__(self):
        return (
            f"Trades: {self.total_trades:3d} | "
            f"WR: {self.win_rate:5.1f}% | "
            f"Avg: {self.avg_return:+6.2f}% | "
            f"Total: {self.total_return:+7.1f}% | "
            f"PF: {self.profit_factor:5.2f}"
        )


def load_data(window_minutes: int = 120):
    """Load announcements and OHLCV data from database."""
    from datetime import timedelta

    client = PostgresClient()
    announcements = client.load_announcements()

    # Load OHLCV bars for each announcement
    bars_dict = {}
    print(f"Loading OHLCV data for {len(announcements)} announcements...")

    for i, ann in enumerate(announcements):
        key = (ann.ticker, ann.timestamp)
        start = ann.timestamp
        end = ann.timestamp + timedelta(minutes=window_minutes)
        bars = client.get_ohlcv_bars(ann.ticker, start, end)
        if bars:
            bars_dict[key] = bars

        if (i + 1) % 100 == 0:
            print(f"  Loaded {i + 1}/{len(announcements)}...")

    return announcements, bars_dict


def filter_announcements(
    announcements: List[Announcement],
    bars_dict: dict,
    channels: Optional[List[str]] = None,
    directions: Optional[List[str]] = None,
    countries: Optional[List[str]] = None,
    price_min: float = 0,
    price_max: float = 100,
    exclude_financing: bool = False,
    require_headline: bool = False,
    min_volume: int = 0,
) -> List[Announcement]:
    """Filter announcements by various criteria."""
    filtered = []

    for ann in announcements:
        # Channel filter
        if channels and ann.channel:
            if not any(ch.lower() in ann.channel.lower() for ch in channels):
                continue

        # Direction filter
        if directions and ann.direction:
            if ann.direction not in directions:
                continue

        # Country filter
        if countries and ann.country:
            if ann.country not in countries:
                continue

        # Price filter (using price_threshold as proxy)
        if ann.price_threshold:
            if ann.price_threshold < price_min or ann.price_threshold > price_max:
                continue

        # Financing filter
        if exclude_financing and ann.headline_is_financing:
            continue

        # Headline filter
        if require_headline and not ann.headline:
            continue

        # Volume filter (liquidity - shares traded)
        if min_volume > 0:
            key = (ann.ticker, ann.timestamp)
            bars = bars_dict.get(key, [])
            total_vol = calc_total_volume(bars)
            if total_vol < min_volume:
                continue

        filtered.append(ann)

    return filtered


def run_optimization(
    announcements: List[Announcement],
    bars_dict: dict,
    param_grid: dict,
) -> List[OptResult]:
    """Run optimization over parameter grid."""
    results = []

    # Generate all combinations
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(itertools.product(*values))

    print(f"Testing {len(combinations)} parameter combinations...")

    for i, combo in enumerate(combinations):
        params = dict(zip(keys, combo))

        # Create config
        config = BacktestConfig(
            stop_loss_pct=params.get("stop_loss", 10),
            take_profit_pct=params.get("take_profit", 10),
            window_minutes=params.get("hold_time", 30),
            entry_after_consecutive_candles=params.get("consec_candles", 0),
            min_candle_volume=params.get("min_candle_vol", 0),
            trailing_stop_pct=params.get("trailing_stop", 0),
            stop_loss_from_open=params.get("sl_from_open", False),
        )

        # Apply filters
        filtered = filter_announcements(
            announcements,
            bars_dict,
            channels=params.get("channels"),
            directions=params.get("directions"),
            countries=params.get("countries"),
            price_min=params.get("price_min", 0),
            price_max=params.get("price_max", 100),
            exclude_financing=params.get("exclude_financing", False),
            require_headline=params.get("require_headline", False),
            min_volume=params.get("min_volume", 0),
        )

        if len(filtered) < 5:
            continue

        # Run backtest
        summary = run_backtest(filtered, bars_dict, config)

        if summary.total_trades < 5:
            continue

        # Calculate additional stats
        winning_returns = [r.return_pct for r in summary.results
                         if r.entered and r.return_pct and r.return_pct > 0]
        losing_returns = [r.return_pct for r in summary.results
                         if r.entered and r.return_pct and r.return_pct < 0]

        avg_win = sum(winning_returns) / len(winning_returns) if winning_returns else 0
        avg_loss = abs(sum(losing_returns) / len(losing_returns)) if losing_returns else 0

        total_gains = sum(winning_returns) if winning_returns else 0
        total_losses = abs(sum(losing_returns)) if losing_returns else 0
        profit_factor = total_gains / total_losses if total_losses > 0 else float('inf')

        win_rate = summary.win_rate
        loss_rate = 100 - win_rate
        expectancy = ((win_rate / 100) * avg_win) - ((loss_rate / 100) * avg_loss)

        result = OptResult(
            config=params,
            total_trades=summary.total_trades,
            win_rate=summary.win_rate,
            avg_return=summary.avg_return,
            total_return=summary.total_return,
            expectancy=expectancy,
            profit_factor=profit_factor if profit_factor != float('inf') else 99.99,
        )
        results.append(result)

        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(combinations)} tested...")

    return results


def main():
    parser = argparse.ArgumentParser(description="Optimize backtest parameters")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    parser.add_argument("--min-trades", type=int, default=10, help="Minimum trades required")
    args = parser.parse_args()

    print("Loading data from database...")
    announcements, bars_dict = load_data()
    print(f"Loaded {len(announcements)} announcements, {len(bars_dict)} with OHLCV data")

    # Calculate date range for weekly estimates
    if announcements:
        dates = [a.timestamp for a in announcements]
        min_date, max_date = min(dates), max(dates)
        weeks = (max_date - min_date).days / 7
        print(f"Date range: {min_date.date()} to {max_date.date()} ({weeks:.1f} weeks)")
    else:
        weeks = 4.0

    # Define parameter grid (reduced for faster search)
    param_grid = {
        # Exit rules
        "stop_loss": [8, 10, 11, 15],
        "take_profit": [8, 10, 15, 20],
        "hold_time": [15, 30, 60],
        "trailing_stop": [0, 7, 10],

        # Entry rules
        "consec_candles": [0, 1],
        "min_candle_vol": [0, 5000],

        # Filters
        "channels": [None, ["select-news"]],
        "directions": [None, ["up_right"]],
        "price_max": [10, 20],
        "sl_from_open": [False, True],

        # Liquidity filter - 100k shares minimum volume
        "min_volume": [100_000],
    }

    results = run_optimization(announcements, bars_dict, param_grid)

    # Filter by minimum trades
    results = [r for r in results if r.total_trades >= args.min_trades]

    print(f"\n{'='*80}")
    print(f"TOP {args.top} BY TOTAL RETURN (min {args.min_trades} trades)")
    print(f"{'='*80}")

    # Sort by total return
    by_return = sorted(results, key=lambda x: x.total_return, reverse=True)[:args.top]
    for i, r in enumerate(by_return, 1):
        weekly = r.weekly_return(weeks)
        print(f"\n#{i}: {r}")
        print(f"     Weekly: ~{weekly:+.1f}% | Config: {r.config}")

    print(f"\n{'='*80}")
    print(f"TOP {args.top} BY PROFIT FACTOR (min {args.min_trades} trades)")
    print(f"{'='*80}")

    # Sort by profit factor
    by_pf = sorted(results, key=lambda x: x.profit_factor, reverse=True)[:args.top]
    for i, r in enumerate(by_pf, 1):
        weekly = r.weekly_return(weeks)
        print(f"\n#{i}: {r}")
        print(f"     Weekly: ~{weekly:+.1f}% | Config: {r.config}")

    print(f"\n{'='*80}")
    print(f"TOP {args.top} BY EXPECTANCY (min {args.min_trades} trades)")
    print(f"{'='*80}")

    # Sort by expectancy
    by_exp = sorted(results, key=lambda x: x.expectancy, reverse=True)[:args.top]
    for i, r in enumerate(by_exp, 1):
        weekly = r.weekly_return(weeks)
        print(f"\n#{i}: {r}")
        print(f"     Weekly: ~{weekly:+.1f}% | Expectancy: {r.expectancy:+.2f}% | Config: {r.config}")


if __name__ == "__main__":
    main()
