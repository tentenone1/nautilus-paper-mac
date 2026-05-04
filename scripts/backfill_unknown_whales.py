#!/usr/bin/env python3
"""One-time backfill: Attempt to resolve existing 'unknown' whale trades.

Strategy:
1. Trades with whale_name='unknown' and whale_address NOT empty → fill in fallback name
2. Trades with whale_name='unknown' and empty whale_address → log count for manual review
   (these have no wallet address to fall back on; likely from recovery path)
"""

import sqlite3
import os
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "research" / "trades.db"


def make_fallback_name(wallet_addr: str) -> str:
    if wallet_addr and len(wallet_addr) >= 6:
        short = wallet_addr[:6].lower()
        return f"whale_0x{short}"
    return None


def backfill():
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    
    # Case 1: whale_name='unknown' but whale_address IS populated → backfill with fallback
    rows = conn.execute(
        "SELECT rowid, whale_address FROM trades "
        "WHERE (whale_name IS NULL OR whale_name IN ('', 'unknown', 'Unknown Whale')) "
        "AND (whale_address IS NOT NULL AND whale_address != '')"
    ).fetchall()
    
    updated_case1 = 0
    for rowid, addr in rows:
        fallback = make_fallback_name(addr)
        if fallback:
            conn.execute(
                "UPDATE trades SET whale_name=? WHERE rowid=?",
                (fallback, rowid)
            )
            updated_case1 += 1
    
    # Case 2: whale_name='unknown' AND whale_address empty → these can't be auto-resolved
    count_case2 = conn.execute(
        "SELECT COUNT(*) FROM trades "
        "WHERE (whale_name IS NULL OR whale_name IN ('', 'unknown', 'Unknown Whale')) "
        "AND (whale_address IS NULL OR whale_address = '')"
    ).fetchone()[0]
    
    # Case 3: Any trades with 'Unknown Whale' (capitalized) - normalize if no address
    count_case3 = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE whale_name = 'Unknown Whale'"
    ).fetchone()[0]
    if count_case3 > 0:
        conn.execute(
            "UPDATE trades SET whale_name = 'unknown' WHERE whale_name = 'Unknown Whale'"
        )
        print(f"  Normalized {count_case3} 'Unknown Whale' entries to 'unknown'")
    
    conn.commit()
    conn.close()
    
    print(f"Backfill complete:")
    print(f"  Case 1: Backfilled {updated_case1} trades with address-based names")
    print(f"  Case 2: {count_case2} trades remain 'unknown' (no wallet address to resolve)")
    
    if updated_case1 > 0 or count_case2 > 0 or count_case3 > 0:
        print(f"  Total affected: {updated_case1 + count_case2 + count_case3}")
    else:
        print(f"  No trades needed backfill (system already clean)")


if __name__ == "__main__":
    backfill()
