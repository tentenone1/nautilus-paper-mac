"""
Circuit breaker for external API calls.

Prevents cascade failures when downstream services (Polymarket, Gamma API)
are rate-limited or unavailable. After N consecutive failures, the breaker
opens for a cooldown period before allowing trial requests.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Any
import time


@dataclass
class CircuitBreaker:
    """
    Circuit breaker that trips after failure_threshold consecutive failures.
    
    States:
    - CLOSED: normal operation, requests pass through
    - OPEN: circuit is tripped, requests fail fast
    - HALF_OPEN: after cooldown, allowing one trial request
    """
    name: str
    failure_threshold: int = 3          # failures before opening
    cooldown_seconds: float = 30.0       # seconds before half-open
    half_open_max_calls: int = 1        # trial calls allowed in half-open
    
    _failure_count: int = field(default=0, init=False)
    _state: str = field(default="CLOSED", init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    
    @property
    def state(self) -> str:
        if self._state == "OPEN":
            if time.time() - self._last_failure_time >= self.cooldown_seconds:
                self._state = "HALF_OPEN"
                self._half_open_calls = 0
        return self._state

    def _record_open(self) -> None:
        """Record a circuit breaker open event in metrics."""
        try:
            from components.metrics import get_metrics
            get_metrics().increment_circuit_breaker_open(self.name)
        except Exception:
            # Metrics is optional — never let circuit breaker fail due to metrics
            pass
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute func with circuit breaker protection."""
        current_state = self.state
        
        if current_state == "OPEN":
            raise CircuitBreakerOpen(f"Circuit {self.name} is OPEN")
        
        if current_state == "HALF_OPEN":
            if self._half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerOpen(f"Circuit {self.name} is HALF_OPEN (max trial calls reached)")
            self._half_open_calls += 1
        
        try:
            result = func(*args, **kwargs)
            # Success
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                self._failure_count = 0
            return result
        except Exception as e:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                self._record_open()
            elif self._state == "HALF_OPEN":
                self._state = "OPEN"  # trial failed, go back to open
                self._record_open()
            raise


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open (fail-fast)."""
    pass


# Global circuit breakers for each external service
_whale_api_breaker = CircuitBreaker("whale_api", failure_threshold=3, cooldown_seconds=30.0)
_clob_breaker = CircuitBreaker("clob", failure_threshold=3, cooldown_seconds=15.0)
_gamma_breaker = CircuitBreaker("gamma_api", failure_threshold=3, cooldown_seconds=30.0)


def get_whale_api_breaker():
    return _whale_api_breaker


def get_clob_breaker():
    return _clob_breaker


def get_gamma_breaker():
    return _gamma_breaker
