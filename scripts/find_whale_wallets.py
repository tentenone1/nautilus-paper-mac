#!/usr/bin/env python3
"""Find wallet addresses for known whales by examining their active markets.

For each whale, queries the Polymarket trades API on their condition_ids
to find who traded those markets recently. Extracts proxyWallet addresses.
"""

import json
import urllib.request
import time
import os

# Resolve relative to the script's location (works on both Mac and 1700)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NAUTILUS_ROOT = os.path.dirname(SCRIPT_DIR)  # goes up from scripts/ to nautilus-trading/
DB_PATH = os.path.join(NAUTILUS_ROOT, "research", "trades.db")
OUTPUT_PATH = os.path.join(NAUTILUS_ROOT, "config", "known_whale_wallets.json")

# Known wallets we already have
KNOWN = {
    "pilotbaby": "0x6815040a7176c958e6ff8818bfe188e80dbd9edb",
    "Herdonia": "0xd106952ebf30a3125affd8a23b6c1f30c35fc79c",
}

TARGETS = ["mooseborzoi", "beetlepimp", "surfandturf", "loitterer", "Deep7", "RJW1"]

API_TRADES = "https://data-api.polymarket.com/trades?conditionId={}&limit=20"
API_MARKETS = "https://data-api.polymarket.com/markets?conditionId={}&limit=1"


def get_condition_ids(whale_name: str) -> list[str]:
    """Get condition_ids for a whale from backup DB."""
    import sqlite3
    try:
        db = sqlite3.connect(DB_PATH)
        rows = db.execute(
            "SELECT DISTINCT condition_id FROM trades WHERE whale_name = ? AND condition_id IS NOT NULL AND condition_id != ''",
            (whale_name,)
        ).fetchall()
        db.close()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"  DB error: {e}")
        return []


def fetch_json(url: str) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


def find_wallet_from_market(condition_id: str, expected_name: str) -> dict | None:
    """Scan trades on a specific market and look for any trader that matches patterns."""
    url = API_TRADES.format(condition_id)
    data = fetch_json(url)
    if not data:
        return None
    
    for t in data:
        wallet = t.get("proxyWallet", "")
        name = t.get("name", "") or ""
        pseudonym = t.get("pseudonym", "") or ""
        price = t.get("price", 0)
        title = t.get("title", "") or ""
        
        # Check if this trader might be our whale
        # Some whales have wallet-based names like "0x...-12345" 
        # If the name contains recognizable patterns, flag it
        if name or pseudonym:
            return {
                "wallet": wallet,
                "name": name,
                "pseudonym": pseudonym,
                "price": price,
                "market": title,
                "condition_id": condition_id,
            }
    
    return None


def main():
    found_wallets = dict(KNOWN)
    
    for whale in TARGETS:
        print(f"\n--- {whale} ---", flush=True)
        cids = get_condition_ids(whale)
        print(f"  Markets: {len(cids)} condition_ids", flush=True)
        
        for cid in cids[:3]:  # Check first 3 markets
            # Check if market still exists
            market_data = fetch_json(API_MARKETS.format(cid))
            if market_data and len(market_data) > 0:
                m = market_data[0]
                title = m.get("title", "?")
                print(f"  Market: {title[:60]}", flush=True)
                
                # Check recent trades
                trades = fetch_json(API_TRADES.format(cid))
                if trades and len(trades) > 0:
                    print(f"  Recent traders: {len(trades)} trades", flush=True)
                    for t in trades[:5]:
                        w = t.get("proxyWallet", "")[:20]
                        n = (t.get("name", "") or "")[:30]
                        p = t.get("price", 0)
                        ps = (t.get("pseudonym", "") or "")[:20]
                        print(f"    wallet={w} name={n} pseudonym={ps} @${p}", flush=True)
                        
                        # Check if this wallet looks like our whale
                        # (wallet-based names: "0x...-12345" pattern)
                        for known_whale, known_wallet in KNOWN.items():
                            if known_wallet.lower() in w.lower():
                                print(f"    *** MATCH: {known_whale}", flush=True)
                        
                        # If name matches known pattern
                        name_lower = n.lower()
                        if any(term in name_lower for term in ["moose", "beetle", "surf", "loit", "deep", "rjw"]):
                            print(f"    *** CANDIDATE: name contains known term", flush=True)
                            if whale not in found_wallets:
                                found_wallets[whale] = w
                else:
                    print(f"  No recent trades", flush=True)
            else:
                print(f"  Market not found on API", flush=True)
            time.sleep(0.3)
    
    print(f"\n{'='*60}")
    print("FOUND WALLETS:")
    print(f"{'='*60}")
    for w, addr in sorted(found_wallets.items()):
        print(f"  {w:20s} → {addr}")
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(found_wallets, f, indent=2)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
