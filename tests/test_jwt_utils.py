"""Tests for JWT utility functions."""

import base64
import json
import pytest
from unittest.mock import patch

from src.jwt_utils import get_websocket_symbols_limit


def _make_jwt(payload: dict) -> str:
    """Create a mock JWT with the given payload."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b'signature').decode().rstrip("=")
    return f"{header}.{payload_b64}.{signature}"


class TestGetWebsocketSymbolsLimit:
    """Tests for get_websocket_symbols_limit function."""

    def test_valid_jwt_with_websocket_symbols(self):
        """Test parsing a valid JWT with websocket_symbols field."""
        jwt = _make_jwt({"websocket_symbols": 10, "plan": "pro"})
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": jwt}):
            result = get_websocket_symbols_limit()
            assert result == 10

    def test_valid_jwt_string_limit_converted_to_int(self):
        """Test that string values are converted to int."""
        jwt = _make_jwt({"websocket_symbols": "15", "plan": "enterprise"})
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": jwt}):
            result = get_websocket_symbols_limit()
            assert result == 15

    def test_missing_env_var_returns_default(self):
        """Test that missing INSIGHT_SENTRY_KEY returns default of 5."""
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": ""}, clear=False):
            result = get_websocket_symbols_limit()
            assert result == 5

    def test_malformed_jwt_not_three_parts(self):
        """Test that JWT without 3 parts returns default."""
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": "invalid.jwt"}):
            result = get_websocket_symbols_limit()
            assert result == 5

    def test_invalid_base64_payload(self):
        """Test that invalid base64 in payload returns default."""
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": "header.!!!invalid!!!.signature"}):
            result = get_websocket_symbols_limit()
            assert result == 5

    def test_invalid_json_payload(self):
        """Test that invalid JSON in payload returns default."""
        # Valid base64 but not valid JSON
        not_json = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": f"header.{not_json}.signature"}):
            result = get_websocket_symbols_limit()
            assert result == 5

    def test_missing_websocket_symbols_returns_default(self):
        """Test that missing websocket_symbols field returns default."""
        jwt = _make_jwt({"plan": "basic", "user_id": "123"})
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": jwt}):
            result = get_websocket_symbols_limit()
            assert result == 5

    def test_jwt_with_padding_needed(self):
        """Test JWT payload that requires padding to decode."""
        # Create a payload that will have base64 length not divisible by 4
        jwt = _make_jwt({"websocket_symbols": 7, "a": "b"})
        with patch.dict("os.environ", {"INSIGHT_SENTRY_KEY": jwt}):
            result = get_websocket_symbols_limit()
            assert result == 7
