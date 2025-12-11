#!/usr/bin/env python3
"""
Quick trade execution CLI for Interactive Brokers.

Requires TWS or IB Gateway running locally.

Usage:
    # Buy $100 of AAPL with 10% take-profit, 7% stop-loss (premarket supported!)
    python trade_ib.py buy AAPL

    # Buy $200 of AAPL
    python trade_ib.py buy AAPL --dollars 200

    # Buy with custom take-profit and stop-loss
    python trade_ib.py buy AAPL --tp 15 --sl 5

    # Check account status
    python trade_ib.py status

    # List open positions
    python trade_ib.py positions

    # List open orders
    python trade_ib.py orders

    # Get quote for a ticker
    python trade_ib.py quote AAPL

    # Close a position
    python trade_ib.py close AAPL

    # Cancel all orders
    python trade_ib.py cancel-all
"""

import argparse
import sys
from dotenv import load_dotenv

load_dotenv()

from src.ib_trader import IBTrader


def main():
    parser = argparse.ArgumentParser(description="Quick trade execution via Interactive Brokers")
    parser.add_argument("--live", action="store_true", help="Use live trading (default: paper)")
    parser.add_argument("--port", type=int, help="TWS/Gateway port (default: 7497 paper, 7496 live)")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Buy command
    buy_parser = subparsers.add_parser("buy", help="Buy a stock with bracket order (premarket supported)")
    buy_parser.add_argument("ticker", help="Stock ticker symbol")
    buy_parser.add_argument("--dollars", "-d", type=float, default=100.0, help="Amount to invest (default: $100)")
    buy_parser.add_argument("--shares", "-s", type=int, help="Number of shares (overrides dollars)")
    buy_parser.add_argument("--tp", type=float, default=10.0, help="Take profit %% (default: 10)")
    buy_parser.add_argument("--sl", type=float, default=7.0, help="Stop loss %% (default: 7)")

    # Status command
    subparsers.add_parser("status", help="Show account status")

    # Positions command
    subparsers.add_parser("positions", help="List open positions")

    # Orders command
    subparsers.add_parser("orders", help="List open orders")

    # Quote command
    quote_parser = subparsers.add_parser("quote", help="Get current quote for a ticker")
    quote_parser.add_argument("ticker", help="Stock ticker symbol")

    # Close command
    close_parser = subparsers.add_parser("close", help="Close a position")
    close_parser.add_argument("ticker", help="Stock ticker symbol to close")

    # Close all command
    subparsers.add_parser("close-all", help="Close all positions")

    # Cancel all command
    subparsers.add_parser("cancel-all", help="Cancel all open orders")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        port = args.port
        if port is None:
            port = 7496 if args.live else 7497

        trader = IBTrader(port=port, paper=not args.live)
        mode = "LIVE" if args.live else "PAPER"
        print(f"[{mode} TRADING - IB Gateway/TWS on port {port}]\n")

        with trader:
            if args.command == "buy":
                result = trader.buy_with_bracket(
                    ticker=args.ticker.upper(),
                    dollars=args.dollars,
                    shares=args.shares,
                    take_profit_pct=args.tp,
                    stop_loss_pct=args.sl,
                )
                print(f"\nBracket order submitted!")
                print(f"  Parent Order ID: {result['parent_order_id']}")
                print(f"  Take Profit Order ID: {result['take_profit_order_id']}")
                print(f"  Stop Loss Order ID: {result['stop_loss_order_id']}")
                print(f"  Status: {result['status']}")

            elif args.command == "status":
                account = trader.get_account()
                print("Account Status:")
                print(f"  Status: {account.get('status', 'N/A')}")
                print(f"  Equity: ${account.get('equity', 0):,.2f}")
                print(f"  Cash: ${account.get('cash', 0):,.2f}")
                print(f"  Buying Power: ${account.get('buying_power', 0):,.2f}")

            elif args.command == "positions":
                positions = trader.get_positions()
                if not positions:
                    print("No open positions")
                else:
                    print(f"Open Positions ({len(positions)}):")
                    for p in positions:
                        print(f"  {p['ticker']}: {p['shares']} shares @ ${p['avg_entry']:.2f}")

            elif args.command == "orders":
                orders = trader.get_open_orders()
                if not orders:
                    print("No open orders")
                else:
                    print(f"Open Orders ({len(orders)}):")
                    for o in orders:
                        price_info = ""
                        if o['limit_price']:
                            price_info += f" limit=${o['limit_price']:.2f}"
                        if o['stop_price']:
                            price_info += f" stop=${o['stop_price']:.2f}"
                        print(f"  {o['ticker']}: {o['side']} {o['qty']} ({o['type']}) - {o['status']}{price_info}")

            elif args.command == "quote":
                quote = trader.get_quote(args.ticker.upper())
                print(f"{args.ticker.upper()} Quote:")
                print(f"  Bid: ${quote['bid']:.2f} x {quote['bid_size']}")
                print(f"  Ask: ${quote['ask']:.2f} x {quote['ask_size']}")
                print(f"  Mid: ${quote['mid']:.2f}")
                print(f"  Source: {quote.get('source', 'quote')}")

            elif args.command == "close":
                result = trader.close_position(args.ticker.upper())
                print(f"Closing {result['ticker']}: {result['status']}")

            elif args.command == "close-all":
                confirm = input("Close ALL positions? (yes/no): ")
                if confirm.lower() == "yes":
                    results = trader.close_all_positions()
                    print(f"Closing {len(results)} positions")
                else:
                    print("Cancelled")

            elif args.command == "cancel-all":
                count = trader.cancel_all_orders()
                print(f"Cancelled {count} orders")

    except ConnectionError as e:
        print(f"Connection Error: {e}", file=sys.stderr)
        print("\nMake sure TWS or IB Gateway is running and API is enabled.", file=sys.stderr)
        print("TWS: Edit > Global Configuration > API > Settings > Enable ActiveX and Socket Clients", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
