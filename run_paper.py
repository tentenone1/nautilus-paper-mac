"""Polymarket Whale Follower — Paper Trading (Sandbox) Node.

Multi-market: subscribes to top active whale markets across sports, politics, crypto.
Uses LIVE Polymarket data + SANDBOX execution (simulated fills).
NO real money. NO real API keys needed.

Usage:
    cd ~/workspace/nautilus-trading
    venv/bin/python run_paper.py
"""

import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal

# --- Fix 1: Follow HTTP redirects ---
# py_clob_client raises PolyApiException(301) on redirects.
# Monkey-patch the module-level httpx client to follow them.
import httpx as _httpx
import py_clob_client.http_helpers.helpers as _clob_helpers
_clob_helpers._http_client = _httpx.Client(http2=True, follow_redirects=True)
# -------------------------------------

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

from nautilus_trader.adapters.polymarket import POLYMARKET
from nautilus_trader.adapters.polymarket import PolymarketDataClientConfig
from nautilus_trader.adapters.polymarket.common.parsing import parse_polymarket_instrument
from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_instrument_id
from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.config import LiveExecEngineConfig, LoggingConfig, TradingNodeConfig, RoutingConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue

from strategies.whale_follower import WhaleFollower, WhaleFollowerConfig

# ── Load top whale markets from CURRENT whale positions (Polymarket data API) ──
def load_whale_markets_from_api(limit: int = 20) -> list[dict]:
    """Fetch markets whales are actively holding positions in — live data, not stale DB."""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "pipeline", "data", "whale_discovery.db"
    )
    # Get top whale addresses
    addresses = []
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT address FROM whales WHERE alpha_score >= 70 ORDER BY alpha_score DESC LIMIT 15"
        ).fetchall()
        conn.close()
        addresses = [r[0] for r in rows]

    if not addresses:
        print("No whale addresses in DB, using fallback")
        return []

    # Fetch CURRENT positions for each whale from Polymarket data API
    # (uses curl subprocess because Python SSL has TLS handshake issues)
    import subprocess, json as _json
    market_conds = {}  # condition_id -> {title, whale_count}
    for addr in addresses:
        try:
            result = subprocess.run(
                ["curl", "-s", "-m", "15",
                 f"https://data-api.polymarket.com/positions?user={addr}&limit=50"],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode == 0 and result.stdout.strip():
                positions = _json.loads(result.stdout)
                for pos in positions:
                    cond = pos.get("conditionId", "")
                    if not cond:
                        continue
                    if cond not in market_conds:
                        market_conds[cond] = {
                            "condition_id": cond,
                            "title": pos.get("title", ""),
                            "whale_count": 0,
                        }
                    market_conds[cond]["whale_count"] += 1
            else:
                print(f"  Skipping {addr[:12]}...: empty response (rate limited?)")
        except Exception as e:
            print(f"  API error for {addr[:12]}...: {e}")
            continue

    # Sort by number of whales holding this market, take top N
    markets_list = sorted(
        market_conds.values(), key=lambda x: x["whale_count"], reverse=True
    )[:limit]
    for m in markets_list:
        print(f"  [{m['whale_count']} whales] {m['title'][:55]}")
    return markets_list


print("Scanning current whale positions for active markets...")
whale_markets = load_whale_markets_from_api(limit=80)
print(f"Found {len(whale_markets)} active whale markets")
for i, m in enumerate(whale_markets):
    print(f"  [{m['whale_count']:2d}] {m['title'][:55]}")

if not whale_markets:
    print("ERROR: No whale markets found. Check the discovery DB.")
    sys.exit(1)

# ── Fetch instrument definitions from Polymarket (anonymous) ─────────────
print(f"\nFetching {len(whale_markets)} market definitions from CLOB API...")
clob = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON)

all_instruments = []  # (instrument, market_title, condition_id)
seen_conditions = set()

for m in whale_markets:
    cond = m["condition_id"]
    if cond in seen_conditions:
        continue
    seen_conditions.add(cond)

    try:
        market_info = clob.get_market(condition_id=cond)
        if not market_info.get("active", False):
            print(f"  SKIP (inactive): {m['title'][:50]}")
            continue

        tokens = market_info.get("tokens", [])
        for t in tokens:
            instrument = parse_polymarket_instrument(
                market_info=market_info,
                token_id=t["token_id"],
                outcome=t["outcome"],
            )
            all_instruments.append((instrument, m["title"], cond))
            if len(all_instruments) == 1:
                print(f"  OK: {m['title'][:50]} | {instrument.id}")

    except Exception as e:
        print(f"  FAIL: {m['title'][:50]} | {e}")

print(f"\nLoaded {len(all_instruments)} instruments from {len(seen_conditions)} markets")

if not all_instruments:
    print("ERROR: No active instruments loaded")
    sys.exit(1)

# Extract instrument IDs
instrument_ids = [inst.id for inst, _, _ in all_instruments]

# ── Sandbox execution (simulated fills) ────────────────────────────────
SANDBOX_VENUE = Venue("POLYMARKET")
instrument_config = PolymarketInstrumentProviderConfig()

