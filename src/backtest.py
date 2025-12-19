from datetime import timedelta
from typing import List, Optional
from .models import Announcement, OHLCVBar, TradeResult, BacktestConfig, BacktestSummary


def run_single_backtest(
    announcement: Announcement,
    bars: List[OHLCVBar],
    config: BacktestConfig,
) -> TradeResult:
    """
    Run a backtest for a single announcement.

    Entry Logic:
    - Wait for N consecutive green candles (entry_after_consecutive_candles)
    - N=0 means enter immediately at the open of the first post-announcement bar
    - The announcement bar counts toward the green candle requirement
    - Entry is always at the OPEN of the bar after the signal

    Exit Logic:
    - Take profit when price reaches take_profit_pct above entry
    - Stop loss when price drops stop_loss_pct below entry (or first candle open)
    - Trailing stop tracks highest price and exits on pullback
    - Timeout after window_minutes

    Args:
        announcement: The announcement to backtest
        bars: OHLCV bars for the window after announcement
        config: Backtest configuration

    Returns:
        TradeResult with entry/exit details
    """
    result = TradeResult(announcement=announcement)

    if not bars:
        result.trigger_type = "no_data"
        return result

    # Find the first bar that starts AFTER the announcement time
    # For a 7:13:24 announcement, the first valid bar is 7:14:00 (next complete minute)
    # Round announcement time up to next minute for comparison
    ann_time = announcement.timestamp
    ann_minute_end = ann_time.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Find index of first post-announcement bar
    first_entry_idx = 0
    for i, bar in enumerate(bars):
        if bar.timestamp >= ann_minute_end:
            first_entry_idx = i
            break
    else:
        # No bars after announcement
        result.trigger_type = "no_data"
        return result

    # Filter bars to only include those starting from first post-announcement bar
    first_bar_time = bars[first_entry_idx].timestamp
    entry_bars = [b for b in bars if b.timestamp >= first_bar_time]

    if not entry_bars:
        result.trigger_type = "no_data"
        return result

    # Keep reference to all bars (including announcement bar) for counting consecutive candles
    all_bars = bars

    # Use entry_bars for all entry/exit logic
    bars = entry_bars

    # Calculate entry window end time (how long to look for entry)
    entry_window = config.entry_window_minutes if config.entry_window_minutes > 0 else config.window_minutes
    entry_window_end = first_bar_time + timedelta(minutes=entry_window)

    # Hold time is calculated from entry, not announcement (set after entry is determined)
    hold_minutes = config.window_minutes

    # Store first candle's open for potential stop loss calculation
    first_candle_open = bars[0].open

    # Entry logic: Wait for X consecutive GREEN candles (close > open) with volume threshold
    # Then enter at the OPEN of the next bar (realistic: can't act until candle closes)
    # NOTE: The announcement bar (the bar containing the announcement) counts toward
    # the green candle requirement, but we can't enter until after it closes.
    # Special case: X=0 means "enter at open of first post-announcement bar" (no waiting)
    required = config.entry_after_consecutive_candles
    consecutive_count = 0
    signal_bar_idx = None
    min_vol = config.min_candle_volume

    # Special case: 0 green candles required = enter immediately at first bar open
    if required == 0:
        signal_bar_idx = -1  # Enter at first post-announcement bar

    # Get the announcement bar (bar containing the announcement timestamp)
    ann_minute = ann_time.replace(second=0, microsecond=0)
    announcement_bar = None
    for bar in all_bars:
        if bar.timestamp == ann_minute:
            announcement_bar = bar
            break

    # Start with announcement bar if it exists and qualifies (only if required > 0)
    if signal_bar_idx is None and announcement_bar and announcement_bar.close > announcement_bar.open and announcement_bar.volume >= min_vol:
        consecutive_count = 1
        if consecutive_count >= required:
            # Signal triggered, entry at first post-announcement bar
            signal_bar_idx = -1  # Special marker: announcement bar triggered signal

    # Continue counting from the first post-announcement bar (if not already triggered)
    if signal_bar_idx is None:
        for i, bar in enumerate(bars):
            # Stop looking for entry after entry window expires
            if bar.timestamp >= entry_window_end:
                break

            # Check: green candle (close > open) AND volume meets minimum
            if bar.close > bar.open and bar.volume >= min_vol:
                consecutive_count += 1
                if consecutive_count >= required:
                    # Signal triggered after this candle closes
                    signal_bar_idx = i
                    break
            else:
                consecutive_count = 0  # Reset on failure

    if signal_bar_idx is None:
        result.trigger_type = "no_entry"
        return result

    # Enter at OPEN of the next bar after signal
    if signal_bar_idx == -1:
        # Announcement bar triggered signal (or required=0), enter at first post-announcement bar
        entry_bar_idx = 0
    else:
        entry_bar_idx = signal_bar_idx + 1

    if entry_bar_idx >= len(bars):
        result.trigger_type = "no_entry"  # No next bar available
        return result

    entry_price = bars[entry_bar_idx].open
    entry_time = bars[entry_bar_idx].timestamp

    if entry_price <= 0:
        result.trigger_type = "invalid_price"
        return result

    result.entry_price = entry_price
    result.entry_time = entry_time

    # Set pre-entry volume (volume of candle before entry, for position sizing)
    if entry_bar_idx is not None and entry_bar_idx > 0:
        result.pre_entry_volume = bars[entry_bar_idx - 1].volume
    else:
        result.pre_entry_volume = None  # No previous bar available

    # Phase 2: Look for exit after entry
    take_profit_price = entry_price * (1 + config.take_profit_pct / 100)

    # Stop loss: either from entry price or from first candle's open
    if config.stop_loss_from_open:
        stop_loss_price = first_candle_open * (1 - config.stop_loss_pct / 100)
        # Sanity check: stop loss should never be at or above entry price
        # (that would cause immediate stop-out or act as a take profit)
        if stop_loss_price >= entry_price:
            stop_loss_price = entry_price * (1 - config.stop_loss_pct / 100)
    else:
        stop_loss_price = entry_price * (1 - config.stop_loss_pct / 100)

    exit_price = None
    exit_time = None
    trigger_type = "timeout"

    # Track highest price since entry for trailing stop
    highest_since_entry = entry_price

    # Track consecutive red candles for exit_after_red_candles
    consecutive_red_candles = 0

    # 4-stage intra-candle model: open -> low (if < open) -> high (if > close) -> close
    # Stage 1: Price at open (entry happens here)
    # Stage 2: Price drops to low - only if low < open
    # Stage 3: Price rises to high - only if high > close
    # Stage 4: Price settles at close
    is_first_bar = True
    for bar in bars[entry_bar_idx:]:
        # Stage 2: Price drops to low (only if low < open)
        # IMPORTANT: Use highest_since_entry from PREVIOUS bars, not current bar's high
        # because in the 4-stage model, low happens BEFORE high
        if bar.low < bar.open:
            # On the entry bar, we entered at the open, so bar.open = entry_price.
            # There can't be a "gap through stop" on the entry bar since we just entered.
            # Gap detection only applies to subsequent bars.
            skip_gap_detection = is_first_bar

            # Check trailing stop FIRST (it's typically hit before fixed SL when falling from a high)
            if config.trailing_stop_pct > 0:
                trailing_stop_price = highest_since_entry * (1 - config.trailing_stop_pct / 100)
                if bar.low <= trailing_stop_price:
                    # Gap handling: if bar opens below stop, fill at open (gapped through)
                    # Skip gap detection on entry bar for intra-bar entries
                    if not skip_gap_detection and bar.open < trailing_stop_price:
                        exit_price = bar.open
                    else:
                        exit_price = trailing_stop_price
                    exit_time = bar.timestamp
                    trigger_type = "trailing_stop"
                    break

            # Then check fixed stop loss
            if bar.low <= stop_loss_price:
                # Gap handling: if bar opens below stop, fill at open (gapped through)
                # Skip gap detection on entry bar for intra-bar entries
                if not skip_gap_detection and bar.open < stop_loss_price:
                    exit_price = bar.open
                else:
                    exit_price = stop_loss_price
                exit_time = bar.timestamp
                trigger_type = "stop_loss"
                break

        # Update highest AFTER stage 2 checks (low happens before high in 4-stage model)
        if bar.high > highest_since_entry:
            highest_since_entry = bar.high

        # Stage 3: Check take profit
        if bar.high >= take_profit_price:
            exit_price = take_profit_price
            exit_time = bar.timestamp
            trigger_type = "take_profit"
            break

        # If high > close, price peaked and came back down - check trailing stop at close
        if bar.high > bar.close and config.trailing_stop_pct > 0:
            trailing_stop_price = highest_since_entry * (1 - config.trailing_stop_pct / 100)
            if bar.close <= trailing_stop_price:
                exit_price = trailing_stop_price
                exit_time = bar.timestamp
                trigger_type = "trailing_stop"
                break

        # Stage 4: Check fixed stop loss at close
        if bar.close <= stop_loss_price:
            # Gap handling: if bar opens below stop, fill at open (gapped through)
            if bar.open < stop_loss_price:
                exit_price = bar.open
            else:
                exit_price = stop_loss_price
            exit_time = bar.timestamp
            trigger_type = "stop_loss"
            break

        # Check consecutive red candles exit
        if config.exit_after_red_candles > 0:
            if bar.close < bar.open:  # Red candle
                consecutive_red_candles += 1
                if consecutive_red_candles >= config.exit_after_red_candles:
                    exit_price = bar.close
                    exit_time = bar.timestamp
                    trigger_type = "red_candles"
                    break
            else:  # Green or doji candle resets the count
                consecutive_red_candles = 0

        # Check timeout: hold time is from ENTRY, not announcement
        timeout_time = entry_time + timedelta(minutes=hold_minutes)
        if bar.timestamp >= timeout_time:
            exit_price = bar.close
            exit_time = bar.timestamp
            trigger_type = "timeout"
            break

        is_first_bar = False

    # If no exit triggered (ran out of bars), use last bar's close
    if exit_price is None:
        exit_price = bars[-1].close
        exit_time = bars[-1].timestamp
        trigger_type = "timeout"

    result.exit_price = exit_price
    result.exit_time = exit_time
    result.trigger_type = trigger_type
    result.return_pct = ((exit_price - entry_price) / entry_price) * 100

    return result


