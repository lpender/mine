#!/usr/bin/env python3
"""
Quick trade execution CLI.

Usage:
    # Buy $100 of AAPL with 10% take-profit, 7% stop-loss
    python trade.py buy AAPL

    # Buy $200 of AAPL
    python trade.py buy AAPL --dollars 200

    # Buy with custom take-profit and stop-loss
    python trade.py buy AAPL --tp 15 --sl 5

    # Check account status
    python trade.py status

    # List open positions
    python trade.py positions

    # List open orders
    python trade.py orders

    # Close a position
    python trade.py close AAPL

    # Cancel all orders
    python trade.py cancel-all
"""

import argparse
import sys
from dotenv import load_dotenv

load_dotenv()

from src.alpaca_trader import AlpacaTrader


def main():
    parser = argparse.ArgumentParser(description="Quick trade execution via Alpaca")
    parser.add_argument("--live", action="store_true", help="Use live trading (default: paper)")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Buy command
    buy_parser = subparsers.add_parser("buy", help="Buy a stock with bracket order")
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
        trader = AlpacaTrader(paper=not args.live)
        mode = "LIVE" if args.live else "PAPER"
        print(f"[{mode} TRADING]\n")

        if args.command == "buy":
            result = trader.buy_with_bracket(
                ticker=args.ticker.upper(),
                dollars=args.dollars,
                shares=args.shares,
                take_profit_pct=args.tp,
                stop_loss_pct=args.sl,
            )
            print(f"\nOrder submitted!")
            print(f"  Order ID: {result['order_id']}")
            print(f"  Status: {result['status']}")

        elif args.command == "status":
            account = trader.get_account()
            print("Account Status:")
            print(f"  Status: {account['status']}")
            print(f"  Equity: ${account['equity']:,.2f}")
            print(f"  Cash: ${account['cash']:,.2f}")
            print(f"  Buying Power: ${account['buying_power']:,.2f}")

        elif args.command == "positions":
            positions = trader.get_positions()
            if not positions:
                print("No open positions")
            else:
                print(f"Open Positions ({len(positions)}):")
                for p in positions:
                    pl_sign = "+" if p['unrealized_pl'] >= 0 else ""
                    print(f"  {p['ticker']}: {p['shares']} shares @ ${p['avg_entry']:.2f}")
                    print(f"    Current: ${p['current_price']:.2f} | P/L: {pl_sign}${p['unrealized_pl']:.2f} ({pl_sign}{p['unrealized_pl_pct']:.1f}%)")

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

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
