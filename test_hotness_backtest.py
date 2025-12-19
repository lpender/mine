#!/usr/bin/env python3
"""Test the hotness coefficient in the backtest engine."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from datetime import datetime, timedelta
from src.database import SessionLocal, AnnouncementDB, OHLCVBarDB
from src.models import Announcement, OHLCVBar, BacktestConfig
from src.backtest import run_backtest, calculate_summary_stats


def load_test_data(limit: int = 500):
    """Load announcements and OHLCV data from database."""
    with SessionLocal() as session:
        # Get announcements with OHLCV data
        ann_rows = (
            session.query(AnnouncementDB)
            .filter(AnnouncementDB.ohlcv_status == 'fetched')
            .order_by(AnnouncementDB.timestamp.asc())
            .limit(limit)
            .all()
        )

        announcements = []
        bars_by_announcement = {}

        for row in ann_rows:
            ann = Announcement(
                ticker=row.ticker,
                timestamp=row.timestamp,
                price_threshold=row.price_threshold or 0,
                headline=row.headline or "",
                country=row.country or "US",
            )
            announcements.append(ann)

            # Load OHLCV bars for this announcement
            bar_rows = (
                session.query(OHLCVBarDB)
                .filter(
                    OHLCVBarDB.announcement_ticker == row.ticker,
                    OHLCVBarDB.announcement_timestamp == row.timestamp,
                )
                .order_by(OHLCVBarDB.timestamp.asc())
                .all()
            )

            bars = [
                OHLCVBar(
                    timestamp=b.timestamp,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    vwap=b.vwap,
                )
                for b in bar_rows
            ]
            bars_by_announcement[(ann.ticker, ann.timestamp)] = bars

        return announcements, bars_by_announcement


def run_comparison():
    """Run backtest with and without hotness to compare results."""
    print("Loading test data...")
    announcements, bars_by_announcement = load_test_data(limit=1000)
    print(f"Loaded {len(announcements)} announcements")

    # Base config (without hotness)
    base_config = BacktestConfig(
        take_profit_pct=10.0,
        stop_loss_pct=5.0,
        window_minutes=60,
        entry_after_consecutive_candles=1,
        hotness_enabled=False,
    )

    # Hotness configs to test
    hotness_configs = [
        ("window=3, 0.5x-1.5x", BacktestConfig(
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            window_minutes=60,
            entry_after_consecutive_candles=1,
            hotness_enabled=True,
            hotness_window=3,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )),
        ("window=3, 0.5x-2.0x", BacktestConfig(
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            window_minutes=60,
            entry_after_consecutive_candles=1,
            hotness_enabled=True,
            hotness_window=3,
            hotness_min_mult=0.5,
            hotness_max_mult=2.0,
        )),
        ("window=5, 0.5x-1.5x", BacktestConfig(
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            window_minutes=60,
            entry_after_consecutive_candles=1,
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.5,
            hotness_max_mult=1.5,
        )),
        ("window=5, 0.25x-2.0x (aggressive)", BacktestConfig(
            take_profit_pct=10.0,
            stop_loss_pct=5.0,
            window_minutes=60,
            entry_after_consecutive_candles=1,
            hotness_enabled=True,
            hotness_window=5,
            hotness_min_mult=0.25,
            hotness_max_mult=2.0,
        )),
    ]

    # Run base backtest
    print("\nRunning base backtest (no hotness)...")
    base_summary = run_backtest(announcements, bars_by_announcement, base_config)
    base_stats = calculate_summary_stats(base_summary.results)

    print(f"\n{'='*70}")
    print("BASE RESULTS (No Hotness)")
    print(f"{'='*70}")
    print(f"Total trades: {base_stats['total_trades']}")
    print(f"Win rate: {base_stats['win_rate']:.1f}%")
    print(f"Avg return: {base_stats['avg_return']:.2f}%")
    print(f"Expectancy: {base_stats['expectancy']:.2f}%")

    # Calculate base P&L for comparison
    base_pnl = sum(
        100.0 * (r.return_pct / 100)
        for r in base_summary.results
        if r.entered and r.return_pct is not None
    )
    print(f"Simulated P&L ($100 base): ${base_pnl:.2f}")

    # Run hotness backtests
    print(f"\n{'='*70}")
    print("HOTNESS COMPARISON")
    print(f"{'='*70}")
    print(f"{'Config':<35} | {'P&L':>10} | {'vs Base':>10} | {'Avg Mult':>8}")
    print("-" * 70)

    for name, config in hotness_configs:
        summary = run_backtest(announcements, bars_by_announcement, config)
        stats = calculate_summary_stats(summary.results)

        if "hotness_pnl" in stats:
            hotness_pnl = stats["hotness_pnl"]
            improvement = stats["hotness_improvement_pct"]
            avg_mult = stats["avg_hotness_mult"]
            print(f"{name:<35} | ${hotness_pnl:>9.2f} | {improvement:>+9.1f}% | {avg_mult:>7.2f}x")
        else:
            print(f"{name:<35} | (no hotness data)")

    # Show detailed breakdown for best config
    print(f"\n{'='*70}")
    print("DETAILED ANALYSIS: window=5, 0.5x-1.5x")
    print(f"{'='*70}")

    config = BacktestConfig(
        take_profit_pct=10.0,
        stop_loss_pct=5.0,
        window_minutes=60,
        entry_after_consecutive_candles=1,
        hotness_enabled=True,
        hotness_window=5,
        hotness_min_mult=0.5,
        hotness_max_mult=1.5,
    )
    summary = run_backtest(announcements, bars_by_announcement, config)

    # Show multiplier distribution
    entered = [r for r in summary.results if r.entered]
    if entered:
        mults = [r.hotness_multiplier for r in entered]
        print(f"\nMultiplier distribution:")
        print(f"  Min: {min(mults):.2f}x")
        print(f"  Max: {max(mults):.2f}x")
        print(f"  Avg: {sum(mults)/len(mults):.2f}x")

        # Show P&L by multiplier bucket
        print(f"\nP&L by multiplier bucket:")
        for low, high, label in [(0.5, 0.7, "cold (0.5-0.7x)"),
                                   (0.7, 0.9, "cool (0.7-0.9x)"),
                                   (0.9, 1.1, "neutral (0.9-1.1x)"),
                                   (1.1, 1.3, "warm (1.1-1.3x)"),
                                   (1.3, 1.5, "hot (1.3-1.5x)")]:
            bucket = [r for r in entered if low <= r.hotness_multiplier < high]
            if bucket:
                wins = sum(1 for r in bucket if r.return_pct > 0)
                avg_ret = sum(r.return_pct for r in bucket) / len(bucket)
                pnl = sum(100 * r.hotness_multiplier * (r.return_pct/100) for r in bucket)
                print(f"  {label:<20}: {len(bucket):>3} trades, {wins/len(bucket)*100:>5.0f}% WR, {avg_ret:>+6.2f}% avg, ${pnl:>+8.2f}")


if __name__ == "__main__":
    run_comparison()
