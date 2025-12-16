"""
JWT utility functions for parsing InsightSentry tokens.
"""

import base64
import json
import logging
import os

logger = logging.getLogger(__name__)


def get_websocket_symbols_limit() -> int:
    """
    Parse InsightSentry JWT token to extract websocket_symbols limit.

    JWT payload contains: {"websocket_symbols": 5, ...}
    Falls back to 5 if parsing fails.
    """
    jwt_token = os.getenv("INSIGHT_SENTRY_KEY", "")
    if not jwt_token:
        logger.warning("INSIGHT_SENTRY_KEY not set, defaulting to 5 max positions")
        return 5

    try:
        # JWT format: header.payload.signature
        parts = jwt_token.split(".")
        if len(parts) != 3:
            logger.warning("Invalid JWT format, defaulting to 5 max positions")
            return 5

        # Decode payload (middle part) - add padding if needed
        payload_b64 = parts[1]
        # JWT uses base64url encoding, add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_json = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_json)

        limit = payload.get("websocket_symbols", 5)
        logger.info(f"Parsed JWT: websocket_symbols={limit}, plan={payload.get('plan', 'unknown')}")
        return int(limit)
    except Exception as e:
        logger.warning(f"Failed to parse JWT token: {e}, defaulting to 5 max positions")
        return 5
