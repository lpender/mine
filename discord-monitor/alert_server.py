#!/usr/bin/env python3
"""
Local webhook server that receives alerts from the Discord plugin.

Usage:
    python alert_server.py                    # Just log alerts
    python alert_server.py --trade            # Auto-trade on alerts (paper)
    python alert_server.py --trade --live     # Auto-trade live (DANGEROUS!)

The BetterDiscord plugin sends POST requests to http://localhost:8765/alert
"""

import argparse
import json
import re
import sys
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.parser import parse_message_line
except ImportError:
    parse_message_line = None


class AlertHandler(BaseHTTPRequestHandler):
    auto_trade = False
    live_mode = False
    seen_alerts = set()

    def log_message(self, format, *args):
        # Suppress default HTTP logging
        pass

    def do_POST(self):
        if self.path == "/alert":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            try:
                data = json.loads(body)
                self.handle_alert(data)
            except json.JSONDecodeError:
                print(f"Invalid JSON: {body[:100]}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        # Handle CORS preflight
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    args = parser.parse_args()

    AlertHandler.auto_trade = args.trade
    AlertHandler.live_mode = args.live

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
    print(f"Auto-trade: {'ENABLED' if args.trade else 'disabled'}")
    if args.trade:
        print(f"Mode: {'LIVE' if args.live else 'PAPER'}")
    print("\nWaiting for alerts from Discord plugin...")
    print("-" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
