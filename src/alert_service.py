"""
Unified alert service that handles all HTTP endpoints.

Runs as a background thread, started automatically with the dashboard.
Handles:
- POST /alert - Real-time alerts (optionally forwarded to trading engine)
- POST /backfill - Historical message backfill
"""

import json
import logging
import re
import threading
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from queue import Queue
from typing import Callable, Optional

from .parser import parse_message_line
from .postgres_client import PostgresClient

logger = logging.getLogger(__name__)


def _infer_author(channel: str, author: Optional[str]) -> Optional[str]:
    """Infer an author label when upstream payloads don't include one."""
    if author:
        author = str(author).strip()
        if author:
            return author

    ch = (channel or "").lower()
    if "pr-spike" in ch or "pr spike" in ch:
        return "PR - Spike"
    if "select-news" in ch or "select news" in ch:
        return "Nuntiobot"
    return None


class UnifiedAlertHandler(BaseHTTPRequestHandler):
    """HTTP handler for all Discord plugin requests."""

    # Class-level state (set by AlertService)
    alert_callback: Optional[Callable] = None  # Called for each alert when trading active
    include_today: bool = False
    fetch_ohlcv: bool = False
    seen_alerts: set = set()
    seen_backfill: set = set()

    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON: {body[:100]}")
            self._send_error(400, "Invalid JSON")
            return

        if self.path == "/alert":
            self._handle_alert(data)
            self._send_ok()
        elif self.path == "/backfill":
            result = self._handle_backfill(data)
            self._send_ok(result)
        else:
            self._send_error(404, "Not found")

    def _send_ok(self, data=None):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        response = {"status": "ok"}
        if data:
            response.update(data)
        self.wfile.write(json.dumps(response).encode())

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "error", "message": message}).encode())

    def _handle_alert(self, data):
        """Handle real-time alert from Discord plugin."""
        ticker = data.get("ticker", "UNKNOWN")
        price_info = data.get("price_info", "")
        channel = data.get("channel", "")
        content = data.get("content", "")
        author = data.get("author")
        timestamp = data.get("timestamp", datetime.now().isoformat())

        # Dedupe by ticker + minute
        alert_key = f"{ticker}:{timestamp[:16]}"
        if alert_key in UnifiedAlertHandler.seen_alerts:
            return
        UnifiedAlertHandler.seen_alerts.add(alert_key)

        # Limit seen alerts size
        if len(UnifiedAlertHandler.seen_alerts) > 500:
            UnifiedAlertHandler.seen_alerts = set(list(UnifiedAlertHandler.seen_alerts)[-250:])

        # Parse price from the alert
        price_match = re.search(r'\$([0-9.]+)', price_info)
        price = float(price_match.group(1)) if price_match else None

        # Extract just the ticker symbol
        ticker_match = re.match(r'([A-Z]{2,5})', ticker)
        ticker_symbol = ticker_match.group(1) if ticker_match else ticker

        # Log the alert
        now = datetime.now().strftime("%H:%M:%S")
        logger.info(f"ALERT @ {now}: {ticker_symbol} ${price:.2f if price else 0} #{channel}")

        # Forward to trading engine if callback is set
        if UnifiedAlertHandler.alert_callback and content:
            try:
                UnifiedAlertHandler.alert_callback(data)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")

    def _handle_backfill(self, data):
        """Handle backfill data from the Discord plugin."""
        channel = data.get("channel", "unknown")
        messages = data.get("messages", [])
        sent_at = data.get("sent_at", datetime.now().isoformat())

        if not messages:
            logger.info(f"Backfill from #{channel}: no messages")
            return {"parsed": 0, "new": 0, "skipped": 0}

        logger.info(f"Backfill from #{channel}: {len(messages)} messages")

        # Archive raw messages
        archive_dir = Path(__file__).parent.parent / "data" / "raw_messages"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(archive_path, "w") as f:
            json.dump({"channel": channel, "messages": messages, "sent_at": sent_at}, f, indent=2)

        # Parse each message
        parsed_announcements = []
        skipped = 0

        for msg in messages:
            msg_id = msg.get("id", "")
            content = msg.get("content", "")
            timestamp_str = msg.get("timestamp", "")
            author = msg.get("author")
            inferred_author = _infer_author(channel, author)

            # Skip if we've seen this message
            if msg_id in UnifiedAlertHandler.seen_backfill:
                skipped += 1
                continue
            UnifiedAlertHandler.seen_backfill.add(msg_id)

            # Parse timestamp
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                timestamp = timestamp.replace(tzinfo=None)
            except (ValueError, AttributeError):
                timestamp = datetime.now()

            # Parse the message
            ann = parse_message_line(content, timestamp)
            if ann:
                ann.channel = channel
                ann.author = inferred_author or ann.author
                parsed_announcements.append(ann)

        # Limit seen_backfill size
        if len(UnifiedAlertHandler.seen_backfill) > 5000:
            UnifiedAlertHandler.seen_backfill = set(list(UnifiedAlertHandler.seen_backfill)[-2500:])

        logger.info(f"Parsed: {len(parsed_announcements)} | Skipped: {skipped}")

        if not parsed_announcements:
            return {"parsed": 0, "new": 0, "skipped": skipped}

        # Save to PostgreSQL
        try:
            client = PostgresClient()

            # Save raw messages
            for msg in messages:
                msg_id = msg.get("id", "")
                content = msg.get("content", "")
                timestamp_str = msg.get("timestamp", "")
                try:
                    msg_ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    msg_ts = datetime.now()
                client.save_raw_message(msg_id, channel, content, msg_ts)

            # Filter out today's announcements if needed
            new_announcements = []
            today = date.today()
            filtered_today = 0

            for ann in parsed_announcements:
                if not UnifiedAlertHandler.include_today and ann.timestamp.date() == today:
                    filtered_today += 1
                    continue
                new_announcements.append(ann)

            if filtered_today > 0:
                logger.info(f"Filtered out {filtered_today} today's announcements")

            if new_announcements:
                new_count = client.save_announcements(new_announcements)
                logger.info(f"Saved: {new_count} new announcements")

                # Optionally fetch OHLCV data
                if UnifiedAlertHandler.fetch_ohlcv:
                    logger.info("Fetching OHLCV data...")
                    for ann in new_announcements:
                        try:
                            bars = client.fetch_after_announcement(
                                ann.ticker,
                                ann.timestamp,
                                window_minutes=120,
                            )
                            status = f"{len(bars)} bars" if bars else "no data"
                            logger.info(f"  {ann.ticker}: {status}")
                        except Exception as e:
                            logger.warning(f"  {ann.ticker}: ERROR - {e}")

                return {"parsed": len(parsed_announcements), "new": new_count, "skipped": skipped}
            else:
                return {"parsed": len(parsed_announcements), "new": 0, "skipped": skipped}

        except Exception as e:
            logger.error(f"Database error: {e}")
            return {"parsed": len(parsed_announcements), "new": 0, "skipped": skipped, "error": str(e)}


