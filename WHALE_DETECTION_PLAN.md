# Whale Detection System Implementation Plan

## Executive Summary

This document provides a detailed implementation plan for the Nautilus WhaleFollower whale detection system. The system monitors 3 known profitable Polymarket whales (CemeterySun, CarlosMC, benwyatt) using the public Polymarket Data API to detect their positions, generate trading signals, and execute Kelly-sized positions.

**Goal:** Build a production-grade whale tracking system with:
- Persistent state (Redis + disk fallback)
- Efficient API scanning with rate limiting
- Signal validation and scoring
- Robust error handling and recovery
- Paper trading mode for validation

---

## 1. Architecture

### 1.1 High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WHALE DETECTION SYSTEM                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────────┐   │
│  │  Whale       │    │  State       │    │  Signal              │   │
│  │  Identity    │    │  Manager     │    │  Validator           │   │
│  │  Registry    │    │              │    │                      │   │
│  │              │    │  ┌──────────┐│    │  ┌─────────────────┐ │   │
│  │              │    │  │  Seen    ││    │  │  Confidence     │ │   │
│  │              │    │  │  Trades  ││    │  │  Score           │ │   │
│  │              │    │  └──────────┘│    │  │                  │ │   │
│  │              │    │  ┌──────────┐│    │  │  Position        │ │   │
│  │              │    │  │  Positions││    │  │  Tracker        │ │   │
│  │              │    │  └──────────┘│    │  └─────────────────┘ │   │
│  │              │    │  └──────────┘│    │  └─────────────────┘ │   │
│  └──────────────┘    │  ┌──────────┐│    │  ┌─────────────────┐ │   │
│  ┌──────────────┐    │  │  Rate    ││    │  │  Signal          │ │   │
│  │  API         │    │  │  Limit   ││    │  │  Publisher       │ │   │
│  │  Client      │    │  │  Lock    ││    │  │                  │ │   │
│  │  (Async)     │    │  └──────────┘│    │  └─────────────────┘ │   │
│  │              │    │  ┌──────────┐│    │  └─────────────────┘ │   │
│  │              │    │  │  Market  ││    │  ┌─────────────────┐ │   │
│  │              │    │  │  Data    ││    │  │  Instrument     │ │   │
│  │              │    │  │  Bridge  ││    │  │  Mapper         │ │   │
│  │              │    │  └──────────┘│    │  └─────────────────┘ │   │
│  │              │    │  └──────────┘│    │  └─────────────────┘ │   │
│  └──────────────┘    └──────────────┘    └─────────────────────┘   │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Data Sources                              │    │
│  │  ┌────────────────┐    ┌────────────────┐    ┌────────────┐ │    │
│  │  │  Polymarket    │    │  Nautilus      │    │  Redis     │ │    │
│  │  │  Data API     │    │  Market Data   │    │  (State)   │ │    │
│  │  │  (Polling)    │    │  (WebSocket)   │    │            │ │    │
│  │  └────────────────┘    └────────────────┘    └────────────┘ │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Core Components

#### 1.2.1 WhaleIdentityRegistry
**Location:** `/strategies/whale_tracker.py`
**Purpose:** Central registry of known whales with metadata

```python
class WhaleIdentityRegistry:
    """Persistent registry of known whale wallets."""
    
    def __init__(self):
        self._whales: dict[str, WhaleIdentity] = {}
        self._whale_names: dict[str, str] = {}  # wallet -> name
        self._whale_wallets: dict[str, str] = {}  # name -> wallet
        
        # Load from config file or env
        self._load_from_config()
    
    def register(self, whale: WhaleIdentity) -> None:
        """Register a new whale."""
        self._whales[whale.proxy_wallet] = whale
        self._whale_names[whale.proxy_wallet] = whale.name
        self._whale_wallets[whale.name] = whale.proxy_wallet
    
    def get_whale(self, wallet: str) -> WhaleIdentity | None:
        """Get whale by wallet address."""
        return self._whales.get(wallet)
    
    def get_whale_by_name(self, name: str) -> WhaleIdentity | None:
        """Get whale by name."""
        wallet = self._whale_wallets.get(name)
        return self._whales.get(wallet) if wallet else None
    
    def is_tracked(self, wallet: str) -> bool:
        """Check if wallet is tracked."""
        return wallet in self._whales
```

**Known Whales:**
| Name | Proxy Wallet | Avg Trade Size | Style |
|------|-------------|----------------|-------|
| CemeterySun | `0x4bbe10ba5b7f6df147c0dae17b46c44a6e562cf3` | ~$50,000 | Event-driven |
| CarlosMC | `0x96489abcb9f583d6835c8ef95ffc923d05a86825` | ~$65,000 | Contrarian |
| benwyatt | `0x03e8a544e97eeff5753bc1e90d46e5ef22af1697` | ~$60,000 | Research-based |

#### 1.2.2 StateManager
**Location:** `/components/state_manager.py`
**Purpose:** Persistent state management using Redis with disk fallback

