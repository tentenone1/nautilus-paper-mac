"""
Phase 1 validation layer - Append-only JSONL event logger with SHA256 checksum chain.

This module provides an immutable audit log for all trading events, ensuring
data integrity through cryptographic checksums and supporting thread-safe
concurrent access.
"""

import gzip
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# Named constants
GENESIS_CHECKSUM: str = "0" * 64
GZIP_SUFFIX: str = ".gz"
LOGS_DIR: Path = Path("logs") / "events"
DATE_FORMAT: str = "%Y-%m-%d"
ISO_FORMAT: str = "%Y-%m-%dT%H:%M:%S.%fZ"


class EventType(Enum):
    """Enumeration of all valid event types for the validation logger."""
    
    WHALE_TRADE_DETECTED = "WHALE_TRADE_DETECTED"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    TRADE_SUBMITTED = "TRADE_SUBMITTED"
    TRADE_FILLED = "TRADE_FILLED"
    TRADE_PARTIAL_FILL = "TRADE_PARTIAL_FILL"
    TRADE_CLOSED = "TRADE_CLOSED"
    MARKET_RESOLVED = "MARKET_RESOLVED"
    API_ERROR = "API_ERROR"
    RETRY_ATTEMPT = "RETRY_ATTEMPT"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"


@dataclass(frozen=True)
class ValidationEvent:
    """Immutable event record for the validation audit log.
    
    Attributes:
        event_id: Unique UUID identifier for this event.
        event_type: Type of event from EventType enum.
        ts_wall: Wall-clock timestamp in ISO 8601 UTC format.
        ts_mono_ns: Monotonic nanosecond timestamp for precise ordering.
        run_id: Identifier for the current execution run.
        mode: Execution mode (paper, live, or replay).
        strategy_id: Identifier for the strategy that generated this event.
        correlation_id: Optional ID to correlate related events.
        payload: Event-specific data dictionary.
        prev_checksum: SHA256 checksum of the previous event in the chain.
        checksum: SHA256 checksum of this event (computed from all fields).
    """
    
    event_id: str
    event_type: str
    ts_wall: str
    ts_mono_ns: int
    run_id: str
    mode: str
    strategy_id: str
    correlation_id: Optional[str]
    payload: Dict[str, Any]
    prev_checksum: str
    checksum: str


# Thread-safe lock for file operations
_file_lock: threading.Lock = threading.Lock()


def _get_log_path(target_date: Optional[date] = None) -> Path:
    """Get the log file path for a specific date.
    
    Args:
        target_date: Date for the log file. Defaults to today (UTC).
        
    Returns:
        Path object pointing to the log file.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    filename = f"{target_date.strftime(DATE_FORMAT)}.validation.jsonl"
    return LOGS_DIR / filename


def _ensure_log_dir() -> None:
    """Ensure the log directory exists."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _compute_checksum(event_dict: Dict[str, Any]) -> str:
    """Compute SHA256 checksum of an event dictionary.
    
    The checksum is computed over the JSON serialization of the event,
    excluding the checksum field itself.
    
    Args:
        event_dict: Dictionary representation of the event.
        
    Returns:
        Hexadecimal string of the SHA256 hash.
    """
    # Create a copy without the checksum field for hash computation
    hash_dict = {k: v for k, v in event_dict.items() if k != "checksum"}
    
    # Sort keys for deterministic serialization
    json_str = json.dumps(hash_dict, sort_keys=True, separators=(",", ":"))
    
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


def _get_last_checksum(target_date: Optional[date] = None) -> str:
    """Get the checksum of the last event in the chain.
    
    Args:
        target_date: Date to check. Defaults to today (UTC).
        
    Returns:
        SHA256 checksum of the last event, or GENESIS_CHECKSUM if no events exist.
    """
    log_path = _get_log_path(target_date)
    
    if not log_path.exists():
        return GENESIS_CHECKSUM
    
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        if not lines:
            return GENESIS_CHECKSUM
        
        # Get the last non-empty line
        last_line = lines[-1].strip()
        if not last_line and len(lines) > 1:
            last_line = lines[-2].strip()
        
        if not last_line:
            return GENESIS_CHECKSUM
        
        last_event = json.loads(last_line)
        return last_event.get("checksum", GENESIS_CHECKSUM)
    except (json.JSONDecodeError, IOError, OSError):
        return GENESIS_CHECKSUM


def _rotate_if_needed() -> None:
    """Rotate log files at midnight UTC.
    
    Checks if there are log files from previous days and if so,
    compresses them with gzip. This maintains a single uncompressed file
    for the current day.
    """
    today = datetime.now(timezone.utc).date()
    _rotate_logs_for_date(today)


