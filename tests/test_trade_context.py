"""Tests for trade_context module."""

import sys
import time
from pathlib import Path

# Load trade_context module directly (bypass components/__init__.py)
_trade_context_path = Path(__file__).resolve().parent.parent / "components" / "validation" / "trade_context.py"
_spec = __import__("importlib.util").util.spec_from_file_location("trade_context", _trade_context_path)
trade_context_module = __import__("importlib.util").util.module_from_spec(_spec)
sys.modules["trade_context"] = trade_context_module
_spec.loader.exec_module(trade_context_module)

TradeContext = trade_context_module.TradeContext


def test_register_signal_and_compute_latencies():
    """Test full signal → submission → fill lifecycle."""
    ctx = TradeContext()
    
    # Simulate timestamps (monotonic_ns)
    whale_ts = time.monotonic_ns()
    signal_detected_ts = whale_ts + 50_000_000  # 50ms later
    signal_generated_ts = whale_ts + 100_000_000  # 100ms later
    submitted_ts = whale_ts + 200_000_000  # 200ms later (100ms execution delay)
    filled_ts = whale_ts + 350_000_000  # 350ms later (150ms fill delay)
    
    # Register phases
    ctx.register_signal(
        signal_id="sig-1",
        whale_trade_ts=whale_ts,
        signal_detected_ts=signal_detected_ts,
        signal_generated_ts=signal_generated_ts,
        snapshot_id="snap-1",
        side="BUY",
    )
    
    ctx.register_submission(
        client_order_id="order-1",
        signal_id="sig-1",
        submitted_ts=submitted_ts,
        intended_price=0.50,
        intended_size=10.0,
    )
    
    ctx.register_fill(
        client_order_id="order-1",
        filled_ts=filled_ts,
        actual_price=0.52,
        filled_size=10.0,
    )
    
    # Compute latencies
    latencies = ctx.compute_latencies("order-1")
    
    assert latencies["detection_delay_ms"] == 100  # 100ms from whale to signal
    assert latencies["execution_delay_ms"] == 100  # 100ms from signal to submit
    assert latencies["fill_delay_ms"] == 150  # 150ms from submit to fill
    assert latencies["total_latency_ms"] == 350  # 350ms total


def test_compute_slippage_buy():
    """Test slippage calculation for BUY side."""
    ctx = TradeContext()
    
    ctx.register_signal(
        signal_id="sig-2",
        whale_trade_ts=time.monotonic_ns(),
        signal_detected_ts=time.monotonic_ns(),
        signal_generated_ts=time.monotonic_ns(),
        snapshot_id="snap-2",
        side="BUY",
    )
    
    ctx.register_submission(
        client_order_id="order-2",
        signal_id="sig-2",
        submitted_ts=time.monotonic_ns(),
        intended_price=0.50,
        intended_size=10.0,
    )
    
    ctx.register_fill(
        client_order_id="order-2",
        filled_ts=time.monotonic_ns(),
        actual_price=0.52,  # 4% higher = 400 bps slippage
        filled_size=8.0,  # 80% fill completion
    )
    
    slippage = ctx.compute_slippage("order-2")
    
    assert abs(slippage["slippage_bps"] - 400.0) < 0.1  # 0.52 - 0.50 / 0.50 * 10000
    assert abs(slippage["fill_completion_pct"] - 80.0) < 0.1


def test_compute_slippage_sell():
    """Test slippage calculation for SELL side."""
    ctx = TradeContext()
    
    ctx.register_signal(
        signal_id="sig-3",
        whale_trade_ts=time.monotonic_ns(),
        signal_detected_ts=time.monotonic_ns(),
        signal_generated_ts=time.monotonic_ns(),
        snapshot_id="snap-3",
        side="SELL",
    )
    
    ctx.register_submission(
        client_order_id="order-3",
        signal_id="sig-3",
        submitted_ts=time.monotonic_ns(),
        intended_price=0.50,
        intended_size=10.0,
    )
    
    ctx.register_fill(
        client_order_id="order-3",
        filled_ts=time.monotonic_ns(),
        actual_price=0.48,  # 4% lower = 400 bps adverse slippage for SELL
        filled_size=10.0,
    )
    
    slippage = ctx.compute_slippage("order-3")
    
    assert abs(slippage["slippage_bps"] - 400.0) < 0.1  # 0.50 - 0.48 / 0.50 * 10000
    assert abs(slippage["fill_completion_pct"] - 100.0) < 0.1


def test_clear_context():
    """Test context cleanup."""
    ctx = TradeContext()
    
    ctx.register_signal(
        signal_id="sig-4",
        whale_trade_ts=time.monotonic_ns(),
        signal_detected_ts=time.monotonic_ns(),
        signal_generated_ts=time.monotonic_ns(),
        snapshot_id="snap-4",
    )
    
    ctx.register_submission(
        client_order_id="order-4",
        signal_id="sig-4",
        submitted_ts=time.monotonic_ns(),
        intended_price=0.50,
        intended_size=10.0,
    )
    
    # Context exists
    assert ctx.get_context("order-4") is not None
    
    # Clear it
    ctx.clear_context("order-4")
    
    # Context removed
    assert ctx.get_context("order-4") is None


def test_missing_context_returns_zeros():
    """Test that missing context returns safe defaults."""
    ctx = TradeContext()
    
    latencies = ctx.compute_latencies("nonexistent")
    slippage = ctx.compute_slippage("nonexistent")
    
    assert latencies["detection_delay_ms"] == 0
    assert latencies["execution_delay_ms"] == 0
    assert latencies["fill_delay_ms"] == 0
    assert latencies["total_latency_ms"] == 0
    assert slippage["slippage_bps"] == 0.0
    assert slippage["fill_completion_pct"] == 0.0