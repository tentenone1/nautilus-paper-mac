"""Whale Follower Strategy — Constants and Configuration.

All module-level constants and the WhaleFollowerConfig dataclass,
extracted from whale_follower.py for centralized management.
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.identifiers import InstrumentId


# ── Trade Buffer Thresholds ──────────────────────────────────────────────────

TRADE_BUFFER_SIZE_THRESHOLD = 200  # Minimum USD to buffer a trade
TRADE_BUFFER_FLUSH_COUNT = 5  # Number of trades to trigger buffer flush


# ── Exit Timer Configuration ─────────────────────────────────────────────────

EXIT_TIMER_INTERVAL_SECS = 30.0  # How often to check all positions for exits
RECYCLE_INTERVAL_SECS = 1800.0  # Unsubscribe/resubscribe interval to flush stale order books


# ── Position Management ──────────────────────────────────────────────────────

RE_ENTRY_COOLDOWN_SECS = 300  # Don't re-enter same instrument within 5 minutes of exit
LOW_CASH_ALERT_PCT = 0.20  # Warn when free balance drops below 20% of bankroll


# ── Whale Blacklists (auto-reject proven losers, data from trades.db) ────────

WHALE_BLACKLIST = frozenset({
    "asdfjh",           # -7,375 on sports
    "benwyatt",         # -1,866 on sports
    "Sassy-Bucket",     # -1,277 on sports
    "JPMorgan101",      # -1,510 on sports
    "joblessfinalboss", # -1,446 on sports
    "TTEST2",           # -17,419 actual P&L
    "Wannac",           # -1,119 actual P&L
})

SPORTS_WHALE_BLACKLIST = frozenset({
    "SMCAOMCRL",         # -6,209 on sports (profitable on general)
    "LaBradfordSmith22", # -2,111 on sports (profitable on general)
    "TheVeryGoodCow",    # -613 on sports
    "beetlepimp",        # -399 on sports
})


# ── Certainty Exit Thresholds (binary prediction markets) ────────────────────

CERTAINTY_WIN_THRESHOLD = 0.95  # Price above this = very likely to win
CERTAINTY_LOSS_THRESHOLD = 0.05  # Price below this = very likely to lose


# ── P&L Sanity Cap ───────────────────────────────────────────────────────────

MAX_SANE_RETURN = 2.0  # Cap P&L returns at +/-200% to prevent sandbox artifacts


# ── Memory Management ────────────────────────────────────────────────────────

MEMORY_PRESSURE_MB = 2500  # RSS threshold in MB to trigger graceful shutdown


# ── Subscription Cleanup ─────────────────────────────────────────────────────

STALE_SUBSCRIPTION_TTL_SECS = 3600  # Clean up dynamic subscriptions older than 1 hour


# ── Resolution Timing ────────────────────────────────────────────────────────

RESOLUTION_EXIT_HOURS = 6  # Exit if market resolves within this many hours


# ── Sports Market Timing ─────────────────────────────────────────────────────

SPORTS_EXIT_HOURS_BEFORE_EVENT = 1  # Exit sports positions this many hours before game


# ── Liquidity Tier Thresholds (volume + liquidity in USD) ────────────────────

LIQUIDITY_TIER4_THRESHOLD = 100_000  # Illiquid: reduce to 25% of Kelly
LIQUIDITY_TIER3_THRESHOLD = 1_000_000  # Moderate: reduce to 50% of Kelly


# ── Liquidity Sizing Multipliers ─────────────────────────────────────────────

LIQUIDITY_TIER4_MULTIPLIER = 0.25
LIQUIDITY_TIER3_MULTIPLIER = 0.50
LIQUIDITY_TIER2_MULTIPLIER = 0.75


# ── Sports Market Keywords ───────────────────────────────────────────────────

SPORTS_KEYWORDS: list[str] = [
    "nfl", "nba", "mlb", "nhl", "ncaa", "college football", "college basketball",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis", "golf",
    "boxing", "mma", "ufc", "wwe", "f1", "formula 1", "nascar",
    "super bowl", "world cup", "champions league", "premier league",
    "playoffs", "stanley cup", "world series", "final four", "march madness",
    "vs.", " vs ", "eagles", "49ers", "chiefs", "lakers", "celtics",
    "warriors", "yankees", "dodgers", "red sox", "patriots",
    "trail blazers", "spurs", "penguins", "stars", "wild",
    "bucks", "thunder", "nuggets", "timberwolves", "knicks",
]


class WhaleFollowerConfig(StrategyConfig, frozen=True):
    """Configuration for WhaleFollower."""

    instrument_ids: list[InstrumentId]
    bankroll: float = 10000.0
    kelly_fraction: float = 0.25
    stop_loss_pct: float = 0.15
    take_profit_pct: float = 0.30
    max_position_pct: float = 0.10
    max_open_positions: int = 50
    # Max total gross exposure as % of bankroll (hard cap on aggregate position size)
    max_total_exposure_pct: float = 5.0  # Total open positions capped at 500% of bankroll
    # Daily loss limit: stop trading if daily loss exceeds this
    daily_loss_limit: float = 500.0
    min_confidence: float = 0.55
    scan_interval_secs: float = 30.0
    auto_trade: bool = True
    # Dynamic Kelly: use whale's actual win rate instead of fixed estimate
    use_dynamic_kelly: bool = True
    # Seen position TTL: re-scan positions older than this (seconds)
    seen_position_ttl: float = 14400.0  # 4 hours (was 24h - 542 orphan_cleanup_sandbox trades avg'd 35h)
    # Max hold time for open positions (hours) - longer than this triggers auto-exit
    max_hold_hours: float = 4.0  # close positions held > 4h (was 24h - 6.2% WR on >1h positions)

    # Asymmetrical SL/TP: TP = TP_MULTIPLIER x SL threshold (winners run longer)
    tp_multiplier: float = 2.5  # TP width = 2.5x SL width

    # Trailing stop - activates after TP threshold is reached
    trailing_stop: bool = True
    trailing_stop_retrace_pct: float = 0.40  # Exit if price retraces 40% from peak gain
    # Max trades per scan cycle (prevents balance exhaustion on restart)
    max_trades_per_scan: int = 5
    # Trade buffer flush interval (seconds)
    trade_buffer_flush_secs: float = 30.0
    # Test mode: inject synthetic signals to exercise pipeline
    test_mode: bool = False
    test_signal_interval_secs: float = 300.0  # 5 min between synthetic signals

    # Backward compat: allow single instrument_id
    @property
    def instrument_id(self) -> InstrumentId | None:
        return self.instrument_ids[0] if self.instrument_ids else None
