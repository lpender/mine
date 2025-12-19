#!/usr/bin/env python3
"""Analyze hotness patterns with DEDUPLICATED trades."""

import sys
sys.path.insert(0, 'src')

from database import SessionLocal, TradeDB
from collections import defaultdict


def analyze_deduplicated():
    """Analyze with trades deduplicated by ticker within time window."""

    with SessionLocal() as session:
        trades = session.query(TradeDB).order_by(TradeDB.entry_time.asc()).all()

        # Dedup: only keep first trade per ticker within 5-minute window
        seen = {}  # ticker -> last entry time
        deduped = []

        for t in trades:
            key = t.ticker
            if key not in seen or (t.entry_time - seen[key]).total_seconds() > 300:
                deduped.append(t)
                seen[key] = t.entry_time

        print(f"Original trades: {len(trades)}")
        print(f"Deduplicated trades: {len(deduped)} ({len(deduped)/len(trades)*100:.0f}%)")

        trades = deduped

        # Basic stats
        print(f"\n{'='*60}")
        print("DEDUPLICATED TRADE STATS")
        print(f"{'='*60}")

        winners = [t for t in trades if t.return_pct > 0]
        print(f"Winners: {len(winners)} ({len(winners)/len(trades)*100:.1f}%)")
        print(f"Losers: {len(trades) - len(winners)} ({(len(trades)-len(winners))/len(trades)*100:.1f}%)")
        print(f"Avg return: {sum(t.return_pct for t in trades)/len(trades):.2f}%")

        # Streak analysis
        print(f"\n{'='*60}")
        print("STREAK ANALYSIS (DEDUPLICATED)")
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

        if win_streaks:
            print(f"Win streaks max: {max(win_streaks)}, avg: {sum(win_streaks)/len(win_streaks):.1f}")
            print(f"  Distribution: {dict(sorted({k: win_streaks.count(k) for k in set(win_streaks)}.items()))}")

        if loss_streaks:
            print(f"Loss streaks max: {max(loss_streaks)}, avg: {sum(loss_streaks)/len(loss_streaks):.1f}")
            print(f"  Distribution: {dict(sorted({k: loss_streaks.count(k) for k in set(loss_streaks)}.items()))}")

        # Post-streak performance
        print(f"\n{'='*60}")
        print("POST-STREAK PERFORMANCE (DEDUPLICATED)")
        print(f"{'='*60}")

        def analyze_after_streak(trades, streak_len, win_streak=True):
            results = []
            streak = 0
            is_winning = None

            for t in trades:
                current_win = t.return_pct > 0
                if is_winning == win_streak and streak >= streak_len:
                    results.append(t.return_pct)

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
        for n in [1, 2, 3, 4]:
            results = analyze_after_streak(trades, n, win_streak=True)
            if len(results) >= 3:
                avg = sum(results) / len(results)
                wins = sum(1 for r in results if r > 0)
                print(f"  After {n}+ wins: {len(results)} trades, avg={avg:+.2f}%, win_rate={wins/len(results)*100:.0f}%")

        print("\n--- After LOSS streaks ---")
        for n in [1, 2, 3, 4]:
            results = analyze_after_streak(trades, n, win_streak=False)
            if len(results) >= 3:
                avg = sum(results) / len(results)
                wins = sum(1 for r in results if r > 0)
                print(f"  After {n}+ losses: {len(results)} trades, avg={avg:+.2f}%, win_rate={wins/len(results)*100:.0f}%")

        # Rolling window analysis
        print(f"\n{'='*60}")
        print("ROLLING WINDOW ANALYSIS (DEDUPLICATED)")
        print(f"{'='*60}")

        def analyze_rolling_hotness(trades, window=5):
            results_by_hotness = defaultdict(list)

            for i in range(window, len(trades)):
                prev_trades = trades[i-window:i]
                wins = sum(1 for t in prev_trades if t.return_pct > 0)
                hotness = wins / window

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

        for window in [3, 5]:
            analyze_rolling_hotness(trades, window)

        # Simulated adaptive sizing
        print(f"\n{'='*60}")
        print("SIMULATED ADAPTIVE SIZING (DEDUPLICATED)")
        print(f"{'='*60}")

        def simulate_adaptive(trades, window=5, min_mult=0.5, max_mult=1.5):
            base_stake = 100
            fixed_pnl = 0
            adaptive_pnl = 0

            for i, t in enumerate(trades):
                fixed_pnl += base_stake * (t.return_pct / 100)

                if i < window:
                    mult = 1.0
                else:
                    prev = trades[i-window:i]
                    wins = sum(1 for pt in prev if pt.return_pct > 0)
                    hotness = wins / window
                    mult = min_mult + hotness * (max_mult - min_mult)

                adaptive_pnl += (base_stake * mult) * (t.return_pct / 100)

            return fixed_pnl, adaptive_pnl

        print(f"\n{'Config':<35} | {'Fixed P&L':>10} | {'Adaptive':>10} | {'Diff':>8}")
        print("-" * 75)

        configs = [
            (3, 0.5, 1.5, "window=3, 0.5x-1.5x"),
            (3, 0.5, 2.0, "window=3, 0.5x-2.0x"),
            (5, 0.5, 1.5, "window=5, 0.5x-1.5x"),
            (5, 0.5, 2.0, "window=5, 0.5x-2.0x"),
        ]

        for window, min_m, max_m, label in configs:
            fixed, adaptive = simulate_adaptive(trades, window, min_m, max_m)
            diff = adaptive - fixed
            diff_pct = (diff / abs(fixed) * 100) if fixed != 0 else 0
            print(f"{label:<35} | ${fixed:>9.2f} | ${adaptive:>9.2f} | {diff_pct:>+7.1f}%")

        # Also analyze by SINGLE strategy
        print(f"\n{'='*60}")
        print("PER-STRATEGY ANALYSIS")
        print(f"{'='*60}")

        by_strategy = defaultdict(list)
        for t in session.query(TradeDB).order_by(TradeDB.entry_time.asc()).all():
            by_strategy[t.strategy_name].append(t)

        for strat_name, strat_trades in sorted(by_strategy.items(), key=lambda x: -len(x[1])):
            if len(strat_trades) < 20:
                continue

            wins = sum(1 for t in strat_trades if t.return_pct > 0)
            wr = wins / len(strat_trades) * 100
            avg_ret = sum(t.return_pct for t in strat_trades) / len(strat_trades)

            # Simulate adaptive for this strategy alone
            fixed, adaptive = simulate_adaptive(strat_trades, window=3, min_mult=0.5, max_mult=1.5)
            diff_pct = ((adaptive - fixed) / abs(fixed) * 100) if fixed != 0 else 0

            print(f"\n{strat_name}: {len(strat_trades)} trades, {wr:.0f}% WR, {avg_ret:+.2f}% avg")
            print(f"  Fixed: ${fixed:.2f} → Adaptive: ${adaptive:.2f} ({diff_pct:+.1f}%)")


if __name__ == "__main__":
    analyze_deduplicated()