sandbox_config = SandboxExecutionClientConfig(
    instrument_provider=instrument_config,
    venue=str(SANDBOX_VENUE),
    account_type="CASH",
    oms_type="NETTING",
    starting_balances=["10_000 USDC.e"],
    default_leverage=Decimal(1),
)

# ── Node ───────────────────────────────────────────────────────────────
config_node = TradingNodeConfig(
    trader_id=TraderId("WHALE-FOLLOWER-PAPER"),
    logging=LoggingConfig(log_level="INFO", use_pyo3=True),
    exec_engine=LiveExecEngineConfig(
        reconciliation=False,
        open_check_interval_secs=5.0,
    ),
    data_clients={
        POLYMARKET: PolymarketDataClientConfig(
            instrument_provider=instrument_config,
        ),
    },
    exec_clients={
        str(SANDBOX_VENUE): sandbox_config,
    },
    timeout_connection=30.0,
    timeout_reconciliation=5.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
)

# ── Strategy ───────────────────────────────────────────────────────────
config_strategy = WhaleFollowerConfig(
    instrument_ids=instrument_ids,
    bankroll=float(os.getenv("BANKROLL", "10000")),
    kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.25")),
    stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.15")),
    take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.30")),
    max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.10")),
    min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.25")),  # Lower threshold = more trades
    auto_trade=os.getenv("AUTO_TRADE", "true").lower() == "true",
    test_mode=False,  # Real mode: actual whale signals → real trades
    test_signal_interval_secs=float(os.getenv("TEST_SIGNAL_INTERVAL", "60")),
)

# ── Build ──────────────────────────────────────────────────────────────
node = TradingNode(config=config_node)
strategy = WhaleFollower(config=config_strategy)
node.trader.add_strategy(strategy)

# ── Preload ALL instruments into cache BEFORE building data client ───────
print(f"\nPreloading {len(all_instruments)} instruments into cache...")
for instrument, title, cond in all_instruments:
    node._builder._cache.add_instrument(instrument)

print(f"  Cached {len(all_instruments)} instruments across {len(seen_conditions)} markets")

# ── Anonymous data factory ─────────────────────────────────────────────
from nautilus_trader.adapters.polymarket.data import PolymarketDataClient
from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProvider
from nautilus_trader.live.factories import LiveDataClientFactory


class AnonymousPolymarketDataFactory(LiveDataClientFactory):
    """Creates Polymarket data client using anonymous (read-only) access."""

    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: PolymarketDataClientConfig,
        msgbus,
        cache,
        clock,
    ):
        http_client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
        )
        provider = PolymarketInstrumentProvider(
            client=http_client,
            clock=clock,
            config=config.instrument_provider,
        )
        return PolymarketDataClient(
            loop=loop,
            http_client=http_client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )


# ── Custom paper execution (monkey-patch SandboxExecutionClient) ───────
# We patch submit_order BEFORE the factory creates the client.
# Our replacement generates OrderFilled events directly (bypassing Cython)
# so our computed real-market fill price reaches the trade record.
from nautilus_trader.adapters.sandbox.execution import SandboxExecutionClient
from components.paper_execution import PaperExecClient

# Replace Cython submit_order with our own — generates fill events at real prices
SandboxExecutionClient.submit_order = PaperExecClient.submit_order
print("  Patched SandboxExecutionClient.submit_order with direct-fill event generation")

node.add_data_client_factory(POLYMARKET, AnonymousPolymarketDataFactory)
node.add_exec_client_factory(str(SANDBOX_VENUE), SandboxLiveExecClientFactory)
node.build()

# ── Run ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  POLYMARKET WHALE FOLLOWER — PAPER TRADING")
    print("=" * 60)
    print(f"  Markets:     {len(seen_conditions)} active whale markets")
    print(f"  Instruments: {len(all_instruments)} (YES+NO tokens)")
    print(f"  Venue:       {SANDBOX_VENUE} (SIMULATED)")
    print(f"  Bankroll:    ${config_strategy.bankroll:,.0f}")
    print(f"  Kelly:       {config_strategy.kelly_fraction}x")
    print(f"  Stop Loss:   {config_strategy.stop_loss_pct:.0%}")
    print(f"  Take Profit: {config_strategy.take_profit_pct:.0%}")
    print(f"  Max Pos:     {config_strategy.max_position_pct:.0%} of bankroll")
    print(f"  Min Conf:    {config_strategy.min_confidence:.0%}")
    print(f"  Auto Trade:  {config_strategy.auto_trade}")
    print(f"  TEST MODE:   {'YES (synthetic signals every ' + str(int(config_strategy.test_signal_interval_secs)) + 's)' if config_strategy.test_mode else 'NO'}")
    print()
    print("  Data:   LIVE Polymarket WebSocket (anonymous)")
    print("  Exec:   SANDBOX (simulated fills)")
    print("  Risk:   ZERO — no real money")
    print("=" * 60)
    print()
    try:
        node.run()
    finally:
        node.dispose()
