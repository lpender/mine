#!/usr/bin/env python3
"""Analyze historical trades for "hotness" patterns - looking for win/loss streaks."""

import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from database import SessionLocal, TradeDB
from sqlalchemy import text


def analyze_trades():
    """Analyze trade history for streak patterns."""

    with SessionLocal() as session:
        # Get all trades ordered by entry time
        trades = session.query(TradeDB).order_by(TradeDB.entry_time.asc()).all()

        if not trades:
            print("No trades found in database")
            return

        print(f"\n{'='*60}")
        print(f"TRADE DATA OVERVIEW")
        print(f"{'='*60}")
        print(f"Total trades: {len(trades)}")
        print(f"Date range: {trades[0].entry_time.date()} to {trades[-1].entry_time.date()}")

        # Basic stats
        winners = [t for t in trades if t.return_pct > 0]
        losers = [t for t in trades if t.return_pct <= 0]
        print(f"Winners: {len(winners)} ({len(winners)/len(trades)*100:.1f}%)")
        print(f"Losers: {len(losers)} ({len(losers)/len(trades)*100:.1f}%)")
        print(f"Avg return: {sum(t.return_pct for t in trades)/len(trades):.2f}%")

        # Analyze streaks
        print(f"\n{'='*60}")
        print(f"STREAK ANALYSIS")
        print(f"{'='*60}")

        streaks = []
        current_streak = 0
        current_type = None

        for t in trades:
            is_win = t.return_pct > 0
            if current_type is None:
                current_type = is_win
                current_streak = 1
            elif is_win == current_type:
                current_streak += 1
            else:
                streaks.append((current_type, current_streak))
                current_type = is_win
                current_streak = 1

        if current_streak > 0:
            streaks.append((current_type, current_streak))

        win_streaks = [s[1] for s in streaks if s[0]]
        loss_streaks = [s[1] for s in streaks if not s[0]]

        print(f"\nWin streaks:")
        if win_streaks:
            print(f"  Max: {max(win_streaks)}")
            print(f"  Avg: {sum(win_streaks)/len(win_streaks):.1f}")
            print(f"  Distribution: {dict(sorted(defaultdict(int, {k: win_streaks.count(k) for k in set(win_streaks)}).items()))}")

        print(f"\nLoss streaks:")
        if loss_streaks:
            print(f"  Max: {max(loss_streaks)}")
            print(f"  Avg: {sum(loss_streaks)/len(loss_streaks):.1f}")
            print(f"  Distribution: {dict(sorted(defaultdict(int, {k: loss_streaks.count(k) for k in set(loss_streaks)}).items()))}")

        # KEY ANALYSIS: Does performance after N wins/losses differ?
        print(f"\n{'='*60}")
        print(f"POST-STREAK PERFORMANCE (Key Question: Is there predictive value?)")
        print(f"{'='*60}")

        def analyze_after_streak(trades, streak_len, win_streak=True):
            """Analyze trade performance after a streak of N wins/losses."""
            results = []
            streak = 0
            is_winning = None

            for i, t in enumerate(trades):
                current_win = t.return_pct > 0

                # Check if we just completed the required streak
                if is_winning == win_streak and streak >= streak_len:
                    # This trade comes after the streak - record it
                    results.append(t.return_pct)

                # Update streak tracking
                if is_winning is None:
                    is_winning = current_win
                    streak = 1
                elif current_win == is_winning:
                    streak += 1
                else:
                    is_winning = current_win
                    streak = 1

            return results

        print("\n--- After WIN streaks ---")
        for n in [1, 2, 3, 4, 5]:
            results = analyze_after_streak(trades, n, win_streak=True)
            if len(results) >= 3:
                avg = sum(results) / len(results)
                wins = sum(1 for r in results if r > 0)
                print(f"  After {n}+ wins: {len(results)} trades, avg={avg:+.2f}%, win_rate={wins/len(results)*100:.0f}%")

        print("\n--- After LOSS streaks ---")
        for n in [1, 2, 3, 4, 5]:
            results = analyze_after_streak(trades, n, win_streak=False)
            if len(results) >= 3:
                avg = sum(results) / len(results)
                wins = sum(1 for r in results if r > 0)
                print(f"  After {n}+ losses: {len(results)} trades, avg={avg:+.2f}%, win_rate={wins/len(results)*100:.0f}%")

        # Rolling window analysis
        print(f"\n{'='*60}")
        print(f"ROLLING WINDOW ANALYSIS")
        print(f"{'='*60}")

        def analyze_rolling_hotness(trades, window=5):
            """Analyze if rolling win rate predicts next trade."""
            results_by_hotness = defaultdict(list)

            for i in range(window, len(trades)):
                # Calculate hotness from previous N trades
                prev_trades = trades[i-window:i]
                wins = sum(1 for t in prev_trades if t.return_pct > 0)
                hotness = wins / window  # 0.0 to 1.0

                # Bucket into categories
                if hotness <= 0.2:
                    bucket = "very_cold (0-20%)"
                elif hotness <= 0.4:
                    bucket = "cold (21-40%)"
                elif hotness <= 0.6:
                    bucket = "neutral (41-60%)"
                elif hotness <= 0.8:
                    bucket = "hot (61-80%)"
                else:
                    bucket = "very_hot (81-100%)"

                results_by_hotness[bucket].append(trades[i].return_pct)

            print(f"\nWindow size: {window} trades")
            print(f"{'Hotness bucket':<22} | {'N':>4} | {'Avg Return':>10} | {'Win Rate':>8}")
            print("-" * 55)

            for bucket in ["very_cold (0-20%)", "cold (21-40%)", "neutral (41-60%)",
                          "hot (61-80%)", "very_hot (81-100%)"]:
                results = results_by_hotness.get(bucket, [])
                if results:
                    avg = sum(results) / len(results)
                    wr = sum(1 for r in results if r > 0) / len(results) * 100
                    print(f"{bucket:<22} | {len(results):>4} | {avg:>+9.2f}% | {wr:>7.0f}%")
                else:
                    print(f"{bucket:<22} | {0:>4} |        n/a |      n/a")

            return results_by_hotness

        for window in [3, 5, 10]:
            analyze_rolling_hotness(trades, window)

        # Simulated adaptive sizing backtest
        print(f"\n{'='*60}")
        print(f"SIMULATED ADAPTIVE SIZING (If we had used hotness...)")
        print(f"{'='*60}")

        def simulate_adaptive_sizing(trades, window=5, min_mult=0.5, max_mult=1.5):
            """Simulate what would have happened with adaptive sizing."""
            base_stake = 100  # $100 base

            fixed_pnl = 0
            adaptive_pnl = 0

            multipliers_used = []

            for i, t in enumerate(trades):
                # Fixed sizing
                fixed_pnl += base_stake * (t.return_pct / 100)

                # Adaptive sizing
                if i < window:
                    mult = 1.0  # Not enough history, use base
                else:
                    prev_trades = trades[i-window:i]
                    wins = sum(1 for pt in prev_trades if pt.return_pct > 0)
                    hotness = wins / window  # 0 to 1
                    # Scale: 0% wins -> min_mult, 100% wins -> max_mult
                    mult = min_mult + hotness * (max_mult - min_mult)

                multipliers_used.append(mult)
                adaptive_pnl += (base_stake * mult) * (t.return_pct / 100)

            return fixed_pnl, adaptive_pnl, multipliers_used

        print("\nComparing fixed vs adaptive sizing:")
        print(f"{'Config':<35} | {'Fixed P&L':>10} | {'Adaptive':>10} | {'Diff':>8}")
        print("-" * 75)

        configs = [
            (3, 0.5, 1.5, "window=3, 0.5x-1.5x"),
            (3, 0.5, 2.0, "window=3, 0.5x-2.0x"),
            (5, 0.5, 1.5, "window=5, 0.5x-1.5x"),
            (5, 0.5, 2.0, "window=5, 0.5x-2.0x"),
            (5, 0.25, 2.0, "window=5, 0.25x-2.0x (aggressive)"),
            (10, 0.5, 1.5, "window=10, 0.5x-1.5x"),
        ]

        for window, min_m, max_m, label in configs:
            fixed, adaptive, _ = simulate_adaptive_sizing(trades, window, min_m, max_m)
            diff = adaptive - fixed
            diff_pct = (diff / abs(fixed) * 100) if fixed != 0 else 0
            print(f"{label:<35} | ${fixed:>9.2f} | ${adaptive:>9.2f} | {diff_pct:>+7.1f}%")

        # Also try INVERSE (anti-martingale in reverse - bet more when cold)
        print(f"\n{'='*60}")
        print(f"INVERSE TEST (Bet MORE when cold - mean reversion)")
        print(f"{'='*60}")

        def simulate_inverse_sizing(trades, window=5, min_mult=0.5, max_mult=1.5):
            """Simulate inverse - bet more when losing."""
            base_stake = 100
            fixed_pnl = 0
            adaptive_pnl = 0

            for i, t in enumerate(trades):
                fixed_pnl += base_stake * (t.return_pct / 100)

                if i < window:
                    mult = 1.0
                else:
                    prev_trades = trades[i-window:i]
                    wins = sum(1 for pt in prev_trades if pt.return_pct > 0)
                    coldness = 1 - (wins / window)  # INVERSE: more cold = higher mult
                    mult = min_mult + coldness * (max_mult - min_mult)

                adaptive_pnl += (base_stake * mult) * (t.return_pct / 100)

            return fixed_pnl, adaptive_pnl

        print(f"{'Config':<35} | {'Fixed P&L':>10} | {'Inverse':>10} | {'Diff':>8}")
        print("-" * 75)

        for window, min_m, max_m, label in configs:
            fixed, inverse = simulate_inverse_sizing(trades, window, min_m, max_m)
            diff = inverse - fixed
            diff_pct = (diff / abs(fixed) * 100) if fixed != 0 else 0
            print(f"{label:<35} | ${fixed:>9.2f} | ${inverse:>9.2f} | {diff_pct:>+7.1f}%")


if __name__ == "__main__":
    analyze_trades()
