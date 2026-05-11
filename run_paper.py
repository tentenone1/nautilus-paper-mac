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

# Fix: Line-buffered stdout so crash output isn't silently lost
sys.stdout.reconfigure(line_buffering=True)

# ── PID file lock — prevent duplicate processes (systemd User= double-fork workaround) ──
import atexit
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".run_paper.pid")

def _check_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            # Check if process with that PID is still running
            os.kill(old_pid, 0)  # signal 0 = existence check
            print(f"Another instance already running (PID {old_pid}). Exiting (code 1).")
            sys.exit(1)  # exit 1 = failure so systemd RestartPreventExitStatus=1 doesn't retry
        except (ValueError, OSError, ProcessLookupError):
            # PID file stale or process dead — we can start
            pass

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    def _cleanup_pid():
        try:
            if os.path.exists(PID_FILE):
                with open(PID_FILE) as f:
                    stored = f.read().strip()
                if stored == str(os.getpid()):
                    os.remove(PID_FILE)
        except OSError:
            pass

    atexit.register(_cleanup_pid)

_check_pid_lock()

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

from components.position_reconciler import PositionReconciler

# ── Load top whale markets from CURRENT whale positions (Polymarket data API) ──
def load_whale_markets_from_api(limit: int = 20) -> list[dict]:
    """Fetch markets whales are actively holding positions in — live data, not stale DB.
    
    Rate limit: Polymarket allows ~100 requests/min for unauthenticated.
    We use 0.7s between calls = ~85 requests/min (safe margin).
    Retry logic: 3 retries with exponential backoff on empty responses.
    """
    import time
    
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "pipeline", "data", "whale_discovery.db"
    )
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

    import subprocess, json as _json
    market_conds = {}
    failed_count = 0
    
    for i, addr in enumerate(addresses):
        # Rate limit: 0.7s between requests
        if i > 0:
            time.sleep(0.7)
        
        success = False
        for retry in range(3):
            try:
                result = subprocess.run(
                    ["curl", "-s", "-m", "15",
                     f"https://data-api.polymarket.com/positions?user={addr}&limit=50"],
                    capture_output=True, text=True, timeout=20
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    positions = _json.loads(result.stdout)
                    if positions:
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
                        print(f"  OK [{i+1}/{len(addresses)}]: {addr[:12]}... ({len(positions)} pos)")
                        success = True
                        break
                    else:
                        print(f"  EMPTY [{i+1}/{len(addresses)}]: {addr[:12]}...")
                        success = True
                        break
                else:
                    if retry < 2:
                        wait = 2 ** retry
                        print(f"  RETRY {retry+1}: {addr[:12]}... (wait {wait}s)")
                        time.sleep(wait)
                    else:
                        print(f"  SKIP [{i+1}/{len(addresses)}]: {addr[:12]}... (rate limited)")
                        failed_count += 1
                        
            except subprocess.TimeoutExpired:
                if retry < 2:
                    wait = 2 ** retry
                    print(f"  TIMEOUT retry {retry+1}: {addr[:12]}...")
                    time.sleep(wait)
                else:
                    print(f"  TIMEOUT [{i+1}/{len(addresses)}]: {addr[:12]}...")
                    failed_count += 1
            except Exception as e:
                print(f"  ERROR [{i+1}/{len(addresses)}]: {addr[:12]}...: {e}")
                failed_count += 1
                break
    
    if failed_count > 0:
        print(f"  Failed: {failed_count}/{len(addresses)} addresses")

    # Sort by whale count and return top N
    markets_list = sorted(market_conds.values(), key=lambda x: x["whale_count"], reverse=True)[:limit]
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
    starting_balances=["100 USDC.e"],
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
    bankroll=float(os.getenv("BANKROLL", "100")),
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
        client = PolymarketDataClient(
            loop=loop,
            http_client=http_client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )

        # ── Import WS message types for method overrides ──────────
        from nautilus_trader.adapters.polymarket.data import (
            PolymarketQuotes, PolymarketBookSnapshot,
            PolymarketTrade, PolymarketTickSizeChange,
        )

        # ── Suppress "Cannot find instrument" WARN spam ────────────
        # The WebSocket sends data for ALL tokens in a market (Yes/No
        # outcomes + derived tokens). Some tokens aren't in cache because
        # markets resolved between API fetch and WebSocket data arrival.
        # This floods logs at 600MB+/day. The Logger class is Cython/read-only
        # so we override the Python methods that produce the warnings instead.
        # See: ~/wiki/nautilus-stale-instrument-warn-spam.md
        #
        # The first 100 warnings are still logged, then suppressed silently.

        import itertools
        _warn_counter = itertools.count()

        def _log_stale_warn(self, instrument_id):
            """Log first 100 stale instrument warnings, then suppress."""
            count = next(_warn_counter)
            if count < 100:
                self._log.warning(f"Cannot find instrument for {instrument_id} (stale, suppressed)")
            elif count == 100:
                self._log.warning(
                    "[SUPPRESSED] Further 'Cannot find instrument' "
                    "warnings suppressed — stale instruments from resolved markets."
                )

        # Override _handle_quotes (highest volume: ~4M/day)
        def _suppressed_handle_quotes(self, ws_message):
            for price_change in ws_message.price_changes:
                instrument_id = get_polymarket_instrument_id(
                    ws_message.market, price_change.asset_id
                )
                instrument = self._cache.instrument(instrument_id)
                if instrument is None:
                    _log_stale_warn(self, instrument_id)
                    continue
                self._handle_quote(
                    instrument=instrument,
                    ws_message=ws_message,
                    price_change=price_change,
                )

        # Override _handle_ws_message (book snapshots, trades, tick changes)
        def _suppressed_handle_ws_message(self, msg):
            if isinstance(msg, PolymarketQuotes):
                self._handle_quotes(ws_message=msg)
            elif isinstance(msg, PolymarketBookSnapshot):
                instrument_id = get_polymarket_instrument_id(msg.market, msg.asset_id)
                instrument = self._cache.instrument(instrument_id)
                if instrument is None:
                    _log_stale_warn(self, instrument_id)
                    return
                self._handle_book_snapshot(instrument=instrument, ws_message=msg)
            elif isinstance(msg, PolymarketTrade):
                instrument_id = get_polymarket_instrument_id(msg.market, msg.asset_id)
                instrument = self._cache.instrument(instrument_id)
                if instrument is None:
                    _log_stale_warn(self, instrument_id)
                    return
                self._handle_trade(instrument=instrument, ws_message=msg)
            elif isinstance(msg, PolymarketTickSizeChange):
                instrument_id = get_polymarket_instrument_id(msg.market, msg.asset_id)
                instrument = self._cache.instrument(instrument_id)
                if instrument is None:
                    _log_stale_warn(self, instrument_id)
                    return
                self._handle_instrument_update(instrument=instrument, ws_message=msg)
            else:
                self._log.error(f"Unknown websocket message topic: {msg}")

        # Bind and replace methods on this instance
        from types import MethodType

        client._handle_quotes = MethodType(_suppressed_handle_quotes, client)
        client._handle_ws_message = MethodType(_suppressed_handle_ws_message, client)
        # Note: _request_instrument is not overridden because it fires
        # rarely (requested by nautilus engine) vs every WebSocket message.
        # ──────────────────────────────────────────────────────────

        return client


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

    # ── P1-3: Position Reconciliation (startup + periodic) ─────────────
    reconciler = PositionReconciler()
    print("  Running startup position reconciliation...")
    report = reconciler.reconcile_all()
    print(f"  Startup recon: {report.matched}/{report.total_paper_positions} positions matched"
          f" | {len(report.mismatches)} issues | {len(report.unmatched_paper)} unmatched paper")
    if not report.ok:
        print(f"  ⚠️  RECONCILIATION ISSUES: {len(report.mismatches)} mismatches found")
        for m in report.mismatches[:5]:
            for issue in m.issues:
                print(f"       {issue}")
    # Start periodic reconciliation (every 5 minutes)
    reconciler.start_periodic(interval_secs=300.0)
    print("  Periodic reconciliation started: every 300s")
    print()

    try:
        node.run()
    finally:
        reconciler.stop_periodic()
        node.dispose()
