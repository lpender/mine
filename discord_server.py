#!/usr/bin/env python3
"""
Simple HTTP server that receives Discord messages and can trigger trades.

Usage:
    python discord_server.py

Then inject the browser script into Discord (see discord_monitor.js).
When a new message is detected, it will:
1. Parse the ticker from the message
2. Optionally auto-execute a trade via Alpaca

Endpoints:
    POST /message - Receive a Discord message
    GET /status - Check server status
    GET /history - View received messages
"""

import json
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import threading
import sys
from dotenv import load_dotenv

load_dotenv()

# Store received messages
message_history = []
AUTO_TRADE = False  # Set to True to auto-execute trades

# Try to import trading module
try:
    from src.alpaca_trader import AlpacaTrader
    from src.parser import parse_message_line
    TRADING_AVAILABLE = True
except ImportError:
    TRADING_AVAILABLE = False
    print("Warning: Trading module not available")


def extract_ticker(message: str) -> str | None:
    """Extract ticker from a Discord message."""
    # Pattern: TICKER < $X or TICKER  < $X (with extra spaces)
    match = re.match(r'^([A-Z]{2,5})\s+<\s*\$', message.strip())
    if match:
        return match.group(1)
    return None


def handle_new_message(message: str, timestamp: str) -> dict:
    """Process a new Discord message."""
    result = {
        "timestamp": timestamp,
        "message": message[:200],  # Truncate for display
        "ticker": None,
        "action": None,
        "trade_result": None,
    }

    ticker = extract_ticker(message)
    if ticker:
        result["ticker"] = ticker
        print(f"\n{'='*50}")
        print(f"NEW ALERT: {ticker}")
        print(f"Time: {timestamp}")
        print(f"Message: {message[:100]}...")

        if AUTO_TRADE and TRADING_AVAILABLE:
            try:
                trader = AlpacaTrader(paper=True)
                trade = trader.buy_with_bracket(
                    ticker=ticker,
                    dollars=100,
                    take_profit_pct=10,
                    stop_loss_pct=7,
                )
                result["action"] = "trade_executed"
                result["trade_result"] = trade
                print(f"TRADE EXECUTED: {trade}")
            except Exception as e:
                result["action"] = "trade_failed"
                result["trade_result"] = str(e)
                print(f"TRADE FAILED: {e}")
        else:
            result["action"] = "alert_only"
            print("Auto-trade disabled. Run manually:")
            print(f"  python trade.py buy {ticker}")

        print(f"{'='*50}\n")
    else:
        result["action"] = "ignored"

    message_history.append(result)
    # Keep only last 100 messages
    if len(message_history) > 100:
        message_history.pop(0)

    return result


class DiscordHandler(BaseHTTPRequestHandler):
    def _send_response(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self._send_response(200, {"status": "ok"})

    def do_GET(self):
        if self.path == "/status":
            self._send_response(200, {
                "status": "running",
                "auto_trade": AUTO_TRADE,
                "trading_available": TRADING_AVAILABLE,
                "messages_received": len(message_history),
            })
        elif self.path == "/history":
            self._send_response(200, {
                "messages": message_history[-20:],  # Last 20
            })
        else:
            self._send_response(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/message":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()

            try:
                data = json.loads(body)
                message = data.get("message", "")
                timestamp = data.get("timestamp", datetime.now().isoformat())

                result = handle_new_message(message, timestamp)
                self._send_response(200, result)
            except json.JSONDecodeError:
                self._send_response(400, {"error": "invalid json"})
        elif self.path == "/toggle-auto-trade":
            global AUTO_TRADE
            AUTO_TRADE = not AUTO_TRADE
            print(f"Auto-trade {'ENABLED' if AUTO_TRADE else 'DISABLED'}")
            self._send_response(200, {"auto_trade": AUTO_TRADE})
        else:
            self._send_response(404, {"error": "not found"})

    def log_message(self, format, *args):
        # Suppress default logging for cleaner output
        pass


def run_server(port: int = 8765):
    server = HTTPServer(("localhost", port), DiscordHandler)
    print(f"""
Discord Message Monitor
=======================
Server running at http://localhost:{port}

Endpoints:
  POST /message          - Receive Discord message
  POST /toggle-auto-trade - Toggle auto-trading
  GET  /status           - Server status
  GET  /history          - Recent messages

Auto-trade: {'ENABLED' if AUTO_TRADE else 'DISABLED'}
Trading:    {'AVAILABLE' if TRADING_AVAILABLE else 'NOT AVAILABLE'}

Paste the following into Discord's browser console (F12 > Console):
--------------------------------------------------------------
""")

    # Print the browser script
    with open("discord_monitor.js", "r") as f:
        print(f.read())

    print("--------------------------------------------------------------")
    print("\nWaiting for messages... (Ctrl+C to stop)\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server(port)
