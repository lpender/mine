"""FastAPI backend for the backtesting dashboard."""

import sys
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.postgres_client import PostgresClient
from src.models import BacktestConfig, get_market_session
from src.backtest import run_backtest, calculate_summary_stats

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
client = PostgresClient()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models for API
# ─────────────────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    entry_trigger_pct: float = 5.0
    take_profit_pct: float = 10.0
    stop_loss_pct: float = 3.0
    volume_threshold: int = 0
    window_minutes: int = 120
    entry_at_candle_close: bool = False
    entry_by_message_second: bool = False


class AnnouncementResponse(BaseModel):
    ticker: str
    timestamp: str
    price_threshold: float
    headline: str
    country: str
    channel: Optional[str]
    market_session: str
    float_shares: Optional[float]
    io_percent: Optional[float]
    market_cap: Optional[float]
    short_interest: Optional[float]
    reg_sho: bool
    high_ctb: bool
    finbert_label: Optional[str]
    finbert_score: Optional[float]
    headline_is_financing: Optional[bool]
    headline_financing_type: Optional[str]
    prev_close: Optional[float]
    regular_open: Optional[float]
    premarket_gap_pct: Optional[float]
    premarket_volume: Optional[int]
    premarket_dollar_volume: Optional[float]
    scanner_gain_pct: Optional[float]
    is_nhod: bool
    is_nsh: bool
    rvol: Optional[float]
    mention_count: Optional[int]
    has_news: bool
    green_bars: Optional[int]
    bar_minutes: Optional[int]
    scanner_test: bool
    scanner_after_lull: bool


class TradeResultResponse(BaseModel):
    ticker: str
    timestamp: str
    headline: str
    market_session: str
    entry_price: Optional[float]
    entry_time: Optional[str]
    exit_price: Optional[float]
    exit_time: Optional[str]
    return_pct: Optional[float]
    trigger_type: str
    # Include announcement data for filtering
    country: str
    channel: Optional[str]
    float_shares: Optional[float]
    io_percent: Optional[float]
    market_cap: Optional[float]
    short_interest: Optional[float]
    reg_sho: bool
    high_ctb: bool
    finbert_label: Optional[str]
    finbert_score: Optional[float]
    headline_is_financing: Optional[bool]
    headline_financing_type: Optional[str]
    prev_close: Optional[float]
    premarket_gap_pct: Optional[float]
    scanner_gain_pct: Optional[float]
    is_nhod: bool
    is_nsh: bool
    has_news: bool


class BacktestResponse(BaseModel):
    results: List[TradeResultResponse]
    summary: dict


class OHLCVBarResponse(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/announcements", response_model=List[AnnouncementResponse])
def get_announcements():
    """Get all announcements."""
    announcements = client.load_announcements()
    return [
        AnnouncementResponse(
            ticker=a.ticker,
            timestamp=a.timestamp.isoformat(),
            price_threshold=a.price_threshold,
            headline=a.headline,
            country=a.country,
            channel=a.channel,
            market_session=a.market_session,
            float_shares=a.float_shares,
            io_percent=a.io_percent,
            market_cap=a.market_cap,
            short_interest=a.short_interest,
            reg_sho=a.reg_sho,
            high_ctb=a.high_ctb,
            finbert_label=a.finbert_label,
            finbert_score=a.finbert_score,
            headline_is_financing=a.headline_is_financing,
            headline_financing_type=a.headline_financing_type,
            prev_close=a.prev_close,
            regular_open=a.regular_open,
            premarket_gap_pct=a.premarket_gap_pct,
            premarket_volume=a.premarket_volume,
            premarket_dollar_volume=a.premarket_dollar_volume,
            scanner_gain_pct=a.scanner_gain_pct,
            is_nhod=a.is_nhod,
            is_nsh=a.is_nsh,
            rvol=a.rvol,
            mention_count=a.mention_count,
            has_news=a.has_news,
            green_bars=a.green_bars,
            bar_minutes=a.bar_minutes,
            scanner_test=a.scanner_test,
            scanner_after_lull=a.scanner_after_lull,
        )
        for a in announcements
    ]


@app.post("/api/backtest", response_model=BacktestResponse)
def run_backtest_endpoint(request: BacktestRequest):
    """Run backtest with given configuration."""
    announcements = client.load_announcements()

    if not announcements:
        raise HTTPException(status_code=404, detail="No announcements found")

    config = BacktestConfig(
        entry_trigger_pct=request.entry_trigger_pct,
        take_profit_pct=request.take_profit_pct,
        stop_loss_pct=request.stop_loss_pct,
        volume_threshold=request.volume_threshold,
        window_minutes=request.window_minutes,
        entry_at_candle_close=request.entry_at_candle_close,
        entry_by_message_second=request.entry_by_message_second,
    )

    # Fetch OHLCV data for all announcements
    bars_by_announcement = {}
    for ann in announcements:
        bars = client.fetch_after_announcement(
            ann.ticker,
            ann.timestamp,
            window_minutes=config.window_minutes,
            use_cache=True,
        )
        bars_by_announcement[(ann.ticker, ann.timestamp)] = bars

    # Run backtest
    summary = run_backtest(announcements, bars_by_announcement, config)
    stats = calculate_summary_stats(summary.results)

    # Convert results to response format
    results = []
    for r in summary.results:
        results.append(TradeResultResponse(
            ticker=r.announcement.ticker,
            timestamp=r.announcement.timestamp.isoformat(),
            headline=r.announcement.headline,
            market_session=r.announcement.market_session,
            entry_price=r.entry_price,
            entry_time=r.entry_time.isoformat() if r.entry_time else None,
            exit_price=r.exit_price,
            exit_time=r.exit_time.isoformat() if r.exit_time else None,
            return_pct=r.return_pct,
            trigger_type=r.trigger_type,
            country=r.announcement.country,
            channel=r.announcement.channel,
            float_shares=r.announcement.float_shares,
            io_percent=r.announcement.io_percent,
            market_cap=r.announcement.market_cap,
            short_interest=r.announcement.short_interest,
            reg_sho=r.announcement.reg_sho,
            high_ctb=r.announcement.high_ctb,
            finbert_label=r.announcement.finbert_label,
            finbert_score=r.announcement.finbert_score,
            headline_is_financing=r.announcement.headline_is_financing,
            headline_financing_type=r.announcement.headline_financing_type,
            prev_close=r.announcement.prev_close,
            premarket_gap_pct=r.announcement.premarket_gap_pct,
            scanner_gain_pct=r.announcement.scanner_gain_pct,
            is_nhod=r.announcement.is_nhod,
            is_nsh=r.announcement.is_nsh,
            has_news=r.announcement.has_news,
        ))

    return BacktestResponse(results=results, summary=stats)


@app.get("/api/ohlcv/{ticker}/{timestamp}", response_model=List[OHLCVBarResponse])
def get_ohlcv(ticker: str, timestamp: str, window_minutes: int = 120):
    """Get OHLCV data for a specific announcement."""
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    bars = client.fetch_after_announcement(
        ticker, dt, window_minutes=window_minutes, use_cache=True
    )

    return [
        OHLCVBarResponse(
            timestamp=b.timestamp.isoformat(),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            vwap=b.vwap,
        )
        for b in bars
    ]


@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
