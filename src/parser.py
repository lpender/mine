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
    """
    Parse country code from flag emoji.

    Handles:
    - Discord text format: ':flag_us:' -> 'US'
    - Unicode flag emoji: '🇺🇸' -> 'US'
    """
    # Try Discord text format first
    match = re.search(r':flag_(\w+):', flag_str)
    if match:
        return match.group(1).upper()

    # Try Unicode flag emoji (regional indicator symbols)
    # Flags are pairs of regional indicator symbols (U+1F1E6 to U+1F1FF)
    for i, char in enumerate(flag_str):
        if '\U0001F1E6' <= char <= '\U0001F1FF':
            # Found first regional indicator, look for second
            if i + 1 < len(flag_str):
                char2 = flag_str[i + 1]
                if '\U0001F1E6' <= char2 <= '\U0001F1FF':
                    # Convert regional indicators to letters (A=0, B=1, etc.)
                    letter1 = chr(ord('A') + ord(char) - ord('\U0001F1E6'))
                    letter2 = chr(ord('A') + ord(char2) - ord('\U0001F1E6'))
                    return letter1 + letter2

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


def extract_scanner_gain_pct(line: str) -> Optional[float]:
    """
    Extract standalone percentage gain from scanner messages.

    These indicate how much the stock has already moved (e.g., "| 42%" or "16% ~").
    Returns None if no standalone percentage is found.
    """
    # Find all percentages in the line
    all_pcts = re.findall(r'(\d+(?:\.\d+)?)\s*%', line)

    if not all_pcts:
        return None

    # Check each percentage - if any is NOT labeled, it's a gain percentage
    for pct in all_pcts:
        pct_pattern = rf'{re.escape(pct)}\s*%'
        # Check if this percentage is labeled (IO:, SI:, etc.)
        labeled_patterns = [
            rf'IO\s*:\s*{pct_pattern}',
            rf'SI\s*:\s*{pct_pattern}',
        ]
        is_labeled = any(re.search(p, line) for p in labeled_patterns)

        if not is_labeled:
            # Found an unlabeled percentage - this is a gain indicator
            return float(pct)

    return None


