"""Phase 1 Validation Layer - Event logging, snapshots, and replay."""

from .event_logger import (
    EventType,
    ValidationEvent,
    GENESIS_CHECKSUM,
    GZIP_SUFFIX,
    LOGS_DIR,
    log_event,
    get_events,
    rotate_previous_day_file,
    _compute_checksum,
    _get_last_checksum,
    _rotate_if_needed,
)

from .trade_context import (
    TradeContext,
    TradeContextEntry,
    get_trade_context,
)

from .db_router import (
    DatabaseRouter,
    get_db_router,
    get_db_path,
    get_current_mode,
    set_trade_mode,
)

__all__ = [
    # Event logger
    "EventType",
    "ValidationEvent",
    "GENESIS_CHECKSUM",
    "GZIP_SUFFIX",
    "LOGS_DIR",
    "log_event",
    "get_events",
    "rotate_previous_day_file",
    "_compute_checksum",
    "_get_last_checksum",
    "_rotate_if_needed",
    # Trade context
    "TradeContext",
    "TradeContextEntry",
    "get_trade_context",
    # DB router
    "DatabaseRouter",
    "get_db_router",
    "get_db_path",
    "get_current_mode",
    "set_trade_mode",
]