import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from .models import Announcement


def parse_value_with_suffix(value_str: str) -> Optional[float]:
    """Parse a value like '139 M', '3.9 M', '490 k', '7.7 B' into a float."""
    if not value_str:
        return None

    value_str = value_str.strip()
    match = re.match(r'([\d.]+)\s*([kKmMbB])?', value_str)
    if not match:
        return None

    number = float(match.group(1))
    suffix = match.group(2)

    if suffix:
        suffix = suffix.upper()
        if suffix == 'K':
            number *= 1_000
        elif suffix == 'M':
            number *= 1_000_000
        elif suffix == 'B':
            number *= 1_000_000_000

    return number


def parse_price(price_str: str) -> Optional[float]:
    """Parse price like '$.50c', '$4', '$0.50', '$13' into a float."""
    if not price_str:
        return None

    # Remove $ and c suffixes, handle various formats
    price_str = price_str.strip()
    price_str = re.sub(r'[$c]', '', price_str)
    price_str = price_str.strip()

    try:
        return float(price_str)
    except ValueError:
        return None


def parse_country_from_flag(flag_str: str) -> str:
    """Parse country code from Discord flag emoji like ':flag_us:' -> 'US'."""
    match = re.search(r':flag_(\w+):', flag_str)
    if match:
        return match.group(1).upper()
    return "UNKNOWN"


def parse_timestamp(time_str: str, reference_date: Optional[datetime] = None) -> datetime:
    """
    Parse Discord timestamp formats:
    - 'Yesterday at 9:15 AM'
    - 'Today at 4:10 PM'
    - '8:00 AM' (assumes today)
    - '4:00 AM'
    """
    if reference_date is None:
        reference_date = datetime.now()

    time_str = time_str.strip()

    # Extract date context (Yesterday, Today, or assume today)
    if 'yesterday' in time_str.lower():
        base_date = reference_date.date() - timedelta(days=1)
        time_str = re.sub(r'yesterday\s+at\s+', '', time_str, flags=re.IGNORECASE)
    elif 'today' in time_str.lower():
        base_date = reference_date.date()
        time_str = re.sub(r'today\s+at\s+', '', time_str, flags=re.IGNORECASE)
    else:
        base_date = reference_date.date()

    # Parse time portion (e.g., '9:15 AM', '4:10 PM')
    time_str = time_str.strip()
    try:
        time_obj = datetime.strptime(time_str, '%I:%M %p').time()
    except ValueError:
        try:
            time_obj = datetime.strptime(time_str, '%H:%M').time()
        except ValueError:
            # Default to midnight if parsing fails
            time_obj = datetime.min.time()

    return datetime.combine(base_date, time_obj)


def parse_message_line(line: str, timestamp: datetime) -> Optional[Announcement]:
    """
    Parse a single announcement line like:
    'BNKK  < $.50c  - Bonk, Inc. Provides 2026 Guidance... - Link  ~  :flag_us:  |  Float: 139 M  |  IO: 6.04%  |  MC: 26.8 M'
    """
    line = line.strip()
    if not line:
        return None

    # Extract ticker (first word, uppercase letters/numbers)
    ticker_match = re.match(r'^([A-Z]+)', line)
    if not ticker_match:
        return None
    ticker = ticker_match.group(1)

    # Extract price threshold (< $X)
    price_match = re.search(r'<\s*(\$[\d.]+c?)', line)
    price_threshold = parse_price(price_match.group(1)) if price_match else None
    if price_threshold is None:
        return None  # Price is required

    # Extract headline (between first '-' and '- Link')
    headline_match = re.search(r'-\s*(.+?)\s*-\s*Link', line)
    headline = headline_match.group(1).strip() if headline_match else ""

    # Extract country from flag
    country = parse_country_from_flag(line)

    # Extract Float
    float_match = re.search(r'Float:\s*([\d.]+\s*[kKmMbB]?)', line)
    float_shares = parse_value_with_suffix(float_match.group(1)) if float_match else None

    # Extract IO%
    io_match = re.search(r'IO:\s*([\d.]+)%', line)
    io_percent = float(io_match.group(1)) if io_match else None

    # Extract MC (Market Cap)
    mc_match = re.search(r'MC:\s*([\d.]+\s*[kKmMbB]?)', line)
    market_cap = parse_value_with_suffix(mc_match.group(1)) if mc_match else None

    # Check for optional flags
    reg_sho = 'Reg SHO' in line
    high_ctb = 'High CTB' in line

    # Extract Short Interest
    si_match = re.search(r'SI:\s*([\d.]+)%', line)
    short_interest = float(si_match.group(1)) if si_match else None

    return Announcement(
        ticker=ticker,
        timestamp=timestamp,
        price_threshold=price_threshold,
        headline=headline,
        country=country,
        float_shares=float_shares,
        io_percent=io_percent,
        market_cap=market_cap,
        reg_sho=reg_sho,
        high_ctb=high_ctb,
        short_interest=short_interest,
    )


def parse_discord_messages(text: str, reference_date: Optional[datetime] = None) -> List[Announcement]:
    """
    Parse pasted Discord messages into a list of Announcements.

    Handles the Discord format with timestamps like:
    PR - Spike
    APP
     — Yesterday at 9:15 AM
    FGNX  < $4  - FG Nexus Announces... - Link  ~  :flag_us:  |  Float: 35.1 M  |  IO: 29.49%  |  MC: 116 M
    """
    if reference_date is None:
        reference_date = datetime.now()

    announcements = []
    current_timestamp = reference_date

    lines = text.split('\n')

    for line in lines:
        line = line.strip()

        # Skip empty lines and header lines
        if not line or line.startswith('PR -') or line == 'APP':
            continue

        # Check if this is a timestamp line (starts with — or -)
        if line.startswith('—') or (line.startswith('-') and 'AM' in line or 'PM' in line):
            # Extract timestamp
            time_str = line.lstrip('—- ').strip()
            current_timestamp = parse_timestamp(time_str, reference_date)
            continue

        # Try to parse as announcement
        announcement = parse_message_line(line, current_timestamp)
        if announcement:
            announcements.append(announcement)

    return announcements


def parse_simple_format(text: str) -> List[Announcement]:
    """
    Parse simple format with explicit timestamps:
    [2024-01-15 09:30:00] BNKK < $.50c - Headline... - Link ~ :flag_us: | Float: 139 M | IO: 6.04% | MC: 26.8 M
    """
    announcements = []

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Check for timestamp prefix
        timestamp_match = re.match(r'\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s*(.+)', line)
        if timestamp_match:
            timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
            message_line = timestamp_match.group(2)
        else:
            timestamp = datetime.now()
            message_line = line

        announcement = parse_message_line(message_line, timestamp)
        if announcement:
            announcements.append(announcement)

    return announcements