```python
from dataclasses import dataclass
from typing import Optional
import redis
import json
import hashlib
import time

@dataclass
class SeenTradeKey:
    """Key for deduplication."""
    wallet: str
    condition_id: str
    timestamp_ms: int
    sequence: int  # Monotonically increasing from API
    
    @property
    def cache_key(self) -> str:
        """Generate Redis cache key."""
        return f"whale_seen:{self.wallet}:{self.condition_id}:{self.timestamp_ms}"

class StateManager:
    """Persistent state management using Redis."""
    
    def __init__(self, redis_url: str = "redis://localhost:6379", fallback_dir: str = "."):
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._sequence: int = 0
        self._last_scan: float = 0.0
        self._rate_limit_lock = redis.Lock(self._redis, "whale_rate_limit", timeout=10)
        self._fallback_dir = fallback_dir
    
    def _get_sequence(self) -> int:
        """Get and increment global sequence counter."""
        self._sequence = self._redis.incr("whale_sequence")
        return self._sequence
    
    def _increment_sequence(self, wallet: str, condition_id: str, timestamp_ms: int) -> int:
        """Increment sequence for specific whale+market."""
        key = f"whale_seq:{wallet}:{condition_id}"
        return self._redis.incr(key)
    
    def has_seen_trade(self, wallet: str, condition_id: str, timestamp_ms: int, sequence: int) -> bool:
        """Check if trade has been seen before."""
        trade_key = SeenTradeKey(wallet, condition_id, timestamp_ms, sequence).cache_key
        return self._redis.exists(trade_key)
    
    def mark_seen_trade(self, wallet: str, condition_id: str, timestamp_ms: int, sequence: int) -> None:
        """Mark trade as seen."""
        trade_key = SeenTradeKey(wallet, condition_id, timestamp_ms, sequence).cache_key
        self._redis.setex(trade_key, 86400*365, "1")  # Expire in 1 year
    
    def get_last_scan_time(self) -> float:
        """Get last scan time."""
        return self._last_scan
    
    def set_last_scan_time(self, timestamp: float) -> None:
        """Set last scan time."""
        self._last_scan = timestamp
    
    def get_rate_limit_lock(self) -> bool:
        """Acquire rate limit lock."""
        return self._rate_limit_lock.acquire(blocking=True, timeout=5)
    
    def release_rate_limit_lock(self) -> None:
        """Release rate limit lock."""
        self._rate_limit_lock.release()
```

#### 1.2.3 APIRateLimiter
**Location:** `/components/api_rate_limiter.py`
**Purpose:** Handle API rate limits and retries

```python
import time
import random
from typing import Optional, Tuple, Callable

class APIRateLimiter:
    """Rate limiter with retry logic for Polymarket API."""
    
    def __init__(self, 
                 base_url: str = "https://data-api.polymarket.com",
                 default_timeout: float = 10.0,
                 default_limit: int = 100,
                 max_retries: int = 5,
                 backoff_factor: float = 2.0,
                 base_delay: float = 1.0,
                 max_delay: float = 60.0,
                 jitter: float = 0.1):
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
    
    def _with_retry(self, func: Callable[[], dict], params: dict) -> Tuple[Optional[dict], int]:
        """Execute API call with retry logic."""
        last_error = None
        
        for attempt in range(self._max_retries):
            self._maybe_throttle()
            
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
                resp = requests.get(url, params=url_params, timeout=self._default_timeout)
                
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
    
    def scan_trades(self,
                    condition_ids: Optional[list[str]] = None,
                    offset: int = 0,
                    limit: Optional[int] = None) -> Optional[dict]:
        """Scan for recent trades with rate limiting."""
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
```

#### 1.2.4 SignalValidator
**Location:** `/components/signal_validator.py`
**Purpose:** Validate whale trade signals before acting

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import time

class SignalState(Enum):
    """Signal validation states."""
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    TIMEOUT = "timeout"
    ERROR = "error"

@dataclass
class ValidationResult:
    """Result of signal validation."""
    state: SignalState
    confidence: float
    reason: str
    timestamp: float
    metadata: dict = None
    
    @property
    def is_valid(self) -> bool:
        return self.state == SignalState.VALIDATED
    
    @property
    def is_rejected(self) -> bool:
        return self.state in (SignalState.REJECTED, SignalState.DUPLICATE)
    
    @property
    def is_timeout(self) -> bool:
        return self.state == SignalState.TIMEOUT
    
    @property
    def is_error(self) -> bool:
        return self.state == SignalState.ERROR
    
    def __str__(self) -> str:
        return f"{self.state.value}: {self.reason}"