def calculate_hotness(recent_results: List[TradeResult], config: BacktestConfig) -> float:
    """
    Calculate hotness multiplier based on recent trade performance.

    Args:
        recent_results: List of recent TradeResults (most recent last)
        config: BacktestConfig with hotness parameters

    Returns:
        Position size multiplier between hotness_min_mult and hotness_max_mult
    """
    if not recent_results:
        return 1.0  # Neutral if no history

    # Count wins in the window
    wins = sum(1 for r in recent_results if r.return_pct is not None and r.return_pct > 0)
    win_rate = wins / len(recent_results)  # 0.0 to 1.0

    # Linear interpolation: 0% wins -> min_mult, 100% wins -> max_mult
    multiplier = config.hotness_min_mult + win_rate * (config.hotness_max_mult - config.hotness_min_mult)

    return multiplier


def run_backtest(
    announcements: List[Announcement],
    bars_by_announcement: dict,  # (ticker, timestamp) -> List[OHLCVBar]
    config: BacktestConfig,
) -> BacktestSummary:
    """
    Run backtests for all announcements.

    Args:
        announcements: List of announcements to backtest
        bars_by_announcement: Dictionary mapping (ticker, timestamp) to OHLCV bars
        config: Backtest configuration

    Returns:
        BacktestSummary with aggregate statistics
    """
    summary = BacktestSummary()
    summary.total_announcements = len(announcements)
    summary.results = []

    returns = []

    # Track recent results for hotness calculation
    recent_entered_results: List[TradeResult] = []

    for announcement in announcements:
        key = (announcement.ticker, announcement.timestamp)
        bars = bars_by_announcement.get(key, [])
        result = run_single_backtest(announcement, bars, config)

        # Calculate and apply hotness multiplier if enabled
        if config.hotness_enabled and result.entered:
            # Use up to hotness_window most recent entered trades
            window = recent_entered_results[-config.hotness_window:] if recent_entered_results else []
            result.hotness_multiplier = calculate_hotness(window, config)

        summary.results.append(result)

        if result.entered:
            summary.total_trades += 1
            # Add to recent results for future hotness calculations
            recent_entered_results.append(result)

            if result.return_pct is not None:
                returns.append(result.return_pct)
                if result.return_pct > 0:
                    summary.winners += 1
                else:
                    summary.losers += 1
        else:
            summary.no_entry += 1

    # Calculate aggregate stats
    if returns:
        summary.avg_return = sum(returns) / len(returns)
        summary.total_return = sum(returns)
        summary.best_trade = max(returns)
        summary.worst_trade = min(returns)

    if summary.total_trades > 0:
        summary.win_rate = (summary.winners / summary.total_trades) * 100

    return summary


