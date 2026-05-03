"""Polymarket Whale Follower — Backtesting with historical data.

Uses nautilus_trader's backtesting engine with Polymarket historical data.

Usage:
    python run_backtest.py [--days 30] [--bankroll 10000] [--kelly 0.25]
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nautilus_trader.adapters.polymarket import POLYMARKET
from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_instrument_id
from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.identifiers import TraderId, Venue

from strategies.whale_follower import WhaleFollower, WhaleFollowerConfig

# ── Market ────────────────────────────────────────────────────────────────────
CONDITION_ID = "0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902"
TOKEN_ID = "8441400852834915183759801017793514978104486628517653995211751018945988243154"
INSTRUMENT_ID = get_polymarket_instrument_id(CONDITION_ID, TOKEN_ID)


def run_backtest(days: int = 30, bankroll: float = 10000, kelly: float = 0.25) -> None:
    """Run backtest with Polymarket historical data."""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)

    engine_config = BacktestEngineConfig(
        trader_id=TraderId("WHALE-BACKTEST-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
    )

    engine = BacktestEngine(config=engine_config)

    # Add Polymarket venue
    engine.add_venue(
        venue=Venue("POLYMARKET"),
        oms_type="HEDGING",
        account_type="CASH",
        base_currency="USD",
        starting_balances=[f"{bankroll} USD"],
    )

    # Add strategy
    strategy_config = WhaleFollowerConfig(
        instrument_id=INSTRUMENT_ID,
        bankroll=bankroll,
        kelly_fraction=kelly,
        subscribe_quotes=True,
        subscribe_trades=True,
    )
    strategy = WhaleFollower(config=strategy_config)
    engine.add_strategy(strategy)

    # Note: Historical data loading depends on nautilus data catalog setup.
    # For now, this shows the structure. Real backtesting requires:
    # 1. Downloading historical Polymarket data
    # 2. Loading it into nautilus ParquetDataCatalog
    # 3. Running engine.run() will execute the backtest

    print(f"Backtest configured:")
    print(f"  Market: {INSTRUMENT_ID}")
    print(f"  Period: {start.date()} → {end.date()} ({days} days)")
    print(f"  Bankroll: ${bankroll}")
    print(f"  Kelly: {kelly}x")
    print()
    print("Note: Full backtesting requires historical data in nautilus data catalog.")
    print("To set up:")
    print("  1. Download Polymarket historical trade data")
    print("  2. Load into ParquetDataCatalog")
    print("  3. engine.run() will execute the backtest")
    print()
    print("For paper trading (no historical data needed), use: python run_live.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--bankroll", type=float, default=10000)
    parser.add_argument("--kelly", type=float, default=0.25)
    args = parser.parse_args()

    run_backtest(args.days, args.bankroll, args.kelly)