class SignalValidator:
    """Validate whale trade signals."""
    
    def __init__(self,
                 min_confidence: float = 0.60,
                 min_trade_size: float = 5000.0,
                 max_trade_size: float = 200000.0,
                 max_price_deviation: float = 0.05,
                 time_decay_factor: float = 0.999,  # 0.1% decay per second
                 min_time_since_trade: float = 0.0):
        """Initialize validator.
        
        Args:
            min_confidence: Minimum confidence to accept signal
            min_trade_size: Minimum trade size in USD
            max_trade_size: Maximum trade size in USD
            max_price_deviation: Maximum price deviation (0.05 = 5%)
            time_decay_factor: Time decay factor per second
            min_time_since_trade: Minimum time since trade for freshness
        """
        self._min_confidence = min_confidence
        self._min_trade_size = min_trade_size
        self._max_trade_size = max_trade_size
        self._max_price_deviation = max_price_deviation
        self._time_decay_factor = time_decay_factor
        self._min_time_since_trade = min_time_since_trade
    
    def validate_signal(self,
                        whale_name: str,
                        whale_wallet: str,
                        condition_id: str,
                        token_id: str,
                        side: str,
                        outcome: str,
                        size: float,
                        price: float,
                        usd_value: float,
                        timestamp: float,
                        current_market_price: Optional[float] = None,
                        whale_roi: Optional[float] = None,
                        whale_win_rate: Optional[float] = None,
                        whale_avg_trade_size: Optional[float] = None,
                        whale_style: Optional[str] = None,
    ) -> ValidationResult:
        """Validate a whale trade signal."""
        try:
            # Check trade size bounds
            if usd_value < self._min_trade_size:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Trade too small: ${usd_value:,.0f} < ${self._min_trade_size:,.0f}",
                    timestamp=timestamp,
                )
            
            if usd_value > self._max_trade_size:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Trade too large: ${usd_value:,.0f} > ${self._max_trade_size:,.0f}",
                    timestamp=timestamp,
                )
            
            # Check price (should be 0.01-0.99 for binary options)
            if price <= 0.01 or price >= 0.99:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Price near resolution: {price:.3f}",
                    timestamp=timestamp,
                )
            
            # Check price deviation if we have current price
            if current_market_price:
                deviation = abs(price - current_market_price) / current_market_price
                if deviation > self._max_price_deviation:
                    return ValidationResult(
                        state=SignalState.REJECTED,
                        confidence=0.0,
                        reason=f"Price deviation: {price:.3f} vs {current_market_price:.3f} ({deviation*100:.1f}%)",
                        timestamp=timestamp,
                    )
            
            # Calculate confidence score
            confidence = self._calculate_confidence(
                whale_name=whale_name,
                whale_roi=whale_roi,
                whale_win_rate=whale_win_rate,
                whale_avg_trade_size=whale_avg_trade_size,
                whale_style=whale_style,
                usd_value=usd_value,
                timestamp=timestamp,
            )
            
            # Check confidence threshold
            if confidence < self._min_confidence:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=confidence,
                    reason=f"Confidence below threshold: {confidence:.0%} < {self._min_confidence:.0%}",
                    timestamp=timestamp,
                    metadata={"confidence": confidence},
                )
            
            # All checks passed
            return ValidationResult(
                state=SignalState.VALIDATED,
                confidence=confidence,
                reason=f"{whale_name} ({confidence:.0%} conf, {whale_style}) {side} {outcome}",
                timestamp=timestamp,
                metadata={
                    "whale_roi": whale_roi,
                    "whale_win_rate": whale_win_rate,
                    "trade_size_factor": usd_value / whale_avg_trade_size if whale_avg_trade_size else 1.0,
                    "price_deviation": abs(price - current_market_price) / current_market_price if current_market_price else 0.0,
                },
            )
        
        except Exception as e:
            return ValidationResult(
                state=SignalState.ERROR,
                confidence=0.0,
                reason=f"Validation error: {e}",
                timestamp=timestamp,
            )
    
    def _calculate_confidence(self,
                            whale_name: str,
                            whale_roi: Optional[float],
                            whale_win_rate: Optional[float],
                            whale_avg_trade_size: Optional[float],
                            whale_style: Optional[str],
                            usd_value: float,
                            timestamp: float,
                    ) -> float:
        """Calculate signal confidence score.
        
        Base formula:
        confidence = (win_rate * 0.8 + 0.2) * size_factor * style_bonus
        
        Where:
        - win_rate * 0.8 + 0.2: Base confidence from historical performance
        - size_factor: Adjust for trade size (larger = more confidence)
        - style_bonus: Bonus for certain whale styles
        """
        # Base confidence from win rate
        base_confidence = 0.5  # Default if win rate unknown
        
        if whale_win_rate is not None:
            base_confidence = whale_win_rate * 0.8 + 0.2
        
        # Adjust for trade size
        if whale_avg_trade_size and whale_avg_trade_size > 0:
            size_ratio = usd_value / whale_avg_trade_size
            size_factor = min(size_ratio / 2.0, 2.0)  # Cap at 2x
            base_confidence *= size_factor
        
        # Style bonus
        style_bonus = 0
        if whale_style == "event_driven":
            style_bonus = 0.1
        elif whale_style == "research_based":
            style_bonus = 0.05
        
        base_confidence = min(base_confidence + style_bonus, 0.95)
        
        # Time decay (older trades = less confidence)
        # Assume ~1000 trades per day average
        trades_per_day = 1000
        days_since_trade = (time.time() - timestamp) / 86400
        time_factor = self._time_decay_factor ** (days_since_trade * trades_per_day)
        base_confidence *= time_factor
        
        return min(base_confidence, 0.95)
