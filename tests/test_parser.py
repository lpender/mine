"""Tests for Discord HTML parser."""

import pytest
from datetime import datetime
from src.parser import (
    parse_message_line,
    parse_discord_html_with_stats,
    parse_value_with_suffix,
    parse_price,
    parse_country_from_flag,
)


class TestParseValueWithSuffix:
    def test_millions(self):
        assert parse_value_with_suffix("55.0 M") == 55_000_000.0
        assert parse_value_with_suffix("139 M") == 139_000_000.0
        assert parse_value_with_suffix("3.9 M") == 3_900_000.0

    def test_thousands(self):
        assert parse_value_with_suffix("490 k") == 490_000.0
        assert parse_value_with_suffix("1.5 K") == 1_500.0

    def test_billions(self):
        assert parse_value_with_suffix("7.7 B") == 7_700_000_000.0
        assert parse_value_with_suffix("1 b") == 1_000_000_000.0

    def test_no_suffix(self):
        assert parse_value_with_suffix("123.45") == 123.45

    def test_empty(self):
        assert parse_value_with_suffix("") is None
        assert parse_value_with_suffix(None) is None


class TestParsePrice:
    def test_dollar_cent(self):
        assert parse_price("$.50c") == 0.50
        assert parse_price("$0.50c") == 0.50

    def test_dollar_only(self):
        assert parse_price("$4") == 4.0
        assert parse_price("$13") == 13.0
        assert parse_price("$3") == 3.0

    def test_decimal(self):
        assert parse_price("$1.25") == 1.25


class TestParseCountryFromFlag:
    def test_us_flag(self):
        assert parse_country_from_flag(":flag_us:") == "US"

    def test_cn_flag(self):
        assert parse_country_from_flag(":flag_cn:") == "CN"

    def test_il_flag(self):
        assert parse_country_from_flag(":flag_il:") == "IL"

    def test_in_message(self):
        assert parse_country_from_flag("some text :flag_us: more text") == "US"

    def test_no_flag(self):
        assert parse_country_from_flag("no flag here") == "UNKNOWN"


