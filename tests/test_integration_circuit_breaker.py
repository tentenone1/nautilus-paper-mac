"""
Integration tests: Circuit Breaker State Machine.
"""
import pytest
import sys
import time as time_module
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from strategies.wf_circuit_breaker import (
    get_whale_api_breaker,
    get_clob_breaker,
    get_gamma_breaker,
    CircuitBreakerOpen,
    _whale_api_breaker,
    _clob_breaker,
    _gamma_breaker,
)


class TestWhaleApiBreaker:
    """Tests for whale API circuit breaker."""

    def test_breaker_opens_after_3_failures(self, fresh_breakers):
        """Simulate 3 consecutive failures → breaker opens."""
        breaker = get_whale_api_breaker()

        failing_func = MagicMock(side_effect=ConnectionError("network error"))

        # First 3 calls: raise original exception (not CircuitBreakerOpen)
        for i in range(3):
            with pytest.raises(ConnectionError):
                breaker.call(failing_func)

        # After 3 failures, breaker should be OPEN
        assert breaker._state == "OPEN", f"Expected OPEN, got {breaker._state}"

    def test_breaker_open_state_fails_fast(self, fresh_breakers):
        """When OPEN, 4th call raises CircuitBreakerOpen immediately."""
        breaker = get_whale_api_breaker()

        failing_func = MagicMock(side_effect=ConnectionError("network error"))

        # Trip the breaker
        for i in range(3):
            with pytest.raises(ConnectionError):
                breaker.call(failing_func)

        # 4th call: fail fast without calling the function
        with pytest.raises(CircuitBreakerOpen):
            breaker.call(failing_func)

        # The function should NOT have been called a 4th time
        assert failing_func.call_count == 3

    def test_breaker_transitions_to_half_open_after_cooldown(self, fresh_breakers):
        """After cooldown expires, breaker moves to HALF_OPEN and allows trial call."""
        breaker = get_whale_api_breaker()

        failing_func = MagicMock(side_effect=ConnectionError("network error"))
        success_func = MagicMock(return_value="ok")

        # Trip the breaker
        for i in range(3):
            with pytest.raises(ConnectionError):
                breaker.call(failing_func)

        assert breaker._state == "OPEN"

        # Advance time past cooldown (breaker.COOLDOWN_SECS = 30)
        breaker._last_failure_time = time_module.time() - 31

        # State should now be HALF_OPEN (must call .state property to trigger transition)
        assert breaker.state == "HALF_OPEN"

        # HALF_OPEN: call succeeds and resets to CLOSED
        result = breaker.call(success_func)
        assert result == "ok"
        assert breaker._state == "CLOSED"
        assert breaker._failure_count == 0

    def test_breaker_resets_after_successful_half_open_call(self, fresh_breakers):
        """Successful call in HALF_OPEN resets failure count to 0."""
        breaker = get_whale_api_breaker()

        failing_func = MagicMock(side_effect=ConnectionError("network error"))
        success_func = MagicMock(return_value="success")

        # Trip breaker
        for i in range(3):
            with pytest.raises(ConnectionError):
                breaker.call(failing_func)

        # Advance past cooldown
        breaker._last_failure_time = time_module.time() - 31

        # Should be HALF_OPEN (call .state property to trigger transition)
        assert breaker.state == "HALF_OPEN"

        # Successful call resets
        breaker.call(success_func)
        assert breaker._failure_count == 0
        assert breaker._state == "CLOSED"


class TestClobBreaker:
    """Tests for CLOB API circuit breaker."""

    def test_clob_breaker_opens_after_threshold(self, fresh_breakers):
        """CLOB breaker trips after 3 consecutive failures."""
        breaker = get_clob_breaker()

        failing_func = MagicMock(side_effect=Exception("CLOB error"))

        for i in range(3):
            with pytest.raises(Exception):
                breaker.call(failing_func)

        assert breaker._state == "OPEN"

    def test_clob_breaker_fails_fast_when_open(self, fresh_breakers):
        """Open CLOB breaker fails fast on 4th call."""
        breaker = get_clob_breaker()

        failing_func = MagicMock(side_effect=Exception("CLOB error"))

        for i in range(3):
            with pytest.raises(Exception):
                breaker.call(failing_func)

        with pytest.raises(CircuitBreakerOpen):
            breaker.call(failing_func)

        assert failing_func.call_count == 3


class TestGammaBreaker:
    """Tests for gamma API circuit breaker."""

    def test_gamma_breaker_opens_and_fails_fast(self, fresh_breakers):
        """Gamma breaker trips and fails fast."""
        breaker = get_gamma_breaker()

        failing_func = MagicMock(side_effect=Exception("gamma error"))

        for i in range(3):
            with pytest.raises(Exception):
                breaker.call(failing_func)

        assert breaker._state == "OPEN"

        with pytest.raises(CircuitBreakerOpen):
            breaker.call(failing_func)