```

#### 1.2.5 MarketDataBridge
**Location:** `/components/market_data_bridge.py`
**Purpose:** Bridge between Polymarket data API and Nautilus instruments

```python
from typing import Optional, List
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.adapters.polymarket.common.symbol import (
    get_polymarket_instrument_id,
    get_polymarket_condition_id,
    get_polymarket_token_id,
)

class MarketDataBridge:
    """Bridge between Polymarket data API and Nautilus instruments."""
    
    def __init__(self):
        """Initialize bridge."""
        self._instrument_cache: dict[str, InstrumentId] = {}
        self._condition_to_instrument: dict[str, InstrumentId] = {}
        self._token_to_instrument: dict[str, InstrumentId] = {}
        self._condition_to_token: dict[str, str] = {}
    
    def load_instrument_mapping(self,
                                condition_id: str,
                                token_id: str) -> InstrumentId:
        """Load instrument mapping for a specific market.
        
        Args:
            condition_id: Polymarket condition ID
            token_id: Polymarket token ID
        
        Returns:
            Nautilus InstrumentId
        """
        instrument_id = get_polymarket_instrument_id(condition_id, token_id)
        
        # Cache mappings
        self._instrument_cache[str(instrument_id)] = instrument_id
        self._condition_to_instrument[condition_id] = instrument_id
        self._token_to_instrument[token_id] = instrument_id
        self._condition_to_token[condition_id] = token_id
        
        return instrument_id
