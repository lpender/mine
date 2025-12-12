#!/usr/bin/env python3
"""
Local webhook server that receives alerts from the Discord plugin.

Usage:
    python alert_server.py                    # Just log alerts
    python alert_server.py --trade            # Auto-trade on alerts (paper)
    python alert_server.py --trade --live     # Auto-trade live (DANGEROUS!)
    python alert_server.py --fetch-ohlcv      # Auto-fetch OHLCV on backfill

The Vencord plugin sends POST requests to:
    http://localhost:8765/alert     - Real-time alerts
    http://localhost:8765/backfill  - Historical message backfill
"""

import argparse
import json
import re
import sys
import subprocess
from datetime import date, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

try:
    from src.parser import parse_message_line
    from src.massive_client import MassiveClient
except ImportError as e:
    print(f"Warning: Could not import modules: {e}")
    parse_message_line = None
    MassiveClient = None


class AlertHandler(BaseHTTPRequestHandler):
    auto_trade = False
    live_mode = False
    fetch_ohlcv = False
    include_today = False
    seen_alerts = set()
    seen_backfill = set()

    def log_message(self, format, *args):
        # Suppress default HTTP logging
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print(f"Invalid JSON: {body[:100]}")
            self.send_error_response(400, "Invalid JSON")
            return

        if self.path == "/alert":
            self.handle_alert(data)
            self.send_ok_response()
        elif self.path == "/backfill":
            result = self.handle_backfill(data)
            self.send_ok_response(result)
        else:
            self.send_error_response(404, "Not found")

    def do_OPTIONS(self):
        # Handle CORS preflight
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_ok_response(self, data=None):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        response = {"status": "ok"}
        if data:
            response.update(data)
        self.wfile.write(json.dumps(response).encode())

    def send_error_response(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "error", "message": message}).encode())

    def handle_alert(self, data):
        ticker = data.get("ticker", "UNKNOWN")
        price_info = data.get("price_info", "")
        channel = data.get("channel", "")
        content = data.get("content", "")
        timestamp = data.get("timestamp", datetime.now().isoformat())

        # Dedupe by ticker + minute
        alert_key = f"{ticker}:{timestamp[:16]}"
        if alert_key in AlertHandler.seen_alerts:
            return
        AlertHandler.seen_alerts.add(alert_key)

        # Limit seen alerts size
        if len(AlertHandler.seen_alerts) > 500:
            AlertHandler.seen_alerts = set(list(AlertHandler.seen_alerts)[-250:])

        # Parse price from the alert
        price_match = re.search(r'\$([0-9.]+)', price_info)
        price = float(price_match.group(1)) if price_match else None

        # Extract just the ticker symbol
        ticker_match = re.match(r'([A-Z]{2,5})', ticker)
        ticker_symbol = ticker_match.group(1) if ticker_match else ticker

        # Print alert
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n{'='*60}")
        print(f"  NEW ALERT @ {now}")
        print(f"{'='*60}")
        print(f"  Ticker:  {ticker_symbol}")
        print(f"  Price:   ${price:.2f}" if price else f"  Info:    {price_info}")
        print(f"  Channel: #{channel}")
        print(f"{'='*60}")

        # Parse full announcement if parser is available
        if parse_message_line and content:
            ann = parse_message_line(content, datetime.now())
            if ann:
                print(f"  Float:   {ann.float_shares/1e6:.1f}M" if ann.float_shares else "")
                print(f"  IO%:     {ann.io_percent:.1f}%" if ann.io_percent else "")
                print(f"  MC:      ${ann.market_cap/1e6:.1f}M" if ann.market_cap else "")
                if ann.high_ctb:
                    print(f"  Flags:   High CTB")
                if ann.reg_sho:
                    print(f"  Flags:   Reg SHO")

        # Auto-trade if enabled
        if AlertHandler.auto_trade and ticker_symbol and ticker_symbol != "TEST":
            self.execute_trade(ticker_symbol, price)

    def handle_backfill(self, data):
        """Handle backfill data from the Discord plugin."""
        channel = data.get("channel", "unknown")
        messages = data.get("messages", [])
        sent_at = data.get("sent_at", datetime.now().isoformat())

        if not messages:
            print(f"\n[Backfill] No messages received from #{channel}")
            return {"parsed": 0, "new": 0, "skipped": 0}

        print(f"\n{'='*60}")
        print(f"  BACKFILL from #{channel}")
        print(f"  Received: {len(messages)} messages @ {sent_at[:19]}")
        print(f"{'='*60}")

        # Debug: save raw messages to file for analysis
        debug_path = Path(__file__).parent / "debug_backfill.json"
        with open(debug_path, "w") as f:
            json.dump({"channel": channel, "messages": messages, "sent_at": sent_at}, f, indent=2)
        print(f"  Debug: saved raw messages to {debug_path}")

        if not parse_message_line:
            print("  ERROR: Parser not available")
            return {"error": "Parser not available"}

        # Parse each message
        parsed_announcements = []
        skipped = 0

        for msg in messages:
            msg_id = msg.get("id", "")
            content = msg.get("content", "")
            timestamp_str = msg.get("timestamp", "")

            # Skip if we've seen this message
            if msg_id in AlertHandler.seen_backfill:
                skipped += 1
                continue
            AlertHandler.seen_backfill.add(msg_id)

            # Parse timestamp
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                # Convert to naive datetime (ET assumed)
                timestamp = timestamp.replace(tzinfo=None)
            except (ValueError, AttributeError):
                timestamp = datetime.now()

            # Parse the message
            ann = parse_message_line(content, timestamp)
            if ann:
                ann.channel = channel
                parsed_announcements.append(ann)
                print(f"  + {ann.ticker:5} @ {ann.timestamp.strftime('%Y-%m-%d %H:%M')} | ${ann.price_threshold:.2f}")

        # Limit seen_backfill size
        if len(AlertHandler.seen_backfill) > 5000:
            AlertHandler.seen_backfill = set(list(AlertHandler.seen_backfill)[-2500:])

        print(f"  Parsed: {len(parsed_announcements)} | Skipped (dupe): {skipped}")

        if not parsed_announcements:
            return {"parsed": 0, "new": 0, "skipped": skipped}

        # Save to cache
        if MassiveClient:
            client = MassiveClient()
            existing = client.load_announcements()
            existing_keys = {(a.ticker, a.timestamp) for a in existing}

            new_announcements = []
            today = date.today()
            filtered_today = 0
            for ann in parsed_announcements:
                # Exclude today's data by default (market data incomplete)
                if not AlertHandler.include_today and ann.timestamp.date() == today:
                    filtered_today += 1
                    continue
                key = (ann.ticker, ann.timestamp)
                if key not in existing_keys:
                    new_announcements.append(ann)
                    existing_keys.add(key)

            if filtered_today > 0:
                print(f"  Filtered out: {filtered_today} today's announcements")

            if new_announcements:
                all_announcements = existing + new_announcements
                client.save_announcements(all_announcements)
                print(f"  Saved: {len(new_announcements)} new announcements")

                # Optionally fetch OHLCV data
                if AlertHandler.fetch_ohlcv:
                    print(f"  Fetching OHLCV data...")
                    for ann in new_announcements:
                        try:
                            bars = client.fetch_after_announcement(
                                ann.ticker,
                                ann.timestamp,
                                window_minutes=120,
                            )
                            status = f"{len(bars)} bars" if bars else "no data"
                            print(f"    {ann.ticker}: {status}")
                        except Exception as e:
                            print(f"    {ann.ticker}: ERROR - {e}")
            else:
                print(f"  No new announcements (all duplicates)")

            return {
                "parsed": len(parsed_announcements),
                "new": len(new_announcements),
                "skipped": skipped
            }
        else:
            print("  WARNING: MassiveClient not available, announcements not saved")
            return {
                "parsed": len(parsed_announcements),
                "new": 0,
                "skipped": skipped,
                "warning": "MassiveClient not available"
            }

    def execute_trade(self, ticker, price):
        """Execute a trade via trade.py"""
        mode = "LIVE" if AlertHandler.live_mode else "PAPER"
        print(f"\n  >>> AUTO-TRADING ({mode}): {ticker}")

        trade_script = Path(__file__).parent.parent / "trade.py"

        cmd = ["python", str(trade_script), "buy", ticker]
        if AlertHandler.live_mode:
            cmd.insert(2, "--live")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                print(f"  >>> Trade executed successfully")
                print(result.stdout)
            else:
                print(f"  >>> Trade failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            print(f"  >>> Trade timed out")
        except Exception as e:
            print(f"  >>> Trade error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Discord alert webhook server")
    parser.add_argument("--trade", action="store_true", help="Auto-trade on alerts")
    parser.add_argument("--live", action="store_true", help="Use live trading (DANGEROUS!)")
    parser.add_argument("--fetch-ohlcv", action="store_true", help="Auto-fetch OHLCV on backfill")
    parser.add_argument("--include-today", action="store_true", help="Include today's announcements (normally excluded)")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    args = parser.parse_args()

    AlertHandler.auto_trade = args.trade
    AlertHandler.live_mode = args.live
    AlertHandler.fetch_ohlcv = args.fetch_ohlcv
    AlertHandler.include_today = args.include_today

    if args.live and args.trade:
        print("\n" + "!"*60)
        print("  WARNING: LIVE TRADING ENABLED!")
        print("  Real money will be used for trades!")
        print("!"*60)
        confirm = input("\nType 'YES' to confirm: ")
        if confirm != "YES":
            print("Aborted.")
            sys.exit(1)

    server = HTTPServer(("localhost", args.port), AlertHandler)

    print(f"\nStock Alert Server running on http://localhost:{args.port}")
    print(f"Endpoints:")
    print(f"  POST /alert    - Real-time alerts")
    print(f"  POST /backfill - Historical message backfill")
    print(f"\nOptions:")
    print(f"  Auto-trade:  {'ENABLED' if args.trade else 'disabled'}")
    if args.trade:
        print(f"  Mode:        {'LIVE' if args.live else 'PAPER'}")
    print(f"  Fetch OHLCV: {'ENABLED' if args.fetch_ohlcv else 'disabled'}")
    print(f"  Include today: {'yes' if args.include_today else 'no (excluded)'}")
    print("\nWaiting for messages from Discord plugin...")
    print("-" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
