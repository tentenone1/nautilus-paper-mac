"""Persistent state management for whale tracking using Redis."""

from dataclasses import dataclass
from typing import Optional
import redis
import json
import time
import os

# Try to import redis from venv, fallback to system
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    try:
        from redis import Redis
        REDIS_AVAILABLE = True
    except ImportError:
        REDIS_AVAILABLE = False


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
    """Persistent state management using Redis.
    
    StateManager provides:
    - Deduplication tracking (seen trades)
    - Sequence number management
    - Rate limiting locks
    - Persistent state across restarts
    - Graceful fallback to in-memory if Redis unavailable
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        db: int = 0,
        fallback_memory: bool = True,
        fallback_dir: Optional[str] = None,
    ):
        """Initialize state manager.
        
        Args:
            redis_url: Redis connection URL (default: localhost:6379)
            db: Redis database number (default: 0)
            fallback_memory: If True, also maintain in-memory state
            fallback_dir: Directory for disk backups (optional)
        """
        self._redis_available = REDIS_AVAILABLE
        self._redis_url = redis_url
        self._db = db
        self._fallback_memory = fallback_memory
        self._fallback_dir = fallback_dir or os.path.join(os.getcwd(), "data")
        
        # Initialize Redis connection (lazy load)
        self._redis: Optional[redis.Redis] = None
        
        # In-memory fallback state
        self._seen_trades: set = set()
        self._sequence: int = 0
        self._last_scan: float = 0.0
        self._rate_limit_lock: Optional[redis.Lock] = None
        
        # Load persisted state if available
        self._load_state()
    
    @property
    def redis(self) -> redis.Redis:
        """Get Redis connection."""
        if self._redis is None:
            if self._redis_available:
                try:
                    self._redis = redis.Redis.from_url(
                        self._redis_url,
                        db=self._db,
                        decode_responses=True,
                    )
                    self._redis.ping()
                except Exception as e:
                    print(f"Redis connection failed: {e}, using fallback")
                    self._redis = None
            else:
                # Use in-memory fallback
                self._redis = None
        return self._redis
    
    def _load_state(self) -> None:
        """Load persisted state from Redis or disk."""
        if self._redis and self._redis_available:
            try:
                self._sequence = int(self._redis.get("whale_sequence") or 0)
                self._last_scan = float(self._redis.get("whale_last_scan") or 0.0)
                # Load seen trades
                seen_keys = self._redis.keys("whale_seen:*")
                if seen_keys:
                    for key in seen_keys:
                        self._seen_trades.add(key)
            except Exception as e:
                print(f"Failed to load state from Redis: {e}")
        elif self._fallback_memory:
            # Load from disk if available
            self._load_from_disk()
    
    def _load_from_disk(self) -> None:
        """Load state from disk backup."""
        try:
            import os
            state_file = os.path.join(self._fallback_dir, "whale_state.json")
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    data = json.load(f)
                    self._sequence = data.get("sequence", 0)
                    self._last_scan = data.get("last_scan", 0.0)
                    self._seen_trades = set(data.get("seen_trades", []))
        except Exception as e:
            print(f"Failed to load state from disk: {e}")
    
    def _save_to_disk(self) -> None:
        """Save state to disk backup."""
        try:
            import os
            os.makedirs(self._fallback_dir, exist_ok=True)
            state_file = os.path.join(self._fallback_dir, "whale_state.json")
            data = {
                "sequence": self._sequence,
                "last_scan": self._last_scan,
                "seen_trades": list(self._seen_trades),
            }
            with open(state_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to save state to disk: {e}")
    
    def get_sequence(self) -> int:
        """Get and increment global sequence counter."""
        if self._redis and self._redis_available:
            self._sequence = self._redis.incr("whale_sequence")
            return self._sequence
        
        # Fallback: increment in-memory
        self._sequence = (self._sequence or 0) + 1
        return self._sequence
    
    def has_seen_trade(
        self,
        wallet: str,
        condition_id: str,
        timestamp_ms: int,
        sequence: int,
    ) -> bool:
        """Check if trade has been seen before."""
        trade_key = SeenTradeKey(wallet, condition_id, timestamp_ms, sequence).cache_key
        if self._redis and self._redis_available:
            return self._redis.exists(trade_key)
        return trade_key in self._seen_trades
    
    def mark_seen_trade(
        self,
        wallet: str,
        condition_id: str,
        timestamp_ms: int,
        sequence: int,
    ) -> None:
        """Mark trade as seen."""
        trade_key = SeenTradeKey(wallet, condition_id, timestamp_ms, sequence).cache_key
        if self._redis and self._redis_available:
            self._redis.setex(trade_key, 86400 * 365, "1")  # 1 year expiry
        
        # Also update in-memory
        self._seen_trades.add(trade_key)
    
    def get_last_scan_time(self) -> float:
        """Get last scan time."""
        if self._redis and self._redis_available:
            return float(self._redis.get("whale_last_scan") or 0.0)
        return self._last_scan
    
    def set_last_scan_time(self, timestamp: float) -> None:
        """Set last scan time."""
        if self._redis and self._redis_available:
            self._redis.set("whale_last_scan", timestamp)
        self._last_scan = timestamp
    
    def get_rate_limit_lock(self, timeout: int = 10) -> bool:
        """Acquire rate limit lock."""
        if self._redis and self._redis_available:
            if self._rate_limit_lock is None:
                self._rate_limit_lock = self._redis.lock("whale_rate_limit", timeout=timeout)
            return self._rate_limit_lock.acquire(blocking=True, timeout=timeout)
        return True  # In-memory: no locking
    
    def release_rate_limit_lock(self) -> None:
        """Release rate limit lock."""
        if self._rate_limit_lock:
            try:
                self._rate_limit_lock.release()
            except Exception:
                pass
    
    def reset_sequence(self, sequence: int = 0) -> None:
        """Reset sequence counter."""
        if self._redis and self._redis_available:
            self._redis.set("whale_sequence", sequence)
        self._sequence = sequence
    
    def cleanup_old_trades(self, max_age_days: int = 365) -> None:
        """Clean up old trade entries."""
        if self._redis and self._redis_available:
            try:
                # Find and delete old entries (older than max_age_days)
                now = time.time()
                cutoff = now - max_age_days * 86400
                pattern = "whale_seen:*"
                keys = self._redis.keys(pattern)
                
                for key in keys:
                    try:
                        value = self._redis.get(key)
                        # Value format: "timestamp_ms" (as string)
                        timestamp_ms = int(value) if value else 0
                        if timestamp_ms and timestamp_ms < cutoff:
                            self._redis.delete(key)
                            self._seen_trades.discard(key)
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                print(f"Failed to cleanup old trades: {e}")
    
    def get_summary(self) -> dict:
        """Get state summary."""
        return {
            "redis_available": self._redis_available,
            "sequence": self._sequence,
            "last_scan": self._last_scan,
            "seen_trades_count": len(self._seen_trades),
            "redis_keys_count": self._redis.dbsize() if self._redis else 0,
        }
    
    def __del__(self):
        """Cleanup on deletion."""
        if self._rate_limit_lock:
            try:
                self._rate_limit_lock.release()
            except Exception:
                pass
        if self._redis and self._redis_available:
            try:
                self._redis.close()
            except Exception:
                pass
    
    def save_state(self) -> None:
        """Force save state to Redis and disk."""
        if self._redis and self._redis_available:
            self._redis.set("whale_sequence", self._sequence)
            self._redis.set("whale_last_scan", self._last_scan)
        
        if self._fallback_memory and self._fallback_dir:
            self._save_to_disk()
