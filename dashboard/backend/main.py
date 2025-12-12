"""FastAPI backend for the backtest dashboard."""

import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.massive_client import MassiveClient
from src.backtest import run_backtest, run_single_backtest
from src.models import BacktestConfig, Announcement

app = FastAPI(title="Backtest Dashboard API")

# CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize client
client = MassiveClient()


class BacktestConfigRequest(BaseModel):
    entry_trigger_pct: float = 5.0
    take_profit_pct: float = 10.0
    stop_loss_pct: float = 3.0
    volume_threshold: int = 0
    window_minutes: int = 120
    entry_by_message_second: bool = False


class BacktestResponse(BaseModel):
    results: list[dict]
    summary: dict


@app.get("/api/announcements")
async def get_announcements():
    """Get all cached announcements."""
    announcements = client.load_announcements()
    return [_announcement_to_dict(a) for a in announcements]


@app.post("/api/backtest")
async def run_backtest_endpoint(config: BacktestConfigRequest):
    """Run backtest on all announcements with given config."""
    announcements = client.load_announcements()

    if not announcements:
        return {"results": [], "summary": _empty_summary()}

    # Load OHLCV data for all announcements
    bars_by_announcement = {}
    for ann in announcements:
        bars = client.fetch_after_announcement(
            ann.ticker,
            ann.timestamp,
            window_minutes=config.window_minutes,
            use_cache=True,
        )
        if bars:
            bars_by_announcement[(ann.ticker, ann.timestamp)] = bars

    # Run backtest
    backtest_config = BacktestConfig(
        entry_trigger_pct=config.entry_trigger_pct,
        take_profit_pct=config.take_profit_pct,
        stop_loss_pct=config.stop_loss_pct,
        volume_threshold=config.volume_threshold,
        window_minutes=config.window_minutes,
        entry_by_message_second=config.entry_by_message_second,
    )

    summary = run_backtest(announcements, bars_by_announcement, backtest_config)

    # Convert results to dicts with announcement info
    results = []
    for i, result in enumerate(summary.results):
        ann = announcements[i]
        results.append({
            "ticker": ann.ticker,
            "timestamp": ann.timestamp.isoformat(),
            "headline": ann.headline,
            "channel": ann.channel,
            "price_threshold": ann.price_threshold,
            "market_session": ann.market_session,
            "float_shares": ann.float_shares,
            "io_percent": ann.io_percent,
            "market_cap": ann.market_cap,
            "short_interest": ann.short_interest,
            "high_ctb": ann.high_ctb,
            "country": ann.country,
            "finbert_label": ann.finbert_label,
            "finbert_score": ann.finbert_score,
            "gap_pct": ann.gap_pct,
            "premarket_dollar_volume": ann.premarket_dollar_volume,
            "financing_type": ann.financing_type,
            "scanner_gain_pct": ann.scanner_gain_pct,
            "rvol": ann.rvol,
            "mention_count": ann.mention_count,
            "is_nhod": ann.is_nhod,
            "is_nsh": ann.is_nsh,
            "has_news": ann.has_news,
            # Backtest results
            "entry_price": result.entry_price,
            "exit_price": result.exit_price,
            "return_pct": result.return_pct,
            "trigger_type": result.trigger_type,
            "entry_time": result.entry_time.isoformat() if result.entry_time else None,
            "exit_time": result.exit_time.isoformat() if result.exit_time else None,
        })

    return {
        "results": results,
        "summary": {
            "total_announcements": summary.total_announcements,
            "total_trades": summary.total_trades,
            "winners": summary.winners,
            "losers": summary.losers,
            "win_rate": summary.win_rate,
            "avg_return": summary.avg_return,
            "total_return": summary.total_return,
            "profit_factor": summary.profit_factor,
            "best_trade": summary.best_trade,
            "worst_trade": summary.worst_trade,
            "no_entry_count": summary.no_entry_count,
            "no_data_count": summary.no_data_count,
        }
    }


@app.get("/api/ohlcv/{ticker}/{timestamp}")
async def get_ohlcv(ticker: str, timestamp: str, window_minutes: int = 120):
    """Get OHLCV data for a specific ticker and timestamp."""
    try:
        ts = datetime.fromisoformat(timestamp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    bars = client.fetch_after_announcement(
        ticker,
        ts,
        window_minutes=window_minutes,
        use_cache=True,
    )

    if not bars:
        return []

    return [
        {
            "timestamp": bar.timestamp.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]


def _announcement_to_dict(ann: Announcement) -> dict:
    """Convert Announcement to dict."""
    return {
        "ticker": ann.ticker,
        "timestamp": ann.timestamp.isoformat(),
        "headline": ann.headline,
        "channel": ann.channel,
        "price_threshold": ann.price_threshold,
        "market_session": ann.market_session,
        "float_shares": ann.float_shares,
        "io_percent": ann.io_percent,
        "market_cap": ann.market_cap,
        "short_interest": ann.short_interest,
        "high_ctb": ann.high_ctb,
        "reg_sho": ann.reg_sho,
        "country": ann.country,
        "finbert_label": ann.finbert_label,
        "finbert_score": ann.finbert_score,
        "gap_pct": ann.gap_pct,
        "premarket_dollar_volume": ann.premarket_dollar_volume,
        "financing_type": ann.financing_type,
        "scanner_gain_pct": ann.scanner_gain_pct,
        "rvol": ann.rvol,
        "mention_count": ann.mention_count,
        "is_nhod": ann.is_nhod,
        "is_nsh": ann.is_nsh,
        "has_news": ann.has_news,
        "green_bars": ann.green_bars,
        "bar_minutes": ann.bar_minutes,
        "scanner_test": ann.scanner_test,
        "scanner_after_lull": ann.scanner_after_lull,
    }


def _empty_summary() -> dict:
    """Return empty summary dict."""
    return {
        "total_announcements": 0,
        "total_trades": 0,
        "winners": 0,
        "losers": 0,
        "win_rate": 0.0,
        "avg_return": 0.0,
        "total_return": 0.0,
        "profit_factor": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "no_entry_count": 0,
        "no_data_count": 0,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
