"""Circuit breaker stubs — module referenced in whale_follower.py but not yet implemented."""

class CircuitBreakerOpen(Exception):
    """Raised when a circuit breaker is in open state."""
    pass


def get_whale_api_breaker():
    """Return a no-op circuit breaker wrapper for the whale API."""
    return _NoOpBreaker()


def get_clob_breaker():
    """Return a no-op circuit breaker wrapper for the CLOB client."""
    return _NoOpBreaker()


class _NoOpBreaker:
    """No-op breaker — allows all calls through."""

    def __init__(self):
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def record_success(self) -> None:
        pass

    def record_failure(self) -> None:
        pass

    def call(self, fn, *args, **kwargs):
        """Synchronous call wrapper — passes through to fn."""
        return fn(*args, **kwargs)