```

---

## 2. Data Flow

### 2.1 Initialization Phase

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INITIALIZATION FLOW                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  1. Create StateManager (connect to Redis)                           │
│     └─> Redis: whale_sequence, whale_last_scan                        │
│                                                                       │
│  2. Load whale registry from config                                   │
│     └─> whale_registry = WhaleIdentityRegistry()                      │
│                                                                       │
│  3. Load instrument mappings for target markets                       │
│     └─> bridge.load_instrument_mapping(condition_id, token_id)        │
│                                                                       │
│  4. Initialize API rate limiter                                       │
│     └─> rate_limiter = APIRateLimiter()                               │
│                                                                       │
│  5. Load any persisted state from disk (if available)                 │
│     └─> state_manager._load_from_disk()                               │
│     └─> state_manager._load_from_redis()                              │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Scan Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│                      SCAN LOOP FLOW                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  1. Check scan interval (e.g., 60s)                                   │
│     └─> if now - last_scan < scan_interval: return []                 │
│                                                                       │
│  2. Acquire rate limit lock                                           │
│     └─> lock = state_manager.get_rate_limit_lock()                    │
│     └─> if not lock: return "Lock acquired"                           │
│                                                                       │
│  3. Fetch 100 trades from API (offset 0)                              │
│     └─> trades = rate_limiter.scan_trades(offset=0, limit=100)        │
│                                                                       │
│  4. For each trade:                                                  │
│     a. Check if from tracked whale                                    │
│        └─> whale = whale_registry.get_whale(trade["proxyWallet"])      │
│        └─> if not whale: continue                                     │
│                                                                       │
│     b. Generate sequence number                                       │
│        └─> sequence = state_manager._get_sequence()                    │
│        └─> timestamp_ms = int(time.time() * 1000)                     │
│                                                                       │
│     c. Check if seen in Redis                                         │
│        └─> if state_manager.has_seen_trade(whale, condition_id, ...)  │
│            └─> continue                                                │
│                                                                       │
│     d. If new trade:                                                  │
│        i.  Mark as seen in Redis                                      │
│            └─> state_manager.mark_seen_trade(...)                     │
│        ii. Parse trade data                                           │
│            └─> trade = WhaleTrade(...)                                │
│        iii. Get current market price                                  │
│            └─> current_price = market_bridge.get_current_price(...)   │
│        iv.  Validate signal                                           │
│             └─> result = validator.validate_signal(...)               │
│        v.   If valid: publish signal                                  │
│             └─> signals.append(signal)                                │
│  5. Release rate limit lock                                           │
│     └─> state_manager.release_rate_limit_lock()                       │
│                                                                       │
│  6. Increment offset (for next scan)                                  │
│     └─> offset += 100                                                 │
│                                                                       │
│  7. Store scan time                                                   │
│     └─> state_manager.set_last_scan_time(now)                        │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 Signal Processing

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SIGNAL PROCESSING FLOW                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  1. Receive validated signal                                          │
│     └─> signal: WhaleSignal                                           │
│                                                                       │
│  2. Check confidence threshold                                        │
│     └─> if signal.confidence < min_confidence:                        │
│          └─> log.info("Low confidence, skipping")                    │
│          └─> continue                                                 │
│                                                                       │
│  3. Check if matches current instrument                               │
│     └─> if signal.token_id != current_instrument.token_id:            │
│          └─> continue                                                 │
│                                                                       │
│  4. If auto-trade enabled:                                           │
│     a. Calculate Kelly size                                           │
│        └─> size_usd = self._kelly_size(price)                        │
│        └─> if size_usd <= 0: continue                                │
│                                                                       │
│     b. Convert USD to share quantity                                  │
│        └─> qty = instrument.make_qty(size_usd / price)               │
│                                                                       │
│     c. Submit order                                                   │
│        └─> order = self.order_factory.limit(...)                      │
│        └─> self.submit_order(order)                                   │
│                                                                       │
│     d. Track entry price                                              │
│        └─> self._entry_prices[str(instrument_id)] = avg_px           │
│                                                                       │
│  5. Log signal                                                        │
│     └─> self.log.info(f"WHALE SIGNAL: {signal.reason}")              │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. State Management

### 3.1 Persistent JSON File for Seen Positions

**Location:** `/data/whale_state.json` (fallback, if Redis unavailable)

**Schema:**
```json
{
  "sequence": 123456,
  "last_scan": 1714401234.567,
  "seen_trades": [
    "whale_seen:0x4bbe10ba5b7f6df147c0dae17b46c44a6e562cf3:0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902:1714401234567",
    "whale_seen:0x96489abcb9f583d6835c8ef95ffc923d05a86825:0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902:1714401234567"
  ]
}
```

**Usage:**
- Primary storage: Redis (fast, distributed, persistent)
- Fallback storage: JSON file on disk (for Redis outages)
- On startup: Load from Redis first, then disk if Redis unavailable
- On shutdown: Save to both Redis and disk

### 3.2 Redis Keys

| Key | Type | Purpose | TTL |
|-----|------|---------|-----|
| `whale_sequence` | Int | Global sequence counter | Persistent |
| `whale_last_scan` | Float | Last scan timestamp | Persistent |
| `whale_rate_limit` | Lock | Rate limit lock | 10s |
| `whale_seen:*` | Set | Seen trades for dedup | 1 year |
| `whale_seq:{wallet}:{condition}` | Int | Per-whale sequence | Persistent |

### 3.3 State Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STATE LIFECYCLE                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  STARTUP:                                                            │
│  ├─> Try Redis: load_state()                                        │
│  ├─> If Redis OK: use Redis only                                    │
│  ├─> If Redis FAIL: try disk backup                                 │
│  ├─> If both FAIL: start with empty state (safe default)            │
│                                                                       │
│  OPERATION:                                                          │
│  ├─> Each scan: mark_seen_trade() → Redis SETEX (1 year TTL)        │
│  ├─> Increment sequence: INCR (atomic)                               │
│  ├─> Rate limit lock: SETNX + NX (10s timeout)                      │
│                                                                       │
│  SHUTDOWN:                                                           │
│  ├─> Flush any pending signals                                       │
│  ├─> Save state to disk (backup)                                     │
│  ├─> Close Redis connection                                          │
│  └─> Log summary                                                     │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Instrument Mapping

### 4.1 condition_id + token_id → Nautilus InstrumentId

**Formula:**
```python
from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_instrument_id

instrument_id = get_polymarket_instrument_id(condition_id, token_id)
# Example: "0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902-8441400852834915183759801017793514978104486628517653995211751018945988243154.POLY"
```

### 4.2 Mapping Cache

```python
from nautilus_trader.model.identifiers import InstrumentId

class MarketDataBridge:
    def __init__(self):
        self._instrument_cache: dict[str, InstrumentId] = {}
        self._condition_to_instrument: dict[str, InstrumentId] = {}
        self._token_to_instrument: dict[str, InstrumentId] = {}
        self._condition_to_token: dict[str, str] = {}
    
    def load_instrument_mapping(self,
                                condition_id: str,
                                token_id: str) -> InstrumentId:
        """Load instrument mapping for a specific market."""
        instrument_id = get_polymarket_instrument_id(condition_id, token_id)
        
        # Cache mappings
        self._instrument_cache[str(instrument_id)] = instrument_id
        self._condition_to_instrument[condition_id] = instrument_id
        self._token_to_instrument[token_id] = instrument_id
        self._condition_to_token[condition_id] = token_id
        
        return instrument_id
    
    def get_condition_id(self,
                         instrument_id: InstrumentId) -> Optional[str]:
        """Get condition ID for an instrument."""
        try:
            cond_id = self._condition_to_instrument.get(str(instrument_id))
            if not cond_id:
                # Try reverse mapping
                for inst, c_id in self._condition_to_instrument.items():
                    if str(inst) == str(instrument_id):
                        cond_id = c_id
                        break
        except Exception:
            pass
        return cond_id
    
    def get_token_id(self,
                     instrument_id: InstrumentId) -> Optional[str]:
        """Get token ID for an instrument."""
        try:
            token_id = self._token_to_instrument.get(str(instrument_id))
            if not token_id:
                # Try reverse mapping
                for inst, t_id in self._token_to_instrument.items():
                    if str(inst) == str(instrument_id):
                        token_id = t_id
                        break
        except Exception:
            pass
        return token_id