class TestParseMessageLine:
    def test_basic_announcement(self):
        line = "JZXN  < $.50c  - Jiuzi Holdings Inc. Announce Reverse Split Record Date - Link  ~  :flag_cn:  |  Float: 55.0 M  |  IO: 7.65%  |  MC: 9.9 M"
        timestamp = datetime(2025, 12, 8, 9, 28, 14)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.ticker == "JZXN"
        assert ann.price_threshold == 0.50
        assert ann.country == "CN"
        assert ann.float_shares == 55_000_000.0
        assert ann.io_percent == 7.65
        assert ann.market_cap == 9_900_000.0
        assert ann.high_ctb is False
        assert ann.short_interest is None

    def test_announcement_with_high_ctb(self):
        line = "TEST  < $2  - Some Headline - Link  ~  :flag_us:  |  Float: 10 M  |  IO: 15.5%  |  MC: 50 M  |  High CTB"
        timestamp = datetime(2025, 12, 8, 10, 0, 0)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.ticker == "TEST"
        assert ann.high_ctb is True

    def test_announcement_with_short_interest(self):
        line = "ABCD  < $5  - News Headline - Link  ~  :flag_us:  |  Float: 20 M  |  IO: 8.2%  |  MC: 100 M  |  SI: 23.9%"
        timestamp = datetime(2025, 12, 8, 10, 0, 0)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.ticker == "ABCD"
        assert ann.short_interest == 23.9

    def test_announcement_with_reg_sho(self):
        line = "XYZ  < $1  - Company News - Link  ~  :flag_us:  |  Float: 5 M  |  IO: 12%  |  MC: 25 M  |  Reg SHO"
        timestamp = datetime(2025, 12, 8, 10, 0, 0)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.reg_sho is True

    def test_announcement_with_all_flags(self):
        line = "FULL  < $3  - Full Test - Link  ~  :flag_il:  |  Float: 100 M  |  IO: 25.5%  |  MC: 500 M  |  SI: 45.2%  |  High CTB  |  Reg SHO"
        timestamp = datetime(2025, 12, 8, 10, 0, 0)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.ticker == "FULL"
        assert ann.country == "IL"
        assert ann.float_shares == 100_000_000.0
        assert ann.io_percent == 25.5
        assert ann.market_cap == 500_000_000.0
        assert ann.short_interest == 45.2
        assert ann.high_ctb is True
        assert ann.reg_sho is True

    def test_no_ticker_pattern(self):
        line = "This is not a valid announcement line"
        timestamp = datetime(2025, 12, 8, 10, 0, 0)

        ann = parse_message_line(line, timestamp)
        assert ann is None

    def test_missing_price(self):
        line = "TEST - Some Headline - Link"
        timestamp = datetime(2025, 12, 8, 10, 0, 0)

        ann = parse_message_line(line, timestamp)
        assert ann is None

    def test_real_img_example(self):
        """Test with text that would come from HTML after img replacement."""
        line = "IMG  < $3  - CIMG Inc. Announces Execution of Computing Power Product Sales Contracts - Link  ~  :flag_cn:  |  Float: 30.9 M  |  IO: 0.54%  |  MC: 70.7 M"
        timestamp = datetime(2025, 12, 8, 9, 30, 50)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.ticker == "IMG"
        assert ann.price_threshold == 3.0
        assert ann.country == "CN"
        assert ann.float_shares == 30_900_000.0
        assert ann.io_percent == 0.54
        assert ann.market_cap == 70_700_000.0

    def test_new_format_with_timestamp_arrow(self):
        """Test newer format with timestamp and arrow prefix."""
        line = "12:15 ↗ TE < $6 ~ :flag_us: | Float: 158 M | IO: 40.99% | MC: 1.2 B"
        timestamp = datetime(2025, 12, 11, 12, 15, 0)

        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.ticker == "TE"
        assert ann.price_threshold == 6.0
        assert ann.country == "US"
        assert ann.float_shares == 158_000_000.0
        assert ann.io_percent == 40.99
        assert ann.market_cap == 1_200_000_000.0


