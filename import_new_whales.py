#!/usr/bin/env python3
"""
Import new whale addresses (v3 dual-axis classification) into whale_discovery.db.
Place in nautilus-trading root and run: python3 import_new_whales.py
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "pipeline" / "data" / "whale_discovery.db"
ADDR_FILE = BASE / "pipeline" / "data" / "new_whale_addresses.json"

with open(ADDR_FILE) as f:
    data = json.load(f)

all_addresses = data.get("all", [])
print(f"Loading {len(all_addresses)} addresses from v3 whale scan...")

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

existing = set()
for row in cur.execute("SELECT address FROM whales"):
    existing.add(row["address"])

print(f"Existing addresses in DB: {len(existing)}")

new_count = 0
for addr in all_addresses:
    if addr not in existing:
        cur.execute("""
            INSERT OR IGNORE INTO whales
                (address, name, alpha_score, pnl, volume, win_rate, total_trades, last_seen, tags, market_category, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            addr,
            f"v3-{addr[:10]}",
            50.0, 0.0, 0.0, 0.0, 0,
            datetime.now().isoformat(),
            '["v3_scan"]', "unknown", "v3_scan"
        ))
        new_count += 1

conn.commit()
print(f"New addresses inserted: {new_count}")
print(f"Total addresses now: {len(existing) + new_count}")

by_tier = data.get("by_capital_tier", {})
for tier, count in by_tier.items():
    print(f"  {tier}: {count}")

conn.close()