```

### 4.3 Example Mapping

| condition_id | token_id | InstrumentId |
|--------------|----------|---------------|
| `0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902` | `8441400852834915183759801017793514978104486628517653995211751018945988243154` | `0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902-8441400852834915183759801017793514978104486628517653995211751018945988243154.POLY` |

---

## 5. Error Handling

### 5.1 API Errors

| Error | Retry Logic | Backoff |
|-------|-------------|---------|
| 429 Rate Limited | 5 retries | `Retry-After` header or 60s |
| 500 Server Error | 5 retries | Exponential (2, 4, 8, 16, 32s) |
| 503 Unavailable | 5 retries | Exponential (2, 4, 8, 16, 32s) |
| 504 Gateway Timeout | 5 retries | Exponential (2, 4, 8, 16, 32s) |
| Timeout (10s) | 5 retries | 1s per retry |
| Connection Error | 5 retries | 1s per retry |
| TooManyRedirects | 5 retries | 1s per retry |

### 5.2 State Recovery

1. **On startup, load state from Redis**
2. **If Redis unavailable, load from disk backup**
3. **If both unavailable, start with empty state (safe default)**

### 5.3 Graceful Degradation

1. **If Redis unavailable → fall back to in-memory state**
2. **If API rate limited → increase scan interval**
3. **If signal validation fails → log and continue**

### 5.4 Retry Logic Implementation

```python
def _with_retry(self, func: Callable[[], dict], params: dict) -> Tuple[Optional[dict], int]:
    """Execute API call with retry logic."""
    last_error = None
    
    for attempt in range(self._max_retries):
        self._maybe_throttle()
        
        try:
            # Make request
            resp = requests.get(url, params=url_params, timeout=self._default_timeout)
            # ... handle response ...
            
            if resp.status_code == 200:
                data = resp.json()
                return data, 0
            
            # Handle other status codes
            if resp.status_code == 429:
                delay = self._last_response_headers.get("Retry-After", 60)
                try:
                    delay = float(delay)
                except (ValueError, TypeError):
                    delay = 60
                last_error = "Rate limited"
            
            elif resp.status_code in (500, 502, 503, 504):
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
```

---

## 6. Testing

### 6.1 Unit Tests

```bash
# Test signal validation
python -m pytest tests/test_signal_validator.py -v

# Test state management
python -m pytest tests/test_state_manager.py -v

# Test API rate limiting
python -m pytest tests/test_api_rate_limiter.py -v

# Test market data bridge
python -m pytest tests/test_market_data_bridge.py -v
```

### 6.2 Paper Trading

```bash
# Create paper trading config
cp .env.example .env
# Add paper credentials
POLYMARKET_PK="paper_private_key"
POLYMARKET_AUTO_TRADE="true"
REDIS_URL="redis://localhost:6379"

# Run with reduced limits for testing
python run_live.py \
  --scan-interval 10 \
  --limit 20 \
  --min-confidence 0.65 \
  --min-trade-size 10000

# Monitor output
tail -f logs/whale_follower.log
```

### 6.3 Performance Benchmarks

```bash
# Benchmark API scan speed
python scripts/benchmark_api.py

# Benchmark Redis operations
python scripts/benchmark_redis.py

# Benchmark full scan loop
python scripts/benchmark_scan_loop.py
```

### 6.4 Edge Case Testing

```bash
# Test API outage
python scripts/test_api_outage.py

# Test Redis outage
python scripts/test_redis_outage.py