class TestParseDiscordHtml:
    """Test full HTML parsing with realistic Discord HTML."""

    SAMPLE_HTML = '''
    <div class="messagesWrapper__36d07">
        <ol class="scrollerInner__36d07">
            <li id="chat-messages-123-456" class="messageListItem__5126c">
                <div class="message__5126c">
                    <div class="contents_c19a55">
                        <h3 class="header_c19a55">
                            <span class="timestamp_c19a55">
                                <time id="message-timestamp-456" datetime="2025-12-08T14:28:14.369Z">12/8/25, 9:28 AM</time>
                            </span>
                        </h3>
                        <div id="message-content-456" class="markup__75297 messageContent_c19a55">
                            <strong><span>JZXN</span></strong>
                            <span>  &lt; $.50c  - Jiuzi Holdings Inc. Announce Reverse Split Record Date </span>
                            <a href="#">- Link</a>
                            <span>  ~  </span>
                            <img class="emoji" alt=":flag_cn:" src="/assets/flag.svg">
                            <span>  |  </span>
                            <strong><span>Float</span></strong><span>: 55.0 M  |  </span>
                            <strong><span>IO</span></strong><span>: 7.65%  |  </span>
                            <strong><span>MC</span></strong><span>: 9.9 M</span>
                        </div>
                    </div>
                </div>
            </li>
            <li id="chat-messages-123-789" class="messageListItem__5126c">
                <div class="message__5126c">
                    <div class="contents_c19a55">
                        <h3 class="header_c19a55">
                            <span class="timestamp_c19a55">
                                <time id="message-timestamp-789" datetime="2025-12-08T14:30:50.379Z">12/8/25, 9:30 AM</time>
                            </span>
                        </h3>
                        <div id="message-content-789" class="markup__75297 messageContent_c19a55">
                            <strong><span>IMG</span></strong>
                            <span>  &lt; $3  - CIMG Inc. Announces News </span>
                            <a href="#">- Link</a>
                            <span>  ~  </span>
                            <img class="emoji" alt=":flag_cn:" src="/assets/flag.svg">
                            <span>  |  </span>
                            <strong><span>Float</span></strong><span>: 30.9 M  |  </span>
                            <strong><span>IO</span></strong><span>: 0.54%  |  </span>
                            <strong><span>MC</span></strong><span>: 70.7 M  |  </span>
                            <span>High CTB</span>
                        </div>
                    </div>
                </div>
            </li>
        </ol>
    </div>
    '''

    def test_parse_html_extracts_announcements(self):
        # Use a cutoff date in the future to include all messages
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, stats = parse_discord_html_with_stats(self.SAMPLE_HTML, cutoff)

        assert stats["total_messages"] == 2
        assert stats["parsed"] == 2
        assert len(announcements) == 2

    def test_parse_html_extracts_fields(self):
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(self.SAMPLE_HTML, cutoff)

        # First announcement: JZXN
        jzxn = announcements[0]
        assert jzxn.ticker == "JZXN"
        assert jzxn.price_threshold == 0.50
        assert jzxn.country == "CN"
        assert jzxn.float_shares == 55_000_000.0
        assert jzxn.io_percent == 7.65
        assert jzxn.market_cap == 9_900_000.0

        # Second announcement: IMG with High CTB
        img = announcements[1]
        assert img.ticker == "IMG"
        assert img.price_threshold == 3.0
        assert img.country == "CN"
        assert img.float_shares == 30_900_000.0
        assert img.io_percent == 0.54
        assert img.market_cap == 70_700_000.0
        assert img.high_ctb is True

    def test_parse_html_filters_by_cutoff(self):
        # Cutoff before both messages
        cutoff = datetime(2025, 12, 8, 14, 0, 0)  # UTC, before both messages
        announcements, stats = parse_discord_html_with_stats(self.SAMPLE_HTML, cutoff)

        assert stats["filtered_by_cutoff"] == 2
        assert len(announcements) == 0

    def test_parse_html_timestamp_extraction(self):
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(self.SAMPLE_HTML, cutoff)

        # Timestamps should be parsed from ISO format (UTC)
        assert announcements[0].timestamp.year == 2025
        assert announcements[0].timestamp.month == 12
        assert announcements[0].timestamp.day == 8
        assert announcements[0].timestamp.hour == 14  # UTC
        assert announcements[0].timestamp.minute == 28


class TestMarketSession:
    """Test market session detection from timestamps."""

    def test_premarket_session(self):
        line = "TEST  < $1  - News - Link  ~  :flag_us:  |  Float: 10 M  |  IO: 5%  |  MC: 50 M"
        # 8:00 AM ET = premarket
        timestamp = datetime(2025, 12, 8, 8, 0, 0)
        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.market_session == "premarket"

    def test_market_session(self):
        line = "TEST  < $1  - News - Link  ~  :flag_us:  |  Float: 10 M  |  IO: 5%  |  MC: 50 M"
        # 10:30 AM ET = market hours
        timestamp = datetime(2025, 12, 8, 10, 30, 0)
        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.market_session == "market"

    def test_postmarket_session(self):
        line = "TEST  < $1  - News - Link  ~  :flag_us:  |  Float: 10 M  |  IO: 5%  |  MC: 50 M"
        # 5:00 PM ET = postmarket
        timestamp = datetime(2025, 12, 8, 17, 0, 0)
        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.market_session == "postmarket"

    def test_closed_session(self):
        line = "TEST  < $1  - News - Link  ~  :flag_us:  |  Float: 10 M  |  IO: 5%  |  MC: 50 M"
        # 9:00 PM ET = closed
        timestamp = datetime(2025, 12, 8, 21, 0, 0)
        ann = parse_message_line(line, timestamp)

        assert ann is not None
        assert ann.market_session == "closed"
