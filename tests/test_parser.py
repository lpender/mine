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


class TestRealHtmlParsing:
    """Test parsing real messages from the pr-spike HTML file."""

    @pytest.fixture
    def html_content(self):
        """Load the real HTML file."""
        from pathlib import Path
        html_path = Path(__file__).parent.parent / "pr-spike-2025-11-13T13-00-05.921Z--2025-11-25T21-00-36.858Z.html"
        if not html_path.exists():
            pytest.skip("HTML test file not found")
        return html_path.read_text()

    def test_vcig_with_reg_sho_and_high_ctb(self, html_content):
        """Test VCIG message with Reg SHO and High CTB flags."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, stats = parse_discord_html_with_stats(html_content, cutoff)

        vcig = next((a for a in announcements if a.ticker == "VCIG"), None)
        assert vcig is not None
        assert vcig.price_threshold == 2.0
        assert vcig.country == "MY"
        assert vcig.float_shares == 6_400_000.0
        assert vcig.io_percent == 0.03
        assert vcig.market_cap == 9_800_000.0
        assert vcig.reg_sho is True
        assert vcig.high_ctb is True
        # Timestamp should be 2025-11-13T13:00:05.921Z (UTC)
        assert vcig.timestamp.year == 2025
        assert vcig.timestamp.month == 11
        assert vcig.timestamp.day == 13

    def test_gfai_singapore(self, html_content):
        """Test GFAI message from Singapore."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        gfai = next((a for a in announcements if a.ticker == "GFAI"), None)
        assert gfai is not None
        assert gfai.price_threshold == 1.0
        assert gfai.country == "SG"
        assert gfai.float_shares == 18_700_000.0
        assert gfai.io_percent == 2.16
        assert gfai.market_cap == 18_700_000.0

    def test_rvyl_50_cent_price(self, html_content):
        """Test RVYL with $.50c price format."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        rvyl = next((a for a in announcements if a.ticker == "RVYL"), None)
        assert rvyl is not None
        assert rvyl.price_threshold == 0.50
        assert rvyl.country == "US"
        assert rvyl.float_shares == 22_200_000.0
        assert rvyl.io_percent == 5.85
        assert rvyl.market_cap == 9_900_000.0

    def test_thar_headline_extraction(self, html_content):
        """Test THAR message headline extraction."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        thar = next((a for a in announcements if a.ticker == "THAR"), None)
        assert thar is not None
        assert thar.price_threshold == 4.0
        assert "FDA" in thar.headline or "Feedback" in thar.headline
        assert thar.float_shares == 6_400_000.0
        assert thar.io_percent == 10.83
        assert thar.market_cap == 112_000_000.0

    def test_rime_small_float(self, html_content):
        """Test RIME with small float."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        rime = next((a for a in announcements if a.ticker == "RIME"), None)
        assert rime is not None
        assert rime.price_threshold == 3.0
        assert rime.country == "US"
        assert rime.float_shares == 2_300_000.0
        assert rime.io_percent == 4.19
        assert rime.market_cap == 6_400_000.0

    def test_chai_canada(self, html_content):
        """Test CHAI message from Canada."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        chai = next((a for a in announcements if a.ticker == "CHAI"), None)
        assert chai is not None
        assert chai.price_threshold == 4.0
        assert chai.country == "CA"
        assert chai.float_shares == 19_900_000.0
        assert chai.io_percent == 0.14
        assert chai.market_cap == 71_200_000.0

    def test_otlk_fda_announcement(self, html_content):
        """Test OTLK FDA announcement."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        otlk = next((a for a in announcements if a.ticker == "OTLK"), None)
        assert otlk is not None
        assert otlk.price_threshold == 2.0
        assert otlk.country == "US"
        assert otlk.float_shares == 29_100_000.0
        assert otlk.io_percent == 13.80
        assert otlk.market_cap == 54_600_000.0

    def test_upxi_with_short_interest(self, html_content):
        """Test UPXI message with short interest."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        upxi = next((a for a in announcements if a.ticker == "UPXI"), None)
        assert upxi is not None
        assert upxi.price_threshold == 4.0
        assert upxi.country == "US"
        assert upxi.float_shares == 52_300_000.0
        assert upxi.io_percent == 11.80
        assert upxi.market_cap == 199_000_000.0
        assert upxi.short_interest == 20.8

    def test_soar_earnings(self, html_content):
        """Test SOAR earnings announcement."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        soar = next((a for a in announcements if a.ticker == "SOAR"), None)
        assert soar is not None
        assert soar.price_threshold == 2.0
        assert soar.country == "US"
        assert soar.float_shares == 6_800_000.0
        assert soar.io_percent == 0.41
        assert soar.market_cap == 10_600_000.0

    def test_tivc_small_market_cap(self, html_content):
        """Test TIVC with small market cap."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        tivc = next((a for a in announcements if a.ticker == "TIVC"), None)
        assert tivc is not None
        assert tivc.price_threshold == 3.0
        assert tivc.country == "US"
        assert tivc.float_shares == 1_600_000.0
        assert tivc.io_percent == 5.69
        assert tivc.market_cap == 3_800_000.0

    def test_ento_with_high_ctb(self, html_content):
        """Test ENTO message with High CTB flag."""
        cutoff = datetime(2025, 12, 31, 0, 0, 0)
        announcements, _ = parse_discord_html_with_stats(html_content, cutoff)

        ento = next((a for a in announcements if a.ticker == "ENTO"), None)
        assert ento is not None
        assert ento.price_threshold == 5.0
        assert ento.country == "US"
        assert ento.float_shares == 1_500_000.0
        assert ento.io_percent == 3.82
        assert ento.market_cap == 6_100_000.0
        assert ento.high_ctb is True


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
