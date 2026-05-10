"""Phase 1 validation layer - Signal snapshot store.

Freezes decision inputs at signal generation time for replay validation.
"""

import gzip
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# Named constants - Use project-relative paths (works on Mac and Linux)
SNAPSHOTS_DIR: Path = Path(__file__).parent.parent.parent / "snapshots"
DATE_FORMAT: str = "%Y-%m-%d"
SNAPSHOT_SUFFIX: str = ".json.gz"


@dataclass(frozen=True)
class SignalSnapshot:
    """Immutable snapshot of decision inputs at signal generation.
    
    Captures market state, orderbook, whale metrics, and classification
    at the exact moment a trading signal is generated.
    """
    snapshot_id: str  # UUID
    timestamp: str  # ISO UTC
    ts_mono_ns: int  # monotonic_ns for correlation
    signal_id: str  # Links to signal event
    market_state: Dict[str, Any]  # price, liquidity, volume, bid, ask
    orderbook: Dict[str, Any]  # top 5 bids/asks
    whale_metrics: Dict[str, Any]  # whale position, confidence, edge_score, classification
    classification: str  # skilled_human, sacrificial_account, etc.
    confidence: float  # Signal confidence (0-1)
    market_regime: str  # trending, neutral, volatile
    strategy_version: str  # Version identifier
    checksum: str  # SHA256 of content


# Thread-safe lock for file operations
_snapshot_lock: threading.Lock = threading.Lock()


def _get_snapshot_dir(target_date: Optional[datetime] = None) -> Path:
    """Get snapshot directory for a specific date.
    
    Args:
        target_date: Date for snapshot directory. Defaults to today UTC.
        
    Returns:
        Path to snapshot directory.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc)
    return SNAPSHOTS_DIR / target_date.strftime(DATE_FORMAT)


def _ensure_snapshot_dir(target_date: Optional[datetime] = None) -> None:
    """Ensure snapshot directory exists.
    
    Args:
        target_date: Date for snapshot directory. Defaults to today UTC.
    """
    snapshot_dir = _get_snapshot_dir(target_date)
    snapshot_dir.mkdir(parents=True, exist_ok=True)


def _compute_snapshot_checksum(snapshot_dict: Dict[str, Any]) -> str:
    """Compute SHA256 checksum of snapshot dictionary.
    
    Excludes checksum field from hash computation.
    
    Args:
        snapshot_dict: Dictionary representation of snapshot.
        
    Returns:
        Hexadecimal SHA256 hash string.
    """
    hash_dict = {k: v for k, v in snapshot_dict.items() if k != "checksum"}
    json_str = json.dumps(hash_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


def freeze_snapshot(
    signal_id: str,
    market_state: Dict[str, Any],
    orderbook: Dict[str, Any],
    whale_metrics: Dict[str, Any],
    classification: str,
    confidence: float,
    market_regime: str = "neutral",
    strategy_version: str = "v1.0",
) -> SignalSnapshot:
    """Create and store a frozen snapshot at signal generation time.
    
    Args:
        signal_id: Signal identifier from event logger.
        market_state: Market data dict (price, liquidity, volume, bid, ask).
        orderbook: Orderbook dict (top 5 bids/asks).
        whale_metrics: Whale data dict (position, confidence, edge_score, classification).
        classification: Whale classification string.
        confidence: Signal confidence (0-1).
        market_regime: Market regime classification. Defaults to "neutral".
        strategy_version: Strategy version string. Defaults to "v1.0".
        
    Returns:
        SignalSnapshot with frozen data and checksum.
    """
    with _snapshot_lock:
        snapshot_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        ts_mono_ns = time.monotonic_ns()
        
        snapshot_dict: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "timestamp": timestamp,
            "ts_mono_ns": ts_mono_ns,
            "signal_id": signal_id,
            "market_state": market_state,
            "orderbook": orderbook,
            "whale_metrics": whale_metrics,
            "classification": classification,
            "confidence": confidence,
            "market_regime": market_regime,
            "strategy_version": strategy_version,
        }
        
        # Compute checksum
        checksum = _compute_snapshot_checksum(snapshot_dict)
        snapshot_dict["checksum"] = checksum
        
        # Create frozen dataclass
        snapshot = SignalSnapshot(**snapshot_dict)
        
        # Write to compressed file
        _ensure_snapshot_dir()
        snapshot_dir = _get_snapshot_dir()
        snapshot_path = snapshot_dir / f"{snapshot_id}{SNAPSHOT_SUFFIX}"
        
        with gzip.open(snapshot_path, "wt", encoding="utf-8") as f:
            json.dump(asdict(snapshot), f, separators=(",", ":"))
        
        return snapshot


def load_snapshot(snapshot_id: str, target_date: Optional[datetime] = None) -> Optional[SignalSnapshot]:
    """Load a snapshot from storage.
    
    Args:
        snapshot_id: Snapshot UUID.
        target_date: Date to search. Defaults to today UTC.
        
    Returns:
        SignalSnapshot if found, None otherwise.
    """
    with _snapshot_lock:
        snapshot_dir = _get_snapshot_dir(target_date)
        snapshot_path = snapshot_dir / f"{snapshot_id}{SNAPSHOT_SUFFIX}"
        
        if not snapshot_path.exists():
            return None
        
        try:
            with gzip.open(snapshot_path, "rt", encoding="utf-8") as f:
                snapshot_dict = json.load(f)
            return SignalSnapshot(**snapshot_dict)
        except (json.JSONDecodeError, IOError, OSError):
            return None


def verify_snapshot(snapshot: SignalSnapshot) -> bool:
    """Verify snapshot checksum integrity.
    
    Args:
        snapshot: SignalSnapshot to verify.
        
    Returns:
        True if checksum matches, False otherwise.
    """
    snapshot_dict = asdict(snapshot)
    expected_checksum = _compute_snapshot_checksum(snapshot_dict)
    return snapshot.checksum == expected_checksum


def list_snapshots(target_date: Optional[datetime] = None) -> list:
    """List all snapshot IDs for a date.
    
    Args:
        target_date: Date to list. Defaults to today UTC.
        
    Returns:
        List of snapshot_id strings.
    """
    with _snapshot_lock:
        snapshot_dir = _get_snapshot_dir(target_date)
        
        if not snapshot_dir.exists():
            return []
        
        snapshot_ids = []
        for snapshot_file in snapshot_dir.glob(f"*{SNAPSHOT_SUFFIX}"):
            # Extract snapshot_id from filename (UUID.json.gz)
            snapshot_id = snapshot_file.stem.replace(".json", "")
            snapshot_ids.append(snapshot_id)
        
        return snapshot_ids


def get_snapshot_path(snapshot_id: str, target_date: Optional[datetime] = None) -> Optional[Path]:
    """Get file path for a snapshot.
    
    Args:
        snapshot_id: Snapshot UUID.
        target_date: Date to search. Defaults to today UTC.
        
    Returns:
        Path if exists, None otherwise.
    """
    snapshot_dir = _get_snapshot_dir(target_date)
    snapshot_path = snapshot_dir / f"{snapshot_id}{SNAPSHOT_SUFFIX}"
    return snapshot_path if snapshot_path.exists() else None