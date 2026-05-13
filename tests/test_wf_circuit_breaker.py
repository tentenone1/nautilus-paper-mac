"""Tests for wf_circuit_breaker module."""

import threading
import time
import pytest
from strategies.wf_circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    get_whale_api_breaker,
    get_clob_breaker,
)


class TestCircuitBreakerOpen:
    """Tests for CircuitBreakerOpen exception."""

    def test_exception_message(self):
        exc = CircuitBreakerOpen(name="test", failure_count=5, recovery_timeout=60.0)
        assert "test" in str(exc)
        assert "5" in str(exc)
        assert "60.0" in str(exc)

    def test_exception_attributes(self):
        exc = CircuitBreakerOpen(name="api", failure_count=3, recovery_timeout=30.0)
        assert exc.name == "api"
        assert exc.failure_count == 3
        assert exc.recovery_timeout == 30.0


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_initial_state_closed(self):
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert not cb.is_open()
        assert not cb.is_half_open()

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, name="test")
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open()
        cb.record_failure()
        assert cb.is_open()
        assert cb.failure_count == 3

    def test_record_success_resets_count(self):
        cb = CircuitBreaker(failure_threshold=3, name="test")
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert not cb.is_open()

    def test_recovery_timeout_transitions_to_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, name="test")
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        time.sleep(0.15)
        assert cb.is_half_open()
        assert not cb.is_open()

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, name="test")
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        assert cb.is_half_open()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, name="test")
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        assert cb.is_half_open()
        cb.record_failure()
        assert cb.is_open()

    def test_call_executes_function(self):
        cb = CircuitBreaker(name="test")
        result = cb.call(lambda x: x * 2, 5)
        assert result == 10

    def test_call_records_success(self):
        cb = CircuitBreaker(failure_threshold=2, name="test")
        cb.call(lambda: None)
        assert cb.failure_count == 0

    def test_call_records_failure_and_reraises(self):
        cb = CircuitBreaker(failure_threshold=5, name="test")

        def failing_fn():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            cb.call(failing_fn)
        assert cb.failure_count == 1

    def test_call_raises_when_open(self):
        cb = CircuitBreaker(failure_threshold=2, name="test")
        cb.record_failure()
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen):
            cb.call(lambda: None)

    def test_name_property(self):
        cb = CircuitBreaker(name="my_breaker")
        assert cb.name == "my_breaker"


class TestThreadSafety:
    """Tests for thread-safety."""

    def test_concurrent_failures(self):
        cb = CircuitBreaker(failure_threshold=100, name="test")
        threads = []

        def fail():
            for _ in range(10):
                cb.record_failure()

        for _ in range(10):
            t = threading.Thread(target=fail)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert cb.failure_count == 100
        assert cb.is_open()

    def test_concurrent_calls(self):
        cb = CircuitBreaker(failure_threshold=1000, name="test")
        results = []
        lock = threading.Lock()

        def safe_call():
            try:
                cb.call(lambda: 42)
                with lock:
                    results.append(True)
            except CircuitBreakerOpen:
                pass

        threads = [threading.Thread(target=safe_call) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_get_whale_api_breaker(self):
        breaker = get_whale_api_breaker()
        assert breaker.name == "whale_api"
        assert breaker._failure_threshold == 5
        assert breaker._recovery_timeout == 60.0

    def test_get_clob_breaker(self):
        breaker = get_clob_breaker()
        assert breaker.name == "clob"
        assert breaker._failure_threshold == 3
        assert breaker._recovery_timeout == 30.0

    def test_singletons(self):
        b1 = get_whale_api_breaker()
        b2 = get_whale_api_breaker()
        assert b1 is b2

        c1 = get_clob_breaker()
        c2 = get_clob_breaker()
        assert c1 is c2
