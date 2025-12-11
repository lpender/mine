import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from .models import Announcement

ET_TZ = ZoneInfo("America/New_York")


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
    - '8:00 AM' (assumes reference date)
    - '12/5/25, 8:13 AM' (explicit date)
    - '12/05/2025, 8:13 AM' (explicit date, full year)
    """
    if reference_date is None:
        reference_date = datetime.now()

    time_str = time_str.strip()

    # Try explicit date formats first: '12/5/25, 8:13 AM' or '12/05/2025, 8:13 AM'
    date_time_match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{2,4}),?\s*(.+)', time_str)
    if date_time_match:
        month = int(date_time_match.group(1))
        day = int(date_time_match.group(2))
        year = int(date_time_match.group(3))
        if year < 100:
            year += 2000  # Convert 25 -> 2025
        time_part = date_time_match.group(4).strip()

        try:
            time_obj = datetime.strptime(time_part, '%I:%M %p').time()
        except ValueError:
            try:
                time_obj = datetime.strptime(time_part, '%H:%M').time()
            except ValueError:
                time_obj = datetime.min.time()

        from datetime import date
        return datetime.combine(date(year, month, day), time_obj)

    # Extract date context (Yesterday, Today, or assume reference date)
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


def parse_iso_timestamp(iso_str: str) -> datetime:
    """
    Parse ISO 8601 timestamp with millisecond precision.
    E.g., '2025-12-10T12:00:08.445Z' -> datetime with ms
    """
    # Handle Z suffix (UTC)
    iso_str = iso_str.replace('Z', '+00:00')

    # Try parsing with microseconds first
    try:
        # Python's fromisoformat handles milliseconds as microseconds
        dt = datetime.fromisoformat(iso_str)
        # Convert to naive datetime (remove timezone for consistency)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except ValueError:
        pass

    # Fallback: try without timezone
    try:
        if '.' in iso_str:
            dt = datetime.strptime(iso_str.split('+')[0], '%Y-%m-%dT%H:%M:%S.%f')
        else:
            dt = datetime.strptime(iso_str.split('+')[0], '%Y-%m-%dT%H:%M:%S')
        return dt
    except ValueError:
        return datetime.now()


def parse_discord_html(html: str, cutoff_date: Optional[datetime] = None) -> List[Announcement]:
    """
    Parse Discord HTML export with millisecond-precision timestamps.

    Extracts timestamps from <time datetime="2025-12-10T12:00:08.445Z">
    and message content from <div id="message-content-...">

    Args:
        html: Discord HTML content
        cutoff_date: Only include messages before this datetime (UTC).
                    Defaults to start of today in Eastern time (excludes today's messages).
    """
    announcements, _ = parse_discord_html_with_stats(html, cutoff_date)
    return announcements


def parse_discord_html_with_stats(
    html: str, cutoff_date: Optional[datetime] = None
) -> Tuple[List[Announcement], dict]:
    """
    Parse Discord HTML with stats about what was parsed/filtered.

    Returns:
        Tuple of (announcements, stats dict with keys:
            total_messages, filtered_by_cutoff, not_ticker_pattern, parsed)
    """
    soup = BeautifulSoup(html, 'html.parser')
    announcements = []
    stats = {
        "total_messages": 0,
        "filtered_by_cutoff": 0,
        "not_ticker_pattern": 0,
        "parsed": 0,
    }

    # Default cutoff: start of today in Eastern time - exclude today's messages
    if cutoff_date is None:
        # Get current time in ET, then get midnight ET, then convert to UTC (naive)
        now_et = datetime.now(ET_TZ)
        midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert to UTC and make naive (Discord timestamps are UTC)
        cutoff_date = midnight_et.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    # Find all message list items
    messages = soup.find_all('li', class_=lambda x: x and 'messageListItem' in x)
    stats["total_messages"] = len(messages)

    for msg in messages:
        # Extract timestamp from <time datetime="...">
        time_elem = msg.find('time')
        if not time_elem or not time_elem.get('datetime'):
            continue

        timestamp = parse_iso_timestamp(time_elem['datetime'])

        # Skip messages from today or after cutoff
        if timestamp >= cutoff_date:
            stats["filtered_by_cutoff"] += 1
            continue

        # Extract message content
        content_div = msg.find('div', id=lambda x: x and x.startswith('message-content-'))
        if not content_div:
            continue

        # Get text content, replacing elements appropriately
        message_text = content_div.get_text(separator=' ', strip=True)

        # Try to parse as announcement
        announcement = parse_message_line(message_text, timestamp)
        if announcement:
            announcements.append(announcement)
            stats["parsed"] += 1
        else:
            stats["not_ticker_pattern"] += 1

    return announcements, stats


def parse_auto(text: str, reference_date: Optional[datetime] = None) -> List[Announcement]:
    """
    Auto-detect format and parse accordingly.
    Supports: HTML (Discord export), plain text Discord paste, simple format.
    """
    text = text.strip()

    # Check if it's HTML
    if '<' in text and ('messageListItem' in text or '<time datetime=' in text):
        return parse_discord_html(text)

    # Check if it's simple format with timestamps
    if text.startswith('[') and re.match(r'\[\d{4}-\d{2}-\d{2}', text):
        return parse_simple_format(text)

    # Default to Discord text paste format
    return parse_discord_messages(text, reference_date)
