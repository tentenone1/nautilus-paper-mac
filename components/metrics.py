"""
Thread-safe metrics collector for the whale follower system.

Exposes key counters and state via /health endpoint:
  - trades_entered_total, trades_exited_total
  - killswitch_triggers_total (by reason)
  - circuit_breaker_opens_total (by breaker name)
  - current_open_positions, current_exposure_usd
  - daily_pnl, daily_loss_breached

Usage:
    from components.metrics import get_metrics
    metrics = get_metrics()
    metrics.increment_trade_entered()
    metrics.set_open_positions(5)
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class MetricsCollector:
    """
    Thread-safe singleton metrics collector.
    All public methods acquire the lock before mutating state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trades_entered = 0
        self._trades_exited = 0
        self._killswitch_triggers: dict[str, int] = defaultdict(int)
        self._cb_opens: dict[str, int] = defaultdict(int)
        self._open_positions = 0
        self._exposure_usd = 0.0
        self._daily_pnl = 0.0
        self._daily_loss_breached = False

    # ── Trade counters ────────────────────────────────────────────────

    def increment_trade_entered(self) -> None:
        with self._lock:
            self._trades_entered += 1

    def increment_trade_exited(self) -> None:
        with self._lock:
            self._trades_exited += 1

    def get_trades_entered(self) -> int:
        with self._lock:
            return self._trades_entered

    def get_trades_exited(self) -> int:
        with self._lock:
            return self._trades_exited

    # ── Kill switch ──────────────────────────────────────────────────

    def increment_killswitch_triggered(self, reason: str) -> None:
        with self._lock:
            self._killswitch_triggers[reason] += 1

    def get_killswitch_triggers(self) -> dict[str, int]:
        with self._lock:
            return dict(self._killswitch_triggers)

    # ── Circuit breaker ──────────────────────────────────────────────

    def increment_circuit_breaker_open(self, breaker_name: str) -> None:
        with self._lock:
            self._cb_opens[breaker_name] += 1

    def get_circuit_breaker_opens(self) -> dict[str, int]:
        with self._lock:
            return dict(self._cb_opens)

    # ── Position state ────────────────────────────────────────────────

    def set_open_positions(self, count: int) -> None:
        with self._lock:
            self._open_positions = count

    def get_open_positions(self) -> int:
        with self._lock:
            return self._open_positions

    def set_exposure_usd(self, amount: float) -> None:
        with self._lock:
            self._exposure_usd = amount

    def get_exposure_usd(self) -> float:
        with self._lock:
            return self._exposure_usd

    # ── Daily P&L ────────────────────────────────────────────────────

    def set_daily_pnl(self, amount: float) -> None:
        """Set daily P&L (use add_daily_pnl for incremental updates)."""
        with self._lock:
            self._daily_pnl = amount

    def add_daily_pnl(self, delta: float) -> None:
        """Accumulate daily P&L across multiple exits."""
        with self._lock:
            self._daily_pnl += delta

    def get_daily_pnl(self) -> float:
        with self._lock:
            return self._daily_pnl

    def set_daily_loss_breached(self, breached: bool) -> None:
        with self._lock:
            self._daily_loss_breached = breached

    def get_daily_loss_breached(self) -> bool:
        with self._lock:
            return self._daily_loss_breached

    # ── Full snapshot ─────────────────────────────────────────────────

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return {
                "trades_entered_total": self._trades_entered,
                "trades_exited_total": self._trades_exited,
                "killswitch_triggers_total": dict(self._killswitch_triggers),
                "circuit_breaker_opens": dict(self._cb_opens),
                "current_open_positions": self._open_positions,
                "current_exposure_usd": round(self._exposure_usd, 4),
                "daily_pnl": round(self._daily_pnl, 4),
                "daily_loss_breached": self._daily_loss_breached,
            }


# ── Singleton ──────────────────────────────────────────────────────────────────

_metrics: MetricsCollector | None = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """Return the thread-safe singleton MetricsCollector instance."""
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = MetricsCollector()
    return _metrics
