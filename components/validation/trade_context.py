"""Phase 1 validation layer - Trade context correlation tracker.

In-memory tracking of signal → submission → fill lifecycle for
latency and slippage computation.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class TradeContextEntry:
    """Single trade context entry for correlation tracking.
    
    Tracks timestamps and prices from whale detection through fill,
    enabling latency and slippage computation.
    """
    signal_id: str = ""
    whale_trade_ts: int = 0  # monotonic_ns when whale trade detected
    signal_detected_ts: int = 0  # monotonic_ns when signal processed
    signal_generated_ts: int = 0  # monotonic_ns when signal created
    submitted_ts: int = 0  # monotonic_ns when order submitted
    filled_ts: int = 0  # monotonic_ns when fill received
    snapshot_id: str = ""
    intended_entry_price: float = 0.0
    actual_fill_price: float = 0.0
    intended_size_usd: float = 0.0
    filled_size_usd: float = 0.0
    side: str = ""  # BUY or SELL


class TradeContext:
    """Thread-safe in-memory correlation tracker for trade lifecycle.
    
    Maps client_order_id to TradeContextEntry for computing:
    - Detection delay (whale trade → signal generated)
    - Execution delay (signal generated → order submitted)
    - Fill delay (order submitted → fill received)
    - Total latency (whale trade → fill received)
    - Slippage (intended entry vs actual fill)
    - Fill completion (intended size vs filled size)
    """
    
    def __init__(self) -> None:
        """Initialize trade context tracker."""
        self._contexts: Dict[str, TradeContextEntry] = {}
        self._signal_to_order: Dict[str, str] = {}  # signal_id -> client_order_id
        self._lock: threading.Lock = threading.Lock()
    
    def register_signal(
        self,
        signal_id: str,
        whale_trade_ts: int,
        signal_detected_ts: int,
        signal_generated_ts: int,
        snapshot_id: str,
        side: str = "BUY",
    ) -> None:
        """Register signal detection phase.
        
        Args:
            signal_id: Unique signal identifier.
            whale_trade_ts: Monotonic timestamp when whale trade was detected.
            signal_detected_ts: Monotonic timestamp when signal processing started.
            signal_generated_ts: Monotonic timestamp when signal was generated.
            snapshot_id: ID of frozen snapshot for this signal.
            side: Trade side (BUY or SELL).
        """
        with self._lock:
            entry = TradeContextEntry(
                signal_id=signal_id,
                whale_trade_ts=whale_trade_ts,
                signal_detected_ts=signal_detected_ts,
                signal_generated_ts=signal_generated_ts,
                snapshot_id=snapshot_id,
                side=side,
            )
            self._contexts[signal_id] = entry
    
    def register_submission(
        self,
        client_order_id: str,
        signal_id: str,
        submitted_ts: int,
        intended_price: float,
        intended_size: float,
    ) -> None:
        """Register order submission phase.
        
        Args:
            client_order_id: Nautilus client order ID.
            signal_id: Signal that triggered this order.
            submitted_ts: Monotonic timestamp when order was submitted.
            intended_price: Expected fill price from signal.
            intended_size: Position size in USD.
        """
        with self._lock:
            # Map signal_id to client_order_id for later lookup
            self._signal_to_order[signal_id] = client_order_id
            
            # Get or create entry
            if signal_id in self._contexts:
                entry = self._contexts[signal_id]
            else:
                entry = TradeContextEntry(signal_id=signal_id)
                self._contexts[signal_id] = entry
            
            # Update with submission data
            entry.submitted_ts = submitted_ts
            entry.intended_entry_price = intended_price
            entry.intended_size_usd = intended_size
            
            # Also store under client_order_id for fill lookup
            self._contexts[client_order_id] = entry
    
    def register_fill(
        self,
        client_order_id: str,
        filled_ts: int,
        actual_price: float,
        filled_size: float,
    ) -> None:
        """Register fill phase.
        
        Args:
            client_order_id: Nautilus client order ID.
            filled_ts: Monotonic timestamp when fill was received.
            actual_price: Actual fill price from market.
            filled_size: Filled position size in USD.
        """
        with self._lock:
            if client_order_id in self._contexts:
                entry = self._contexts[client_order_id]
                entry.filled_ts = filled_ts
                entry.actual_fill_price = actual_price
                entry.filled_size_usd = filled_size
    
    def compute_latencies(self, client_order_id: str) -> Dict[str, int]:
        """Compute latency metrics for a trade.
        
        Args:
            client_order_id: Nautilus client order ID.
            
        Returns:
            Dict with detection_delay_ms, execution_delay_ms, fill_delay_ms, total_latency_ms.
            Returns zeros if context incomplete.
        """
        with self._lock:
            if client_order_id not in self._contexts:
                return {
                    "detection_delay_ms": 0,
                    "execution_delay_ms": 0,
                    "fill_delay_ms": 0,
                    "total_latency_ms": 0,
                }
            
            entry = self._contexts[client_order_id]
            
            # Convert nanoseconds to milliseconds (divide by 1_000_000)
            ns_to_ms = 1_000_000
            
            detection_delay_ms = (entry.signal_generated_ts - entry.whale_trade_ts) // ns_to_ms
            execution_delay_ms = (entry.submitted_ts - entry.signal_generated_ts) // ns_to_ms
            fill_delay_ms = (entry.filled_ts - entry.submitted_ts) // ns_to_ms
            total_latency_ms = (entry.filled_ts - entry.whale_trade_ts) // ns_to_ms
            
            return {
                "detection_delay_ms": max(0, detection_delay_ms),
                "execution_delay_ms": max(0, execution_delay_ms),
                "fill_delay_ms": max(0, fill_delay_ms),
                "total_latency_ms": max(0, total_latency_ms),
            }
    
    def compute_slippage(self, client_order_id: str) -> Dict[str, float]:
        """Compute slippage metrics for a trade.
        
        Args:
            client_order_id: Nautilus client order ID.
            
        Returns:
            Dict with slippage_bps, fill_completion_pct.
            Returns zeros if context incomplete or price is zero.
        """
        with self._lock:
            if client_order_id not in self._contexts:
                return {"slippage_bps": 0.0, "fill_completion_pct": 0.0}
            
            entry = self._contexts[client_order_id]
            
            # Slippage calculation (in basis points)
            # For BUY: slippage = (actual - intended) / intended * 10000
            # For SELL: slippage = (intended - actual) / intended * 10000 (adverse direction)
            if entry.intended_entry_price > 0:
                if entry.side == "BUY":
                    # Positive slippage = paid more than expected (bad)
                    slippage_bps = (entry.actual_fill_price - entry.intended_entry_price) / entry.intended_entry_price * 10000
                else:
                    # For SELL, positive slippage = received less than expected (bad)
                    slippage_bps = (entry.intended_entry_price - entry.actual_fill_price) / entry.intended_entry_price * 10000
            else:
                slippage_bps = 0.0
            
            # Fill completion percentage
            if entry.intended_size_usd > 0:
                fill_completion_pct = (entry.filled_size_usd / entry.intended_size_usd) * 100
            else:
                fill_completion_pct = 0.0
            
            return {
                "slippage_bps": slippage_bps,
                "fill_completion_pct": fill_completion_pct,
            }
    
    def get_context(self, client_order_id: str) -> Optional[Dict]:
        """Get full context for a trade.
        
        Args:
            client_order_id: Nautilus client order ID.
            
        Returns:
            Dict of all context fields, or None if not found.
        """
        with self._lock:
            if client_order_id not in self._contexts:
                return None
            entry = self._contexts[client_order_id]
            return {
                "signal_id": entry.signal_id,
                "whale_trade_ts": entry.whale_trade_ts,
                "signal_detected_ts": entry.signal_detected_ts,
                "signal_generated_ts": entry.signal_generated_ts,
                "submitted_ts": entry.submitted_ts,
                "filled_ts": entry.filled_ts,
                "snapshot_id": entry.snapshot_id,
                "intended_entry_price": entry.intended_entry_price,
                "actual_fill_price": entry.actual_fill_price,
                "intended_size_usd": entry.intended_size_usd,
                "filled_size_usd": entry.filled_size_usd,
                "side": entry.side,
            }
    
    def clear_context(self, client_order_id: str) -> None:
        """Remove context for a completed trade.
        
        Args:
            client_order_id: Nautilus client order ID.
        """
        with self._lock:
            if client_order_id in self._contexts:
                entry = self._contexts[client_order_id]
                # Also remove signal_id mapping
                if entry.signal_id in self._signal_to_order:
                    del self._signal_to_order[entry.signal_id]
                del self._contexts[client_order_id]
    
    def clear_all(self) -> None:
        """Clear all contexts (for testing or reset)."""
        with self._lock:
            self._contexts.clear()
            self._signal_to_order.clear()


# Global instance for convenience
_trade_context: Optional[TradeContext] = None
_context_lock: threading.Lock = threading.Lock()


def get_trade_context() -> TradeContext:
    """Get or create global trade context instance.
    
    Returns:
        Global TradeContext instance.
    """
    global _trade_context
    with _context_lock:
        if _trade_context is None:
            _trade_context = TradeContext()
        return _trade_context