def parse_message_line(line: str, timestamp: datetime, source_message: Optional[str] = None) -> Optional[Announcement]:
    """
    Parse a single announcement line like:
    'BNKK  < $.50c  - Bonk, Inc. Provides 2026 Guidance... - Link  ~  :flag_us:  |  Float: 139 M  |  IO: 6.04%  |  MC: 26.8 M'

    Also handles newer format with timestamp/arrow prefix:
    '12:15 ↗ TE < $6 ~ :flag_us: | Float: 158 M | IO: 40.99% | MC: 1.2 B'

    Scanner format with gain percentage:
    '08:26 ↗ CAUD < $30 | 16% ~ | Float: 2.6 M | IO: 18.96%'

    Args:
        line: The message line to parse
        timestamp: Timestamp for the announcement
        source_message: Optional raw source message/HTML that generated this
    """
    line = line.strip()
    if not line:
        return None

    # Normalize whitespace (HTML parsing can leave newlines in text)
    line = ' '.join(line.split())

    # Extract ticker - look for uppercase letters followed by < $
    # This handles both old format (ticker at start) and new format (timestamp ↗ TICKER < $)
    # Also handles markdown bold format: **TICKER** < $
    ticker_match = re.search(r'\*{0,2}([A-Z]{2,5})\*{0,2}\s*<\s*\$', line)
    if not ticker_match:
        return None
    ticker = ticker_match.group(1)

    # Extract direction arrow (↑ = up, ↗ = up_right)
    direction = None
    if '↑' in line:
        direction = 'up'
    elif '↗' in line:
        direction = 'up_right'

    # Extract price threshold (< $X)
    price_match = re.search(r'<\s*(\$[\d.]+c?)', line)
    price_threshold = parse_price(price_match.group(1)) if price_match else None
    if price_threshold is None:
        return None  # Price is required

    # Extract headline (between price and 'Link' before the '~' separator)
    # Traditional format: "TICKER < $X - HEADLINE - Link ~ ..."
    # Scanner format: "TIME ↑ TICKER < $X ~ ... X ago PR HEADLINE - Link,..."
    # Only look for headline before the first '~' to avoid matching news refs
    pre_tilde = line.split('~')[0] if '~' in line else line
    headline_match = re.search(r'<\s*\$[\d.]+c?\s+-\s*(.+?)\s*-?\s*Link', pre_tilde)
    headline = headline_match.group(1).strip() if headline_match else ""

    # Scanner format: look for "X ago PR HEADLINE - Link" pattern after the ~ section
    # Example: "2 days ago PR Aurora Expands Leading Portfolio... - Link"
    # Also handles: "a day ago", "an hour ago", "yesterday", "today"
    if not headline:
        # Match patterns like "X ago PR HEADLINE - Link" or "X ago AR HEADLINE - Link"
        scanner_headline_match = re.search(
            r'(?:(?:\d+|a|an)\s+(?:days?|hours?|minutes?)\s+ago|yesterday|today)\s+(?:PR|AR)\s+(.+?)\s*-\s*Link',
            line,
            re.IGNORECASE
        )
        if scanner_headline_match:
            headline = scanner_headline_match.group(1).strip()

    # Validate headline - reject if it looks like SEC form number or garbage
    if headline and (len(headline) <= 2 or headline.startswith('Link') or re.match(r'^[\d\-]+$', headline)):
        headline = ""

    # Extract country from flag
    country = parse_country_from_flag(line)

    # Extract Float (handle optional space before colon from HTML parsing)
    # Also handles markdown bold: **Float**: value
    float_match = re.search(r'\*{0,2}Float\*{0,2}\s*:\s*([\d.]+\s*[kKmMbB]?)', line)
    float_shares = parse_value_with_suffix(float_match.group(1)) if float_match else None

    # Extract IO%
    io_match = re.search(r'IO\s*:\s*([\d.]+)%', line)
    io_percent = float(io_match.group(1)) if io_match else None

    # Extract MC (Market Cap)
    mc_match = re.search(r'MC\s*:\s*([\d.]+\s*[kKmMbB]?)', line)
    market_cap = parse_value_with_suffix(mc_match.group(1)) if mc_match else None

    # Check for optional flags
    reg_sho = 'Reg SHO' in line
    high_ctb = 'High CTB' in line

    # Extract Short Interest
    si_match = re.search(r'SI\s*:\s*([\d.]+)%', line)
    short_interest = float(si_match.group(1)) if si_match else None

    # Scanner-specific fields
    scanner_gain_pct = extract_scanner_gain_pct(line)
    is_nhod = 'NHOD' in line
    is_nsh = 'NSH' in line

    # Extract RVol (relative volume)
    # Also handles markdown bold: **RVol**: value
    rvol_match = re.search(r'\*{0,2}RVol\*{0,2}\s*:\s*([\d.]+)', line)
    rvol = float(rvol_match.group(1)) if rvol_match else None

    # Extract mention count / intraday mentions (• 3 or · 3 pattern)
    # Handles both bullet point (•) and middle dot (·)
    mention_match = re.search(r'[•·]\s*(\d+)', line)
    mention_count = int(mention_match.group(1)) if mention_match else None

    # Extract green bars pattern (e.g., "3 green bars 2m")
    green_bars_match = re.search(r'(\d+)\s+green\s+bars?\s+(\d+)m', line, re.IGNORECASE)
    green_bars = int(green_bars_match.group(1)) if green_bars_match else None
    bar_minutes = int(green_bars_match.group(2)) if green_bars_match else None

    # Scanner type detection
    scanner_test = bool('test' in line.lower() and ('scanner' in line.lower() or re.search(r'\btest\b', line, re.IGNORECASE)))
    scanner_after_lull = 'after-lull' in line.lower() or 'after_lull' in line.lower()

    # Detect if this has news (PR/AR/SEC) or is scanner-only
    # News messages have "- Link" with headline, scanner-only don't
    has_news = bool(headline) or 'PR' in line or 'SEC' in line or 'AR' in line

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
        direction=direction,
        scanner_gain_pct=scanner_gain_pct,
        is_nhod=is_nhod,
        is_nsh=is_nsh,
        rvol=rvol,
        mention_count=mention_count,
        has_news=has_news,
        green_bars=green_bars,
        bar_minutes=bar_minutes,
        scanner_test=scanner_test,
        scanner_after_lull=scanner_after_lull,
        source_message=source_message or line,  # Default to the parsed line if no source provided
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

    Finds messages by looking for:
    1. <li id="chat-messages-*"> elements (anywhere in the document)
    2. <li class="*messageListItem*"> elements (fallback)

    Returns:
        Tuple of (announcements, stats dict with keys:
            total_messages, filtered_by_cutoff, not_ticker_pattern, parsed, error)
    """
    soup = BeautifulSoup(html, 'html.parser')
    announcements = []
    stats = {
        "total_messages": 0,
        "filtered_by_cutoff": 0,
        "not_ticker_pattern": 0,
        "parsed": 0,
        "error": None,
    }

    # Default cutoff: start of today in Eastern time - exclude today's messages
    if cutoff_date is None:
        # Get current time in ET, then get midnight ET, then convert to UTC (naive)
        now_et = datetime.now(ET_TZ)
        midnight_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert to UTC and make naive (Discord timestamps are UTC)
        cutoff_date = midnight_et.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    # Find all message elements - try multiple selectors
    # Method 1: Find by id pattern (chat-messages-*)
    messages = soup.find_all('li', id=lambda x: x and x.startswith('chat-messages-'))

    # Method 2: Fallback to class pattern if no id matches found
    if not messages:
        messages = soup.find_all('li', class_=lambda x: x and 'messageListItem' in x)

    # Method 3: Look for any element with message-content-* div inside
    if not messages:
        content_divs = soup.find_all('div', id=lambda x: x and x.startswith('message-content-'))
        # Find parent li elements
        seen_parents = set()
        messages = []
        for div in content_divs:
            parent = div.find_parent('li')
            if parent and id(parent) not in seen_parents:
                messages.append(parent)
                seen_parents.add(id(parent))

    stats["total_messages"] = len(messages)

    if not messages:
        # Try to provide helpful error message
        if '<li' not in html:
            stats["error"] = "No <li> elements found in HTML"
        elif '<time' not in html:
            stats["error"] = "No timestamp elements found in HTML"
        else:
            stats["error"] = "Could not find Discord message elements (expected id='chat-messages-*' or class='*messageListItem*')"
        return announcements, stats

    def extract_author_from_msg(msg_elem) -> Optional[str]:
        """
        Best-effort author extraction from Discord message HTML.

        Discord DOM/classes change frequently, so this uses a few heuristics:
        - any element whose class contains 'username' (case-insensitive)
        - fallback to aria-label patterns when present
        """
        # Heuristic 1: class contains 'username'
        try:
            for el in msg_elem.find_all(True):
                classes = el.get("class") or []
                if not classes:
                    continue
                cls_joined = " ".join(classes).lower()
                if "username" in cls_joined:
                    text = el.get_text(separator=" ", strip=True)
                    if text:
                        return text
        except Exception:
            pass

        # Heuristic 2: aria-label (sometimes contains author in copied HTML)
        try:
            for el in msg_elem.find_all(True):
                aria = el.get("aria-label")
                if not aria:
                    continue
                # Common-ish patterns: "PR - Spike, Today at 9:28 AM" etc.
                # Keep it conservative: split on comma and take first chunk if short-ish.
                first = aria.split(",")[0].strip()
                if first and 1 <= len(first) <= 80:
                    return first
        except Exception:
            pass

        return None

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

        # Replace emoji img elements with their alt text (contains flag codes like :flag_cn:)
        # BeautifulSoup's get_text() doesn't include img alt text by default
        for img in content_div.find_all('img', class_='emoji'):
            alt = img.get('alt', '') or img.get('data-name', '')
            if alt:
                img.replace_with(alt)

        # Get text content
        message_text = content_div.get_text(separator=' ', strip=True)

        # Capture the raw HTML for this message
        source_html = str(msg)

        # Try to parse as announcement
        announcement = parse_message_line(message_text, timestamp, source_message=source_html)
        if announcement:
            announcement.author = extract_author_from_msg(msg)
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