def rotate_previous_day_file(now_utc: Optional[date] = None) -> Optional[Path]:
    """Compress yesterday's log file (public wrapper for testing).
    
    Args:
        now_utc: Current date for rotation check. Defaults to today UTC.
        
    Returns:
        Path to compressed file if rotation occurred, None otherwise.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc).date()
    return _rotate_logs_for_date(now_utc)


def _rotate_logs_for_date(today: date) -> Optional[Path]:
    
    # Ensure directory exists before checking
    if not LOGS_DIR.exists():
        return None
    
    for log_file in LOGS_DIR.glob("*.validation.jsonl"):
        # Skip if it's a .gz file (shouldn't match, but just in case)
        if ".gz" in log_file.name:
            continue
        
        # Extract date from filename (YYYY-MM-DD.validation.jsonl)
        try:
            # stem is YYYY-MM-DD.validation
            file_date_str = log_file.stem.replace(".validation", "")
            file_date = datetime.strptime(file_date_str, DATE_FORMAT).date()
        except (ValueError, AttributeError):
            continue
        
        # If the file is from a previous day, compress it
        if file_date < today:
            compressed_path = Path(str(log_file) + ".gz")
            if compressed_path.exists():
                # Already compressed, remove the uncompressed file
                log_file.unlink()
                continue
            
            # Compress the file
            with open(log_file, "rb") as f_in:
                with gzip.open(compressed_path, "wb") as f_out:
                    f_out.writelines(f_in)
            
            # Remove the original file after successful compression
            log_file.unlink()
            return compressed_path
    
    return None


def log_event(
    event_type: EventType,
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
    mode: str = "paper",
    strategy_id: str = "whale_follower",
    run_id: Optional[str] = None,
) -> str:
    """Log a validation event to the append-only JSONL file.
    
    Creates a new ValidationEvent with a SHA256 checksum that chains to
    the previous event, ensuring tamper-evident audit logging.
    
    Args:
        event_type: Type of event from EventType enum.
        payload: Event-specific data dictionary.
        correlation_id: Optional ID to correlate related events.
        mode: Execution mode (paper, live, or replay). Defaults to "paper".
        strategy_id: Strategy identifier. Defaults to "whale_follower".
        run_id: Run identifier. Defaults to a new UUID if not provided.
        
    Returns:
        The event_id (UUID string) of the logged event.
        
    Raises:
        ValueError: If event_type is not a valid EventType.
    """
    if not isinstance(event_type, EventType):
        raise ValueError(f"Invalid event_type: {event_type}")
    
    with _file_lock:
        # Check for log rotation
        _rotate_if_needed()
        
        # Ensure log directory exists
        _ensure_log_dir()
        
        # Generate event data
        event_id = str(uuid.uuid4())
        ts_wall = datetime.now(timezone.utc).strftime(ISO_FORMAT)
        ts_mono_ns = time.monotonic_ns()
        
        if run_id is None:
            run_id = str(uuid.uuid4())
        
        # Get previous checksum for chain integrity
        prev_checksum = _get_last_checksum()
        
        # Create event dictionary (without checksum first)
        event_dict: Dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type.value,
            "ts_wall": ts_wall,
            "ts_mono_ns": ts_mono_ns,
            "run_id": run_id,
            "mode": mode,
            "strategy_id": strategy_id,
            "correlation_id": correlation_id,
            "payload": payload,
            "prev_checksum": prev_checksum,
        }
        
        # Compute and add checksum
        checksum = _compute_checksum(event_dict)
        event_dict["checksum"] = checksum
        
        # Write to file
        log_path = _get_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_dict, separators=(",", ":")) + "\n")
        
        return event_id


def get_events(
    target_date: Optional[date] = None,
    event_type: Optional[EventType] = None,
) -> List[ValidationEvent]:
    """Retrieve events from the log file.
    
    Reads events from the specified date's log file, optionally filtering
    by event type. Supports reading from both uncompressed and gzip-compressed
    files.
    
    Args:
        target_date: Date to retrieve events from. Defaults to today (UTC).
        event_type: Optional event type to filter by.
        
    Returns:
        List of ValidationEvent objects matching the criteria.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()
    
    log_path = _get_log_path(target_date)
    compressed_path = Path(str(log_path) + ".gz")
    
    events: List[ValidationEvent] = []
    
    with _file_lock:
        # Prefer uncompressed file if it exists (might have newer data)
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event_dict = json.loads(line)
                        if event_type is None or event_dict.get("event_type") == event_type.value:
                            events.append(ValidationEvent(**event_dict))
                    except json.JSONDecodeError:
                        continue
        elif compressed_path.exists():
            with gzip.open(compressed_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event_dict = json.loads(line)
                        if event_type is None or event_dict.get("event_type") == event_type.value:
                            events.append(ValidationEvent(**event_dict))
                    except json.JSONDecodeError:
                        continue
    
    return events
