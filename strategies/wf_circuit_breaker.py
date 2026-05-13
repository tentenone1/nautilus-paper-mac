"""Production-grade thread-safe circuit breaker for external service calls."""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Callable


# Circuit breaker state constants
_DEFAULT_FAILURE_THRESHOLD = 5
_DEFAULT_RECOVERY_TIMEOUT = 60.0
_WHALE_API_FAILURE_THRESHOLD = 5
_WHALE_API_RECOVERY_TIMEOUT = 60.0
_CLOB_FAILURE_THRESHOLD = 3
_CLOB_RECOVERY_TIMEOUT = 30.0


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a circuit breaker is in open state."""

    def __init__(self, name: str, failure_count: int, recovery_timeout: float) -> None:
        self.name = name
        self.failure_count = failure_count
        self.recovery_timeout = recovery_timeout
        super().__init__(
            f"Circuit breaker '{name}' is open after {failure_count} failures. "
            f"Retry after {recovery_timeout}s."
        )


class CircuitBreaker:
    """Thread-safe circuit breaker implementation.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit tripped, requests fail immediately
    - HALF_OPEN: Recovery mode, one test request allowed

    Transitions:
    - CLOSED -> OPEN: When consecutive failures >= failure_threshold
    - OPEN -> HALF_OPEN: After recovery_timeout seconds
    - HALF_OPEN -> CLOSED: On successful test request
    - HALF_OPEN -> OPEN: On failed test request
    """

    def __init__(
        self,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = _DEFAULT_RECOVERY_TIMEOUT,
        name: str = "",
    ) -> None:
        """Initialize circuit breaker.

        Args:
            failure_threshold: Consecutive failures before transitioning to OPEN state.
            recovery_timeout: Seconds to wait before transitioning OPEN->HALF_OPEN.
            name: Identifier for this circuit breaker (used in exception messages).
        """
        self._lock = threading.Lock()
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0

    @property
    def name(self) -> str:
        """Return the circuit breaker name."""
        return self._name

    @property
    def state(self) -> CircuitState:
        """Return the current circuit breaker state."""
        with self._lock:
            self._transition()
            return self._state

    @property
    def failure_count(self) -> int:
        """Return the current consecutive failure count."""
        with self._lock:
            return self._failure_count

    def is_open(self) -> bool:
        """Check if the circuit breaker is open (blocking calls).

        Returns:
            True if the circuit is open, False otherwise.
        """
        with self._lock:
            self._transition()
            return self._state == CircuitState.OPEN

    def is_half_open(self) -> bool:
        """Check if the circuit breaker is in half-open state (testing recovery).

        Returns:
            True if the circuit is half-open, False otherwise.
        """
        with self._lock:
            self._transition()
            return self._state == CircuitState.HALF_OPEN

    def record_success(self) -> None:
        """Record a successful call, resetting failure count and closing circuit.

        Transitions HALF_OPEN->CLOSED on success.
        """
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed call, incrementing failure count and potentially opening circuit.

        Transitions:
        - CLOSED->OPEN: When failure_count >= failure_threshold
        - HALF_OPEN->OPEN: On any failure in half-open state
        """
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
            elif self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a function through the circuit breaker.

        Args:
            fn: The function to execute.
            *args: Positional arguments to pass to fn.
            **kwargs: Keyword arguments to pass to fn.

        Returns:
            The return value of fn.

        Raises:
            CircuitBreakerOpen: If the circuit is open.
            Any exception raised by fn is caught, recorded as failure, and re-raised.
        """
        with self._lock:
            self._transition()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpen(
                    name=self._name,
                    failure_count=self._failure_count,
                    recovery_timeout=self._recovery_timeout,
                )

        try:
            result = fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def _transition(self) -> None:
        """Handle state transitions based on elapsed time.

        Must be called with self._lock held.

        Transitions:
        - OPEN->HALF_OPEN: When recovery_timeout has elapsed since last failure.
        """
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN


# Module-level singletons
_whale_api_breaker: CircuitBreaker | None = None
_clob_breaker: CircuitBreaker | None = None
_breaker_lock = threading.Lock()


def get_whale_api_breaker() -> CircuitBreaker:
    """Return a singleton circuit breaker for whale API calls.

    Returns:
        CircuitBreaker configured with threshold=5, timeout=60s.
    """
    global _whale_api_breaker
    if _whale_api_breaker is None:
        with _breaker_lock:
            if _whale_api_breaker is None:
                _whale_api_breaker = CircuitBreaker(
                    failure_threshold=_WHALE_API_FAILURE_THRESHOLD,
                    recovery_timeout=_WHALE_API_RECOVERY_TIMEOUT,
                    name="whale_api",
                )
    return _whale_api_breaker


def get_clob_breaker() -> CircuitBreaker:
    """Return a singleton circuit breaker for CLOB client calls.

    Returns:
        CircuitBreaker configured with threshold=3, timeout=30s.
    """
    global _clob_breaker
    if _clob_breaker is None:
        with _breaker_lock:
            if _clob_breaker is None:
                _clob_breaker = CircuitBreaker(
                    failure_threshold=_CLOB_FAILURE_THRESHOLD,
                    recovery_timeout=_CLOB_RECOVERY_TIMEOUT,
                    name="clob",
                )
    return _clob_breaker