# Test rate limiting
python scripts/test_rate_limiting.py
```

### 6.5 Validation Checklist

- [ ] Signal validation passes for known whales
- [ ] Duplicate trades are deduplicated
- [ ] Rate limits are handled gracefully
- [ ] Redis failures fall back to disk
- [ ] Disk failures start with empty state
- [ ] Scan interval respects rate limits
- [ ] Entry prices are tracked correctly
- [ ] Exit positions close when needed

---

## 7. Phase-by-Phase Implementation

### 7.1 Phase 1: Add New Components (Week 1)

**Goal:** Create all new components in `components/` directory

**Tasks:**
1. [ ] Create `StateManager` with Redis persistence
   - Add disk fallback
   - Implement sequence management
   - Implement rate limit locking
2. [ ] Create `APIRateLimiter` with retry logic
   - Handle 429, 500, 503, 504, timeouts
   - Implement exponential backoff with jitter
   - Track request metrics
3. [ ] Create `SignalValidator` with scoring
   - Implement trade size validation
   - Implement price validation
   - Implement confidence scoring
4. [ ] Create `MarketDataBridge` for instrument mapping
   - Cache condition_id → token_id mappings
   - Cache token_id → instrument mappings
5. [ ] Update `components/__init__.py`

**Deliverables:**
- `/components/state_manager.py`
- `/components/api_rate_limiter.py`
- `/components/signal_validator.py`
- `/components/market_data_bridge.py`
- `/components/__init__.py`

### 7.2 Phase 2: Update Existing Code (Week 2)

**Goal:** Integrate new components into existing codebase

**Tasks:**
1. [ ] Update `strategies/whale_tracker.py`
   - Add `StateManager` initialization
   - Add `APIRateLimiter` initialization
   - Add `SignalValidator` initialization
   - Add `MarketDataBridge` initialization
   - Modify `scan_whale_trades_sync()` to use new components
   - Add state persistence methods
2. [ ] Update `strategies/whale_follower.py`
   - Update `on_start()` to initialize components
   - Update `_maybe_scan_whales()` to use validated signals
   - Add confidence threshold checking
3. [ ] Update `run_live.py`
   - Add Redis connection configuration
   - Add component initialization
   - Add shutdown hooks
4. [ ] Update `run_paper.py`
   - Add Redis connection configuration
   - Add component initialization
   - Add paper mode flags

**Deliverables:**
- Modified `/strategies/whale_tracker.py`
- Modified `/strategies/whale_follower.py`
- Modified `/run_live.py`
- Modified `/run_paper.py`

### 7.3 Phase 3: Testing & Refinement (Week 3)

**Goal:** Validate and tune the system

**Tasks:**
1. [ ] Unit tests for all new components
   - Test signal validation scoring
   - Test state persistence (Redis + disk)
   - Test rate limiting behavior
   - Test instrument mapping
2. [ ] Paper trading with reduced limits
   - Run with 10s scan interval
   - Run with 20 trades per page
   - Verify signals are validated before executing
3. [ ] Performance benchmarking
   - Measure API scan throughput
   - Measure Redis latency
   - Measure total scan loop latency
4. [ ] Edge case testing
   - Simulate API outages
   - Simulate Redis outages
   - Simulate rate limiting
   - Test graceful recovery

**Deliverables:**
- `/tests/test_signal_validator.py`
- `/tests/test_state_manager.py`
- `/tests/test_api_rate_limiter.py`
- `/tests/test_market_data_bridge.py`
- Performance report
- Edge case test results

### 7.4 Phase 4: Production Deployment (Week 4)

**Goal:** Deploy to production environment

**Tasks:**
1. [ ] Update production Redis instance
   - Configure Redis connection string in `.env`
   - Set up Redis persistence (RDB/AOF)
   - Configure memory limits
2. [ ] Migrate existing state (if any)
   - Export current state from old system
   - Import into new Redis
   - Verify state integrity
3. [ ] Monitor and tune parameters
   - Adjust scan interval based on API load
   - Tune confidence thresholds
   - Optimize rate limit backoff
4. [ ] Document runbooks and alerts
   - Create monitoring dashboard
   - Set up alerts for key metrics
   - Document recovery procedures

**Deliverables:**
- Production configuration
- Monitoring setup
- Runbook documentation
- Alert configuration

---

## 8. Configuration

### 8.1 Redis Setup

```bash
# Docker
docker run -d --name whale-state \
  -p 6379:6379 \
  -v $(pwd)/data:/data \
  redis:7-alpine

# Or standalone
redis-server /etc/redis.conf
```

### 8.2 Environment Variables

```bash
# Redis connection
REDIS_URL="redis://localhost:6379"

# API settings
POLYMARKET_DATA_API_TIMEOUT=10
POLYMARKET_DATA_API_LIMIT=100
POLYMARKET_DATA_API_MAX_RETRIES=5

# Strategy settings
WHALE_SCAN_INTERVAL=60
WHALE_MIN_CONFIDENCE=0.60
WHALE_MIN_TRADE_SIZE=5000
WHALE_MAX_TRADE_SIZE=200000

