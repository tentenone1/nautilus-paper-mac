"""API rate limiting and retry logic for Polymarket Data API."""

import time
import random
from typing import Optional, Tuple, Callable
from functools import wraps
import requests


class APIRateLimiter:
    """Rate limiter with retry logic for Polymarket API.
    
    Handles:
    - Rate limit headers (Retry-After, X-RateLimit-*)
    - HTTP errors (429, 500, 503)
    - Timeouts and connection errors
    - Exponential backoff with jitter
    - Request counting for monitoring
    """
    
    def __init__(
        self,
        base_url: str = "https://data-api.polymarket.com",
        default_timeout: float = 10.0,
        default_limit: int = 100,
        max_retries: int = 5,
        backoff_factor: float = 2.0,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: float = 0.1,  # Jitter factor
    ):
        """Initialize rate limiter.
        
        Args:
            base_url: Base API URL
            default_timeout: Default request timeout
            default_limit: Default trades per request
            max_retries: Maximum retry attempts
            backoff_factor: Multiplier for exponential backoff
            base_delay: Base delay between retries
            max_delay: Maximum delay between retries
            jitter: Jitter factor (0.0-1.0) for randomized backoff
        """
        self._base_url = base_url
        self._default_timeout = default_timeout
        self._default_limit = default_limit
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter = jitter
        
        # Tracking
        self._last_request_time: float = 0.0
        self._request_count: int = 0
        self._last_response_headers: dict = {}
        self._last_response_code: int = 200
        self._last_response_data: Optional[dict] = None
    
    @property
    def request_count(self) -> int:
        """Total requests made."""
        return self._request_count
    
    @property
    def requests_per_minute(self) -> float:
        """Estimated requests per minute."""
        if self._last_request_time:
            elapsed = time.time() - self._last_request_time
            if elapsed > 0:
                return self._request_count / (elapsed / 60)
        return 0.0
    
    def _maybe_throttle(self, delay: Optional[float] = None) -> None:
        """Apply delay based on rate limit headers."""
        if delay is None:
            delay = 0.0
        
        # Check rate limit headers
        retry_after = self._last_response_headers.get("Retry-After", "0")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                pass
        
        self._last_request_time = time.time()
        self._request_count += 1
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter."""
        # Exponential backoff
        delay = self._base_delay * (self._backoff_factor ** attempt)
        
        # Cap at max delay
        delay = min(delay, self._max_delay)
        
        # Add jitter (random factor)
        jitter_range = self._jitter * delay
        delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0.1, delay)  # Minimum 100ms
    
    def _with_retry(
        self,
        func: Callable[[], dict],
        params: dict,
        delay: Optional[float] = None,
    ) -> Tuple[Optional[dict], int]:
        """Execute API call with retry logic.
        
        Args:
            func: Function to execute (should return response data)
            params: Parameters for URL building
            delay: Initial delay (overrides rate limit header)
        
        Returns:
            Tuple of (response_data, retry_count)
        """
        last_error = None
        
        for attempt in range(self._max_retries):
            self._maybe_throttle(delay)
            
            try:
                # Build URL with pagination
                url = f"{self._base_url}/trades"
                url_params = {
                    "limit": self._default_limit,
                    "offset": params.get("offset", 0),
                }
                
                # Add condition_id if specified
                if params.get("condition_ids"):
                    url_params["conditionId"] = params["condition_ids"][0]
                
                # Make request
                resp = requests.get(
                    url,
                    params=url_params,
                    timeout=self._default_timeout,
                )
                
                self._last_response_code = resp.status_code
                self._last_response_headers = dict(resp.headers)
                
                if resp.status_code == 200:
                    data = resp.json()
                    self._last_response_data = data
                    return data, 0
                
                # Handle other status codes
                if resp.status_code == 429:  # Rate limited
                    delay = self._last_response_headers.get("Retry-After", 60)
                    try:
                        delay = float(delay)
                    except (ValueError, TypeError):
                        delay = 60
                    last_error = "Rate limited"
                    
                elif resp.status_code in (500, 502, 503, 504):  # Server errors
                    delay = self._calculate_delay(attempt)
                    last_error = f"Server error: {resp.status_code}"
                    
                else:
                    delay = 1.0
                    last_error = f"HTTP {resp.status_code}"
                    
            except requests.exceptions.Timeout:
                delay = self._calculate_delay(attempt)
                last_error = "Timeout"
            except requests.exceptions.ConnectionError:
                delay = self._calculate_delay(attempt)
                last_error = "Connection error"
            except requests.exceptions.TooManyRedirects:
                delay = self._calculate_delay(attempt)
                last_error = "Too many redirects"
            except Exception as e:
                delay = self._calculate_delay(attempt)
                last_error = f"Exception: {type(e).__name__}"
            
            # Wait before retry (unless it's a 429 with explicit header)
            if delay:
                time.sleep(delay)
        
        # All retries exhausted
        return self._last_response_data, self._max_retries
    
    def scan_trades(
        self,
        condition_ids: Optional[list[str]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> Optional[dict]:
        """Scan for recent trades with rate limiting.
        
        Args:
            condition_ids: Optional list of condition IDs to filter by
            offset: Pagination offset
            limit: Trades per page (overrides default)
        
        Returns:
            Response data or None on error
        """
        if limit is None:
            limit = self._default_limit
        
        def _fetch():
            url = f"{self._base_url}/trades"
            params = {
                "limit": limit,
                "offset": offset,
            }
            
            # Add condition_id if specified
            if condition_ids:
                params["conditionId"] = condition_ids[0]
            
            resp = requests.get(url, params=params, timeout=self._default_timeout)
            
            self._last_response_code = resp.status_code
            self._last_response_headers = dict(resp.headers)
            
            if resp.status_code == 200:
                return resp.json()
            return None
        
        data, retries = self._with_retry(_fetch, {
            "condition_ids": condition_ids,
            "offset": offset,
        })
        
        return data
    
    def get_trade_count_estimate(
        self,
        condition_id: str,
    ) -> Optional[int]:
        """Get estimated total trade count for a market.
        
        Uses the /stats endpoint if available.
        
        Args:
            condition_id: Market condition ID
        
        Returns:
            Estimated trade count or None
        """
        try:
            url = f"{self._base_url}/stats"
            params = {
                "conditionId": condition_id,
                "timeframe": "1d",
            }
            
            resp = requests.get(url, params=params, timeout=self._default_timeout)
            if resp.status_code == 200:
                data = resp.json()
                # stats[0][3] = total trades (based on API structure)
                return data[0][3] if data else 0
        except Exception:
            pass
        
        return None
    
    def get_rate_limit_info(self) -> dict:
        """Get current rate limit info."""
        return {
            "requests_made": self._request_count,
            "requests_per_minute": self.requests_per_minute,
            "last_request_time": self._last_request_time,
            "last_response_code": self._last_response_code,
            "last_response_headers": {
                "Retry-After": self._last_response_headers.get("Retry-After"),
                "X-RateLimit-Limit": self._last_response_headers.get("X-RateLimit-Limit"),
                "X-RateLimit-Remaining": self._last_response_headers.get("X-RateLimit-Remaining"),
                "X-RateLimit-Reset": self._last_response_headers.get("X-RateLimit-Reset"),
            },
            "max_retries": self._max_retries,
            "backoff_factor": self._backoff_factor,
        }
    
    def reset(self) -> None:
        """Reset tracking counters."""
        self._last_request_time = 0.0
        self._request_count = 0
        self._last_response_headers = {}
        self._last_response_code = 200
        self._last_response_data = None
