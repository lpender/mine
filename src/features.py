import re
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple

from .massive_client import MassiveClient
from .models import MARKET_OPEN, OHLCVBar


@dataclass
class HeadlineFlags:
    is_financing: bool
    financing_type: Optional[str]
    tags: List[str]


_FIN_TAGS: List[Tuple[str, str]] = [
    ("offering", r"\b(registered direct|public|underwritten)\s+offering\b"),
    ("offering", r"\b(prices|priced|pricing)\s+an?\s+(registered direct|public|underwritten)\s+offering\b"),
    ("atm", r"\b(at[-\s]?the[-\s]?market|ATM)\b"),
    ("pipe", r"\bPIPE\b"),
    ("warrants", r"\b(pre[-\s]?funded\s+)?warrants?\b"),
    ("convertible", r"\b(convertible|notes?\b.*\bconvertible)\b"),
    ("shelf", r"\b(shelf registration|prospectus)\b"),
    ("sec_filing", r"\b(S-1|F-1|S-3|424B|8-K)\b"),
    ("equity_line", r"\b(equity line|purchase agreement|committed equity)\b"),
    # Reverse split patterns: "reverse split", "1-for-20", "1 for 20", "R/S"
    ("reverse_split", r"\b(reverse (stock )?split)\b"),
    ("reverse_split", r"\b\d+[- ]for[- ]\d+\b"),  # "1-for-20" or "1 for 20"
    ("reverse_split", r"\bR/S\b"),  # R/S abbreviation
    ("compliance", r"\b(nasdaq|nyse)\b.*\b(compliance|deficienc(y|ies)|minimum bid|continued listing)\b"),
]


def classify_headline(headline: str) -> HeadlineFlags:
    """
    Cheap keyword classifier for dilution/financing risk headlines.
    Returns tags + a primary type.
    """
    h = (headline or "").strip()
    if not h:
        return HeadlineFlags(is_financing=False, financing_type=None, tags=[])

    hl = h.lower()
    tags: List[str] = []

    for tag, pattern in _FIN_TAGS:
        if re.search(pattern, h, flags=re.IGNORECASE):
            tags.append(tag)

    # Deduplicate preserving order
    seen = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]

    # Primary type preference (dilution-ish first)
    for primary in ["offering", "atm", "pipe", "convertible", "warrants", "shelf", "equity_line"]:
        if primary in tags:
            return HeadlineFlags(is_financing=True, financing_type=primary, tags=tags)

    # Corporate-action / listing-risk flags (still "avoid" for long-biased strategies)
    for primary in ["reverse_split", "compliance", "sec_filing"]:
        if primary in tags:
            return HeadlineFlags(is_financing=True, financing_type=primary, tags=tags)

    return HeadlineFlags(is_financing=False, financing_type=None, tags=tags)


def _sum_dollar_volume(bars: List[OHLCVBar]) -> float:
    total = 0.0
    for b in bars:
        px = b.vwap if b.vwap is not None else b.close
        total += float(b.volume) * float(px)
    return total


def _find_prev_close(
    client: MassiveClient,
    ticker: str,
    session_date: datetime.date,
    *,
    lookback_days: int = 7,
) -> Optional[float]:
    """
    Best-effort: find the most recent regular-session close before `session_date`.
    Fetches 15:30-16:01 for prior days and takes the last close.
    """
    for i in range(1, lookback_days + 1):
        d = session_date - timedelta(days=i)
        start = datetime.combine(d, time(15, 30))
        end = datetime.combine(d, time(16, 1))
        bars = client.fetch_ohlcv(ticker, start, end)
        if bars:
            return float(bars[-1].close)
    return None


def compute_premarket_features(
    client: MassiveClient,
    ticker: str,
    effective_session_date: datetime.date,
) -> Dict[str, Optional[float]]:
    """
    Compute premarket context features for the given trading date:
    - premarket volume + dollar volume (04:00-09:30)
    - regular open (09:30 bar open)
    - previous close (prior session close)
    - gap % = (open - prev_close) / prev_close * 100
    """
    pre_start = datetime.combine(effective_session_date, time(4, 0))
    pre_end = datetime.combine(effective_session_date, time(9, 30))
    open_start = datetime.combine(effective_session_date, MARKET_OPEN)
    open_end = open_start + timedelta(minutes=1)

    pre_bars = client.fetch_ohlcv(ticker, pre_start, pre_end)
    open_bars = client.fetch_ohlcv(ticker, open_start, open_end)

    pre_vol = int(sum(b.volume for b in pre_bars)) if pre_bars else None
    pre_dv = float(_sum_dollar_volume(pre_bars)) if pre_bars else None

    regular_open = float(open_bars[0].open) if open_bars else None
    prev_close = _find_prev_close(client, ticker, effective_session_date)

    gap_pct = None
    if regular_open is not None and prev_close is not None and prev_close > 0:
        gap_pct = (regular_open - prev_close) / prev_close * 100.0

    return {
        "prev_close": prev_close,
        "regular_open": regular_open,
        "premarket_gap_pct": gap_pct,
        "premarket_volume": pre_vol,
        "premarket_dollar_volume": pre_dv,
    }

