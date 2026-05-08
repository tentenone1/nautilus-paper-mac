"""Blacklist manager for whale wallet addresses.

Maintains config/whale_blacklist.json - centralized blacklist management
for the Nautilus trading system.
"""

import json
import logging
import pathlib
from typing import Iterable, Set

LOGGER = logging.getLogger(__name__)

BLACKLIST_PATH = pathlib.Path("config/whale_blacklist.json")
BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_blacklist() -> Set[str]:
    """Load current blacklist from JSON file.

    Returns:
        Set of blacklisted wallet addresses (lowercase).
    """
    if not BLACKLIST_PATH.is_file():
        return set()
    try:
        data = json.loads(BLACKLIST_PATH.read_text())
        # Handle both list format and dict format with 'addresses' key
        if isinstance(data, dict):
            addresses = data.get("addresses", [])
        else:
            addresses = data
        return set(str(a).lower() for a in addresses)
    except Exception as exc:
        LOGGER.error("Failed to read blacklist: %s", exc)
        return set()


def save_blacklist(bl_set: Set[str]) -> None:
    """Save blacklist to JSON file.

    Args:
        bl_set: Set of wallet addresses to save.
    """
    BLACKLIST_PATH.write_text(json.dumps(sorted(bl_set), indent=2))
    LOGGER.info("Blacklist updated: %d addresses", len(bl_set))


def add_to_blacklist(addresses: Iterable[str]) -> int:
    """Add addresses to blacklist (idempotent merge).

    Args:
        addresses: Wallet addresses to add.

    Returns:
        Number of new addresses added.
    """
    bl = load_blacklist()
    new_addrs = {str(a).lower() for a in addresses if str(a).lower() not in bl}
    if new_addrs:
        bl.update(new_addrs)
        save_blacklist(bl)
        LOGGER.info("Added %d new addresses to blacklist", len(new_addrs))
    return len(new_addrs)


def remove_from_blacklist(addresses: Iterable[str]) -> int:
    """Remove addresses from blacklist.

    Args:
        addresses: Wallet addresses to remove.

    Returns:
        Number of addresses removed.
    """
    bl = load_blacklist()
    to_remove = {str(a).lower() for a in addresses}
    removed = len(bl.intersection(to_remove))
    if removed:
        bl.difference_update(to_remove)
        save_blacklist(bl)
        LOGGER.info("Removed %d addresses from blacklist", removed)
    return removed


if __name__ == "__main__":
    # CLI interface for manual blacklist operations
    import sys
    if len(sys.argv) < 2:
        print("Usage: blacklist_manager.py [add|remove|list] [addresses...]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "list":
        bl = load_blacklist()
        print(f"Current blacklist ({len(bl)} addresses):")
        for addr in sorted(bl):
            print(f"  {addr}")
    elif action == "add":
        if len(sys.argv) < 3:
            print("Error: No addresses provided")
            sys.exit(1)
        added = add_to_blacklist(sys.argv[2:])
        print(f"Added {added} addresses")
    elif action == "remove":
        if len(sys.argv) < 3:
            print("Error: No addresses provided")
            sys.exit(1)
        removed = remove_from_blacklist(sys.argv[2:])
        print(f"Removed {removed} addresses")
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)