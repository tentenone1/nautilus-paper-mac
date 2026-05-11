"""
Shared fixtures for integration tests.
"""
import pytest
import sys
import time as time_module
from unittest.mock import MagicMock, patch
from pathlib import Path

sys.path.insert(0, ".")


# ─── Core Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def fresh_breakers():
    """Reset all three global circuit breakers to CLOSED state before each test."""
    from strategies.wf_circuit_breaker import (
        _whale_api_breaker,
        _clob_breaker,
        _gamma_breaker,
    )
    breakers = (_whale_api_breaker, _clob_breaker, _gamma_breaker)
    original_states = [(b._state, b._failure_count, b._last_failure_time, b._half_open_calls) for b in breakers]
    for b in breakers:
        b._state = "CLOSED"
        b._failure_count = 0
        b._last_failure_time = 0.0
        b._half_open_calls = 0
    yield
    # Restore original state after test
    for b, (state, count, last_fail, half_open) in zip(breakers, original_states):
        b._state = state
        b._failure_count = count
        b._last_failure_time = last_fail
        b._half_open_calls = half_open


@pytest.fixture
def mock_nautilus_env():
    """Build a minimal Nautilus environment with recording mocks."""
    cache = MagicMock()
    cache.positions_open.return_value = []
    cache.instrument.return_value = MagicMock()
    cache.quote_tick.return_value = None

    portfolio = MagicMock()
    account = MagicMock()
    account.balance_free.return_value = MagicMock()
    account.balance_free.return_value.as_double.return_value = 500.0
    portfolio.account.return_value = account

    order_factory = MagicMock()
    mock_order = MagicMock()
    mock_order.client_order_id = MagicMock()
    mock_order.client_order_id.__str__ = lambda self: "O-TEST-001"
    order_factory.market.return_value = mock_order

    exec_engine = MagicMock()
    clock = MagicMock()

    class Bunch:
        pass
    env = Bunch()
    env.cache = cache
    env.portfolio = portfolio
    env.order_factory = order_factory
    env.exec_engine = exec_engine
    env.clock = clock
    return env


@pytest.fixture
def mock_config():
    """Return a WhaleFollowerConfig with test-safe values."""
    from strategies.whale_follower import WhaleFollowerConfig
    cfg = WhaleFollowerConfig(
        instrument_ids=[],
        bankroll=1000.0,
        kelly_fraction=0.25,
        stop_loss_pct=0.25,
        take_profit_pct=0.50,
        max_position_pct=0.10,
        max_open_positions=10,
        max_single_position_pct=0.02,
        max_total_exposure_pct=0.20,
        validation_capital_base=1000.0,
        daily_loss_limit=100.0,
        min_confidence=0.55,
        scan_interval_secs=30.0,
        auto_trade=True,
        use_dynamic_kelly=True,
        seen_position_ttl=14400.0,
        max_hold_hours=4.0,
        tp_multiplier=2.5,
        trailing_stop=True,
        trailing_stop_retrace_pct=0.40,
        max_trades_per_scan=5,
        trade_buffer_flush_secs=30.0,
        test_mode=False,
        test_signal_interval_secs=300.0,
    )
    # Simulate _pending_order and _pending_whales
    cfg._pending_order = None
    cfg._pending_whales = {}
    return cfg


@pytest.fixture
def mock_logger():
    """Return a Logger backed by StringIO for log assertion."""
    import logging
    import io
    handler = io.StringIO()
    logger = logging.getLogger("test_integration")
    logger.setLevel(logging.DEBUG)
    logger.handlers = [logging.StreamHandler(handler)]
    return logger, handler


@pytest.fixture
def test_instrument():
    """Return a mock Nautilus Instrument."""
    instr = MagicMock()
    instr.size = MagicMock()
    instr.size.toDecimal.return_value.__float__ = lambda: 40.0
    instr.price = MagicMock()
    instr.price.toDecimal.return_value.__float__ = lambda: 0.50
    instr.quote_currency = MagicMock()
    instr.quote_currency.code = "USDC"
    return instr


@pytest.fixture
def position_registry():
    """Return a mutable {open_positions, exited_positions, last_exit_time} triplet."""
    class Registry:
        pass
    r = Registry()
    r.open_positions = {}
    r.exited_positions = set()
    r.last_exit_time = {}
    return r


@pytest.fixture
def whale_signal_factory():
    """Callable that creates a WhaleSignal with configurable fields."""
    from strategies.whale_tracker_new import WhaleSignal, WhaleSignalType, SignalSource
    import uuid

    def _factory(
        price=0.50,
        edge_score=0.60,
        confidence=0.80,
        whale_win_rate=0.65,
        whale_name="TestWhale",
        market_title="Test Market",
        market_category="politics",
        whale_amount=1000.0,
    ):
        return WhaleSignal(
            signal_id=str(uuid.uuid4()),
            source=SignalSource.WHALETRACKER,
            whale_name=whale_name,
            market_title=market_title,
            market_category=market_category,
            condition_id="cond123",
            outcome="YES",
            price=price,
            edge_score=edge_score,
            confidence=confidence,
            whale_win_rate=whale_win_rate,
            whale_amount=whale_amount,
            timestamp=time_module.time(),
        )
    return _factory
