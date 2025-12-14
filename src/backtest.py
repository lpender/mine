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

    Strategy:
    1. Wait for price to move up by entry_trigger_pct from first bar's open
    2. If volume is above threshold, enter the trade
    3. Exit when: take_profit_pct reached, stop_loss_pct reached, or timeout

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

    # Filter bars to only include those within the window
    first_bar_time = bars[0].timestamp
    window_end = first_bar_time + timedelta(minutes=config.window_minutes)
    bars = [b for b in bars if b.timestamp <= window_end]

    if not bars:
        result.trigger_type = "no_data"
        return result

    # Store first candle's open for potential stop loss calculation
    first_candle_open = bars[0].open

    # Handle "entry after consecutive candles" mode
    # Wait for X consecutive candles where low > first candle's open and volume meets threshold
    if config.entry_after_consecutive_candles > 0:
        required = config.entry_after_consecutive_candles
        consecutive_count = 0
        entry_bar_idx = None
        min_vol = config.min_candle_volume

        for i, bar in enumerate(bars):
            # Check: low > first candle open AND volume meets minimum
            if bar.low > first_candle_open and bar.volume >= min_vol:
                consecutive_count += 1
                if consecutive_count >= required:
                    # Entry at close of this candle
                    entry_bar_idx = i
                    break
            else:
                consecutive_count = 0  # Reset on failure

        if entry_bar_idx is None:
            result.trigger_type = "no_entry"
            return result

        entry_price = bars[entry_bar_idx].close
        entry_time = bars[entry_bar_idx].timestamp

        if entry_price <= 0:
            result.trigger_type = "invalid_price"
            return result

    # Handle "entry at candle close" mode - assume we get in at end of first candle
    elif config.entry_at_candle_close:
        # Enter at first candle's close (more realistic - takes time to see alert and execute)
        entry_price = bars[0].close
        entry_time = bars[0].timestamp
        entry_bar_idx = 0

        if entry_price <= 0:
            result.trigger_type = "invalid_price"
            return result
    # Handle "entry within first candle by message second" mode.
    # This is intended for the common case where you enter immediately (no trigger/volume gate),
    # but the fill price is somewhere between the first candle's low/high based on how many
    # seconds into the minute the alert was received.
    elif config.entry_by_message_second and config.entry_trigger_pct == 0 and config.volume_threshold == 0:
        first = bars[0]

        if first.low <= 0 or first.high <= 0:
            result.trigger_type = "invalid_price"
            return result

        sec = getattr(announcement.timestamp, "second", 0) or 0
        # Clamp to [0, 59]
        sec = 0 if sec < 0 else 59 if sec > 59 else sec
        frac = sec / 60.0

        entry_price = first.low + (first.high - first.low) * frac
        entry_time = first.timestamp + timedelta(seconds=sec)
        entry_bar_idx = 0
    else:
        # Original logic: reference price is the first bar's open
        reference_price = bars[0].open
        if reference_price <= 0:
            result.trigger_type = "invalid_price"
            return result

        entry_price = None
        entry_time = None
        entry_bar_idx = None

        # Calculate trigger price (price must reach this level)
        trigger_price = reference_price * (1 + config.entry_trigger_pct / 100)

        # Phase 1: Look for entry - both price AND volume conditions must be met on the same bar
        for i, bar in enumerate(bars):
            # Check if price moved up enough to trigger entry (bar.high reaches trigger level)
            price_change_pct = ((bar.high - reference_price) / reference_price) * 100
            price_triggered = price_change_pct >= config.entry_trigger_pct

            # Check if this bar's volume meets the threshold (intra-candle, not cumulative)
            volume_met = bar.volume >= config.volume_threshold

            # Enter when BOTH conditions are satisfied on this bar
            if price_triggered and volume_met:
                entry_time = bar.timestamp
                entry_bar_idx = i

                # Calculate entry price: the LATER of the two trigger points
                if config.volume_threshold > 0 and bar.volume > 0:
                    # Interpolate: entry when volume threshold is reached within the bar
                    volume_fraction = config.volume_threshold / bar.volume
                    volume_entry_price = bar.low + (bar.high - bar.low) * volume_fraction
                else:
                    volume_entry_price = bar.low

                if config.entry_trigger_pct > 0:
                    price_entry_price = trigger_price
                else:
                    price_entry_price = bar.low

                # Entry price is the LATER of the two trigger points (higher price)
                entry_price = max(volume_entry_price, price_entry_price)
                break

        # No entry triggered
        if entry_price is None:
            result.trigger_type = "no_entry"
            return result

    result.entry_price = entry_price
    result.entry_time = entry_time

    # Phase 2: Look for exit after entry
    take_profit_price = entry_price * (1 + config.take_profit_pct / 100)

    # Stop loss: either from entry price or from first candle's open
    if config.stop_loss_from_open:
        stop_loss_price = first_candle_open * (1 - config.stop_loss_pct / 100)
    else:
        stop_loss_price = entry_price * (1 - config.stop_loss_pct / 100)

    exit_price = None
    exit_time = None
    trigger_type = "timeout"

    # Track highest price since entry for trailing stop
    highest_since_entry = entry_price

    # Start looking for exit on the NEXT bar after entry (can't know intra-bar order)
    for bar in bars[entry_bar_idx + 1:]:
        # Update highest price seen
        if bar.high > highest_since_entry:
            highest_since_entry = bar.high

        # Check for take profit (hit the high)
        if bar.high >= take_profit_price:
            exit_price = take_profit_price
            exit_time = bar.timestamp
            trigger_type = "take_profit"
            break

        # Check for trailing stop (price drops X% from highest point)
        if config.trailing_stop_pct > 0:
            trailing_stop_price = highest_since_entry * (1 - config.trailing_stop_pct / 100)
            if bar.low <= trailing_stop_price:
                exit_price = trailing_stop_price
                exit_time = bar.timestamp
                trigger_type = "trailing_stop"
                break

        # Check for stop loss (hit the low)
        if bar.low <= stop_loss_price:
            exit_price = stop_loss_price
            exit_time = bar.timestamp
            trigger_type = "stop_loss"
            break

    # If no exit triggered, use last bar's close
    if exit_price is None:
        exit_price = bars[-1].close
        exit_time = bars[-1].timestamp
        trigger_type = "timeout"

    result.exit_price = exit_price
    result.exit_time = exit_time
    result.trigger_type = trigger_type
    result.return_pct = ((exit_price - entry_price) / entry_price) * 100

    return result


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

    for announcement in announcements:
        key = (announcement.ticker, announcement.timestamp)
        bars = bars_by_announcement.get(key, [])
        result = run_single_backtest(announcement, bars, config)
        summary.results.append(result)

        if result.entered:
            summary.total_trades += 1
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


def calculate_summary_stats(results: List[TradeResult]) -> dict:
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

    return {
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
