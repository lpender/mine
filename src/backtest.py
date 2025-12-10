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

    # Reference price is the first bar's open
    reference_price = bars[0].open
    if reference_price <= 0:
        result.trigger_type = "invalid_price"
        return result

    entry_price = None
    entry_time = None
    entry_bar_idx = None

    # Phase 1: Look for entry trigger
    for i, bar in enumerate(bars):
        # Check if price moved up enough to trigger entry
        price_change_pct = ((bar.high - reference_price) / reference_price) * 100

        if price_change_pct >= config.entry_trigger_pct:
            # Check volume threshold
            if bar.volume >= config.volume_threshold:
                # Entry triggered - use the trigger price (reference + entry%)
                entry_price = reference_price * (1 + config.entry_trigger_pct / 100)
                entry_time = bar.timestamp
                entry_bar_idx = i
                break

    # No entry triggered
    if entry_price is None:
        result.trigger_type = "no_entry"
        return result

    result.entry_price = entry_price
    result.entry_time = entry_time

    # Phase 2: Look for exit after entry
    take_profit_price = entry_price * (1 + config.take_profit_pct / 100)
    stop_loss_price = entry_price * (1 - config.stop_loss_pct / 100)

    exit_price = None
    exit_time = None
    trigger_type = "timeout"

    for bar in bars[entry_bar_idx:]:
        # Check for take profit (hit the high)
        if bar.high >= take_profit_price:
            exit_price = take_profit_price
            exit_time = bar.timestamp
            trigger_type = "take_profit"
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
    bars_by_ticker: dict,  # ticker -> List[OHLCVBar]
    config: BacktestConfig,
) -> BacktestSummary:
    """
    Run backtests for all announcements.

    Args:
        announcements: List of announcements to backtest
        bars_by_ticker: Dictionary mapping ticker to OHLCV bars
        config: Backtest configuration

    Returns:
        BacktestSummary with aggregate statistics
    """
    summary = BacktestSummary()
    summary.total_announcements = len(announcements)
    summary.results = []

    returns = []

    for announcement in announcements:
        bars = bars_by_ticker.get(announcement.ticker, [])
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

    return {
        "total_announcements": total,
        "total_trades": len(entered),
        "no_entry": total - len(entered),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": (len(winners) / len(entered) * 100) if entered else 0,
        "avg_return": sum(returns) / len(returns) if returns else 0,
        "total_return": sum(returns) if returns else 0,
        "best_trade": max(returns) if returns else 0,
        "worst_trade": min(returns) if returns else 0,
    }
