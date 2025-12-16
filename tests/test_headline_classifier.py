"""Tests for headline financing classifier."""

import pytest
from datetime import datetime
from src.features import classify_headline, HeadlineFlags
from src.parser import parse_message_line


class TestClassifyHeadline:
    """Test classify_headline function with real-world examples."""

    def test_reverse_split_with_spaces(self):
        """Should detect '1 for 20 R/S' format."""
        msg = "07:54 ↗ MIGI < $8 ~ :flag_us: | Float: 930 k | IO: 2.33% | MC: 7.5 M | SI: 16.2% ~ 1 for 20 R/S Nov. 21 a day ago SEC Form 8-K - Link"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "reverse_split" in result.tags or "sec_filing" in result.tags

    def test_reverse_split_rs_abbreviation(self):
        """Should detect 'R/S' abbreviation."""
        msg = "ABCD announces 1-for-10 R/S effective next week"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "reverse_split" in result.tags

    def test_reverse_split_with_hyphens(self):
        """Should detect '1-for-20' format (existing pattern)."""
        msg = "Company announces 1-for-20 reverse stock split"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "reverse_split" in result.tags

    def test_sec_8k_filing(self):
        """Should detect '8-K' SEC filing."""
        msg = "SEC Form 8-K filed"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "sec_filing" in result.tags

    def test_offering_priced(self):
        """Should detect 'priced a public offering'."""
        msg = "Company priced a $50M public offering at $2.50"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "offering" in result.tags

    def test_atm_offering(self):
        """Should detect ATM offering."""
        msg = "Company announces at-the-market offering program"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "atm" in result.tags

    def test_warrants(self):
        """Should detect warrants."""
        msg = "Offering includes pre-funded warrants"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "warrants" in result.tags

    def test_pipe_deal(self):
        """Should detect PIPE."""
        msg = "Company closes $20M PIPE financing"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "pipe" in result.tags

    def test_compliance_nasdaq(self):
        """Should detect NASDAQ compliance issues."""
        msg = "Company receives NASDAQ minimum bid price deficiency notice"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert "compliance" in result.tags

    def test_no_financing_regular_news(self):
        """Should NOT flag regular news."""
        msg = "Company reports record Q4 earnings, beats estimates"
        result = classify_headline(msg)
        assert result.is_financing is False
        assert len(result.tags) == 0

    def test_no_financing_fda(self):
        """Should NOT flag FDA news."""
        msg = "Company receives FDA approval for new drug"
        result = classify_headline(msg)
        assert result.is_financing is False

    def test_empty_headline(self):
        """Should handle empty headline."""
        result = classify_headline("")
        assert result.is_financing is False
        assert result.financing_type is None

    def test_none_headline(self):
        """Should handle None headline."""
        result = classify_headline(None)
        assert result.is_financing is False


class TestRealWorldExamples:
    """Test with actual messages from the system."""

    def test_migi_reverse_split(self):
        """Real example: MIGI reverse split + 8-K filing."""
        msg = "07:54 ↗ MIGI < $8 ~ :flag_us: | Float: 930 k | IO: 2.33% | MC: 7.5 M | SI: 16.2% ~ 1 for 20 R/S Nov. 21 a day ago SEC Form 8-K - Link"
        result = classify_headline(msg)
        assert result.is_financing is True, f"Expected financing, got tags: {result.tags}"

    def test_message_with_offering(self):
        """Real example format with offering."""
        msg = "09:30 ↗ XYZ < $5 ~ :flag_us: | Float: 2 M ~ Prices $10M registered direct offering at $4.50"
        result = classify_headline(msg)
        assert result.is_financing is True
        assert result.financing_type == "offering"


class TestParserIntegration:
    """Test that parser correctly populates headline classification fields."""

    def test_parser_sets_financing_fields_for_reverse_split(self):
        """Parser should set headline_is_financing for reverse split messages."""
        msg = "07:54 ↗ MIGI < $8 ~ :flag_us: | Float: 930 k | IO: 2.33% | MC: 7.5 M | SI: 16.2% ~ 1 for 20 R/S Nov. 21 a day ago SEC Form 8-K - Link"
        timestamp = datetime(2024, 12, 16, 7, 54)

        ann = parse_message_line(msg, timestamp, source_message=msg)

        assert ann is not None
        assert ann.ticker == "MIGI"
        assert ann.headline_is_financing is True, f"Expected is_financing=True, got {ann.headline_is_financing}"
        assert ann.headline_financing_type is not None
        assert ann.headline_financing_tags is not None

    def test_parser_sets_financing_fields_for_offering(self):
        """Parser should set headline_is_financing for offering messages."""
        msg = "09:30 ↗ XYZ < $5 ~ :flag_us: | Float: 2 M ~ Prices $10M registered direct offering at $4.50"
        timestamp = datetime(2024, 12, 16, 9, 30)

        ann = parse_message_line(msg, timestamp, source_message=msg)

        assert ann is not None
        assert ann.headline_is_financing is True
        assert ann.headline_financing_type == "offering"

    def test_parser_no_financing_for_regular_news(self):
        """Parser should NOT set headline_is_financing for regular news."""
        msg = "10:00 ↗ ABC < $10 ~ :flag_us: | Float: 5 M ~ Company reports record earnings"
        timestamp = datetime(2024, 12, 16, 10, 0)

        ann = parse_message_line(msg, timestamp, source_message=msg)

        assert ann is not None
        assert ann.headline_is_financing is False