def calculate_summary_stats(results: List[TradeResult], config: Optional[BacktestConfig] = None) -> dict:
    """Calculate summary statistics from trade results."""
    total = len(results)
    entered = [r for r in results if r.entered]
    winners = [r for r in entered if r.is_winner]
    losers = [r for r in entered if not r.is_winner]

    returns = [r.return_pct for r in entered if r.return_pct is not None]
    winning_returns = [r.return_pct for r in winners if r.return_pct is not None]
    losing_returns = [r.return_pct for r in losers if r.return_pct is not None]

    # Calculate win rate
    win_rate = (len(winners) / len(entered) * 100) if entered else 0

    # Calculate average win and average loss
    avg_win = sum(winning_returns) / len(winning_returns) if winning_returns else 0
    avg_loss = abs(sum(losing_returns) / len(losing_returns)) if losing_returns else 0

    # Expectancy = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
    # Expressed as expected return per trade
    loss_rate = 100 - win_rate
    expectancy = ((win_rate / 100) * avg_win) - ((loss_rate / 100) * avg_loss)

    # Profit Factor = Total Gains / Total Losses
    total_gains = sum(winning_returns) if winning_returns else 0
    total_losses = abs(sum(losing_returns)) if losing_returns else 0
    profit_factor = total_gains / total_losses if total_losses > 0 else float('inf') if total_gains > 0 else 0

    stats = {
        "total_announcements": total,
        "total_trades": len(entered),
        "no_entry": total - len(entered),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "avg_return": sum(returns) / len(returns) if returns else 0,
        "total_return": sum(returns) if returns else 0,
        "best_trade": max(returns) if returns else 0,
        "worst_trade": min(returns) if returns else 0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
    }

    # Calculate hotness comparison if we have results with hotness multipliers
    if entered and any(r.hotness_multiplier != 1.0 for r in entered):
        base_stake = 100.0  # $100 base for comparison
        fixed_pnl = sum(
            base_stake * (r.return_pct / 100)
            for r in entered if r.return_pct is not None
        )
        hotness_pnl = sum(
            (base_stake * r.hotness_multiplier) * (r.return_pct / 100)
            for r in entered if r.return_pct is not None
        )
        stats["fixed_pnl"] = fixed_pnl
        stats["hotness_pnl"] = hotness_pnl
        stats["hotness_improvement_pct"] = (
            ((hotness_pnl - fixed_pnl) / abs(fixed_pnl) * 100)
            if fixed_pnl != 0 else 0
        )
        stats["avg_hotness_mult"] = (
            sum(r.hotness_multiplier for r in entered) / len(entered)
        )

    return stats