# Auto-trade
WHALE_AUTO_TRADE=true
```

---

## 9. Performance Targets

| Metric | Current | Target |
|--------|---------|--------|
| Time to find whale trade | ~24 hours (100k trades) | ~24 hours (optimized scan) |
| API calls per whale trade | ~1000 | ~100 |
| Memory usage | ~50MB | ~100MB (with Redis) |
| CPU usage | ~2% | ~5% |
| P99 scan latency | ~15s | ~5s |

---

## 10. Monitoring & Alerts

### 10.1 Key Metrics

- `whale_scan_rate`: Scans per second
- `whale_signal_count`: Signals per hour
- `whale_api_latency`: API response time
- `whale_redis_latency`: Redis latency
- `whale_state_size`: Number of seen trades

### 10.2 Alerts

- API rate limited for >10s
- Redis connection lost
- Signal validation error rate >5%
- Scan interval >2x configured

### 10.3 Logging

```python
self.log.info(f"Scanned {len(trades)} trades, found {len(whale_trades)} whale trades")
self.log.info(f"Signal validated: {signal.reason} (conf={signal.confidence:.0%})")
self.log.warning(f"API rate limited, retrying in {delay}s")
self.log.error(f"Signal validation failed: {e}")
```

---

## 11. Appendix A: API Reference

### 11.1 Polymarket Data API

- **Base URL:** `https://data-api.polymarket.com`
- **Trades Endpoint:** `/trades`
- **Parameters:**
  - `limit`: 1-100 (default: 100)
  - `offset`: 0-N (default: 0)
  - `conditionId`: Optional market filter
- **Response Fields:**
  - `proxyWallet`: On-chain proxy address
  - `conditionId`: Market condition ID
  - `side`: BUY/SELL
  - `size`: Number of shares
  - `price`: Price per share
  - `timestamp`: Unix timestamp
  - `outcome`: YES/NO
  - `title`: Market title

### 11.2 Redis Keys

- `whale_seen:{wallet}:{conditionId}:{timestamp_ms}`: Seen trade
- `whale_sequence:{wallet}:{conditionId}`: Sequence counter
- `whale_rate_limit`: Rate limit lock

---

## 12. Appendix B: Example Usage

```python
# Initialize components
from components.state_manager import StateManager
from components.api_rate_limiter import APIRateLimiter
from components.signal_validator import SignalValidator
from components.market_data_bridge import MarketDataBridge
from strategies.whale_tracker import WhaleIdentityRegistry

# Create registry
whale_registry = WhaleIdentityRegistry()

# Create state manager
state_manager = StateManager(redis_url="redis://localhost:6379")

# Create rate limiter
rate_limiter = APIRateLimiter()

# Create validator
validator = SignalValidator(
    min_confidence=0.60,
    min_trade_size=5000.0,
    max_trade_size=200000.0,
)

# Create bridge
bridge = MarketDataBridge()
bridge.load_instrument_mapping(
    condition_id="0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902",
    token_id="8441400852834915183759801017793514978104486628517653995211751018945988243154",
)

# Scan for whale trades
def scan_loop():
    for offset in range(0, 1000000, 100):  # Scan first 1M trades
        trades = rate_limiter.scan_trades(
            condition_ids=["0xcccb7e7613a087c132b69cbf3a02bece3fdcb824c1da54ae79acc8d4a562d902"],
            offset=offset,
        )
        if not trades:
            break
        
        # Filter for whale trades
        for trade_data in trades:
            whale = whale_registry.get_whale(trade_data["proxyWallet"])
            if whale:
                sequence = state_manager._get_sequence()
                timestamp_ms = int(time.time() * 1000)
                
                if state_manager.has_seen_trade(
                    whale.proxy_wallet,
                    trade_data["conditionId"],
                    timestamp_ms,
                    sequence,
                ):
                    continue
                
                state_manager.mark_seen_trade(
                    whale.proxy_wallet,
                    trade_data["conditionId"],
                    timestamp_ms,
                    sequence,
                )
                
                # Validate signal
                result = validator.validate_signal(
                    whale_trade=WhaleTrade(...),
                    current_market_price=0.5,
                )
                
                if result.is_valid:
                    print(f"Validated signal: {result.reason}")
        
        # Rate limiting
        time.sleep(0.1)
        offset += 100
```

---

## 13. Appendix C: Rollback Plan

If the new system causes issues, rollback to the original:

```python
# Original simple scan (from whale_tracker.py)
def scan_whale_trades_sync(self, condition_ids=None) -> list[WhaleSignal]:
    """Original sync scan without Redis."""
    # ... existing implementation ...
```

To restore:
1. Stop new components
2. Restore `.env` with original settings
3. Revert git changes
4. Restart strategy

---

## 14. Summary

### 14.1 Key Benefits

1. **Persistent state** — No data loss on restart
2. **Rate limiting** — Handles API throttling gracefully
3. **Signal validation** — Confirms signal quality before acting
4. **Efficient scanning** — Targets whale trades specifically
5. **Production-ready** — Enterprise-grade reliability

### 14.2 Trade-offs

1. **Redis dependency** — Adds external dependency (mitigated by disk fallback)
2. **Async complexity** — Requires async/await patterns
3. **Configuration overhead** — More config parameters to manage

### 14.3 Next Steps

1. Create new components in `components/` directory
2. Update `whale_tracker.py` to integrate new components
3. Update `whale_follower.py` to consume validated signals
4. Update `run_live.py` to connect to Redis
5. Create unit tests
6. Test in paper mode with reduced limits
7. Monitor and tune parameters
