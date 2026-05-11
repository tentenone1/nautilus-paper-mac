"""
Open position persistence.

Saves open positions to a JSON file so they survive restarts.
Format: {instrument_id_str: {size, entry_price, side, market_title, trade_id, ...}}
"""

import json
import os
from pathlib import Path

POSITIONS_FILE = Path(__file__).parent.parent / "open_positions.json"


def save_open_positions(open_positions: dict) -> None:
    """Persist open positions to JSON file."""
    # Convert any non-serializable values
    serializable = {}
    for inst_id, info in open_positions.items():
        serializable[str(inst_id)] = {k: v for k, v in info.items() if k != "_pending"}
    
    with open(POSITIONS_FILE, "w") as f:
        json.dump(serializable, f, indent=2, default=str)


def load_open_positions() -> dict:
    """Load open positions from JSON file. Returns {} if no file exists."""
    if not POSITIONS_FILE.exists():
        return {}
    
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, IOError):
        return {}


def clear_open_positions() -> None:
    """Clear persisted positions (called after full resolution)."""
    if POSITIONS_FILE.exists():
        os.remove(POSITIONS_FILE)