class AlertService:
    """
    Background HTTP server for receiving Discord plugin messages.

    Started automatically with the dashboard, handles both alerts and backfill.
    """

    _instance: Optional["AlertService"] = None

    def __init__(self, port: int = 8765):
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    @classmethod
    def get_instance(cls, port: int = 8765) -> "AlertService":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(port=port)
        return cls._instance

    @classmethod
    def is_running(cls) -> bool:
        """Check if the service is running."""
        return cls._instance is not None and cls._instance._running

    def start(self):
        """Start the HTTP server in a background thread."""
        if self._running:
            logger.warning("Alert service already running")
            return

        try:
            self._server = HTTPServer(("0.0.0.0", self.port), UnifiedAlertHandler)
            self._running = True

            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

            logger.info(f"Alert service started on port {self.port}")
        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning(f"Port {self.port} already in use - alert service may already be running")
                self._running = True  # Assume it's our service
            else:
                raise

    def _run(self):
        """Server loop."""
        try:
            self._server.serve_forever()
        except Exception as e:
            logger.error(f"Alert service error: {e}")
        finally:
            self._running = False

    def stop(self):
        """Stop the HTTP server."""
        if self._server:
            self._server.shutdown()
        self._running = False
        logger.info("Alert service stopped")

    def set_alert_callback(self, callback: Optional[Callable]):
        """Set callback for real-time alerts (used by trading engine)."""
        UnifiedAlertHandler.alert_callback = callback

    def set_options(self, include_today: bool = False, fetch_ohlcv: bool = False):
        """Set processing options."""
        UnifiedAlertHandler.include_today = include_today
        UnifiedAlertHandler.fetch_ohlcv = fetch_ohlcv


# Convenience functions
def start_alert_service(port: int = 8765) -> AlertService:
    """Start the global alert service."""
    service = AlertService.get_instance(port)
    service.start()
    return service


def stop_alert_service():
    """Stop the global alert service."""
    if AlertService._instance:
        AlertService._instance.stop()


def set_alert_callback(callback: Optional[Callable]):
    """Set the callback for real-time alerts."""
    if AlertService._instance:
        AlertService._instance.set_alert_callback(callback)
