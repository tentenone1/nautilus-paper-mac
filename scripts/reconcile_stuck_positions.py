#!/usr/bin/env python3
"""Reconcile stuck open positions in trades.db.

This script scans all open positions (exit_price IS NULL) in trades.db,
checks their current market status via Polymarket CLOB API, and either:
  1. Records resolution exits if the market has resolved
  2. Records stale_max_hold exits if the position exceeds max_hold_hours
  3. Leaves positions alone if they're still valid and recent

Usage:
    cd ~/workspace/nautilus-trading
    venv/bin/python scripts/reconcile_stuck_positions.py [--dry-run]
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "research" / "trades.db"
MAX_HOLD_HOURS = 4.0  # Match config
DRY_RUN = False


def fetch_resolution(condition_id: str) -> dict | None:
    """Fetch market resolution from CLOB API."""
    try:
        url = f"https://clob.polymarket.com/markets/{condition_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        
        tokens = data.get("tokens", [])
        winners = [t for t in tokens if t.get("winner") is True]
        losers = [t for t in tokens if t.get("winner") is False]
        
        resolved = len(winners) == 1 and len(losers) >= 1
        return {
            "resolved": resolved,
            "winning_token_id": winners[0].get("token_id", "") if winners else None,
            "question": data.get("question", ""),
            "closed": data.get("closed", False),
        }
    except Exception as e:
        return {"error": str(e)}


def fetch_midpoint(token_id: str) -> float | None:
    """Fetch current midpoint price from CLOB API."""
    try:
        url = f"https://clob.polymarket.com/midpoint?token_id={token_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        price_str = data.get("midpoint") or data.get("price")
        if price_str is not None:
            return float(price_str)
    except Exception:
        pass
    return None


def extract_token_id(instrument_id: str) -> str:
    """Extract token_id from instrument_id like 'condition_id-token_id.POLYMARKET'."""
    clean = instrument_id.replace(".POLYMARKET", "")
    parts = clean.split("-")
    return parts[-1] if len(parts) >= 2 else ""


def extract_condition_id(instrument_id: str) -> str:
    """Extract condition_id from instrument_id."""
    clean = instrument_id.replace(".POLYMARKET", "")
    parts = clean.split("-")
    return parts[0] if parts else instrument_id


def reconcile():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    
    # Fetch all open positions
    rows = conn.execute("""
        SELECT trade_id, condition_id, instrument_id, whale_name, market_title,
               side, entry_price, position_size_usd, timestamp, category
        FROM trades
        WHERE exit_price IS NULL
        ORDER BY timestamp ASC
    """).fetchall()
    
    if not rows:
        print("No open positions to reconcile.")
        return
    
    now = time.time()
    total = len(rows)
    print(f"Found {total} open positions to reconcile")
    print(f"Max hold threshold: {MAX_HOLD_HOURS}h")
    print(f"Dry run: {'YES' if DRY_RUN else 'NO'}")
    print()
    
    updated = 0
    skipped = 0
    api_errors = 0
    
    for i, row in enumerate(rows):
        trade_id, condition_id, instrument_id, whale_name, market_title, \
            side, entry_price, size_usd, timestamp_str, category = row
        
        # Parse timestamp
        try:
            if "+" in str(timestamp_str):
                ts = datetime.fromisoformat(str(timestamp_str)).timestamp()
            else:
                ts = datetime.strptime(str(timestamp_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            ts = now
        
        age_hours = (now - ts) / 3600
        
        token_id = extract_token_id(instrument_id) if instrument_id else ""
        cond_id = condition_id or extract_condition_id(instrument_id) if instrument_id else ""
        
        print(f"[{i+1}/{total}] {whale_name} | {market_title[:40]} | "
              f"age={age_hours:.1f}h | entry=${entry_price:.4f} | "
              f"size=${size_usd:.2f} | {side}")
        
        exit_price = None
        exit_reason = None
        realized_pnl = None
        realized_return = None
        
        if age_hours > MAX_HOLD_HOURS:
            # Stale position — try to get resolution or midpoint
            if cond_id:
                res = fetch_resolution(cond_id)
                if res and not res.get("error"):
                    if res["resolved"]:
                        winning_token = res["winning_token_id"]
                        our_won = (token_id == winning_token) if side == "BUY" else (token_id != winning_token)
                        exit_price = 1.0 if our_won else 0.0
                        exit_reason = "reconciled_resolved"
                        realized_return = (exit_price - entry_price) / entry_price if side == "BUY" else (entry_price - exit_price) / entry_price
                        realized_pnl = size_usd * realized_return
                        print(f"  → RESOLVED: winner={'YES' if our_won else 'NO'}, PnL=${realized_pnl:+.2f}")
                    elif res.get("closed"):
                        # Closed but not resolved — use current midpoint
                        mid = fetch_midpoint(token_id) if token_id else None
                        if mid:
                            exit_price = mid
                            exit_reason = "reconciled_closed"
                            realized_return = (exit_price - entry_price) / entry_price if side == "BUY" else (entry_price - exit_price) / entry_price
                            realized_pnl = size_usd * realized_return
                            print(f"  → CLOSED: mid=${mid:.4f}, PnL=${realized_pnl:+.2f}")
                        else:
                            print(f"  → CLOSED but no midpoint, using entry price (no loss/gain)")
                            exit_price = entry_price
                            exit_reason = "reconciled_closed_no_price"
                            realized_pnl = 0.0
                            realized_return = 0.0
                    else:
                        # Still active but stale — use midpoint
                        mid = fetch_midpoint(token_id) if token_id else None
                        if mid:
                            exit_price = mid
                            exit_reason = "reconciled_stale_max_hold"
                            realized_return = (exit_price - entry_price) / entry_price if side == "BUY" else (entry_price - exit_price) / entry_price
                            realized_pnl = size_usd * realized_return
                            print(f"  → STALE (mid=${mid:.4f}): PnL=${realized_pnl:+.2f}")
                        else:
                            print(f"  → STALE but no API data, marking as stale_no_data")
                            exit_price = entry_price
                            exit_reason = "reconciled_stale_no_data"
                            realized_pnl = 0.0
                            realized_return = 0.0
                else:
                    api_errors += 1
                    print(f"  → API error: {res.get('error', 'unknown') if res else 'no response'}")
                    # Still mark as stale
                    exit_price = entry_price
                    exit_reason = "reconciled_api_error"
                    realized_pnl = 0.0
                    realized_return = 0.0
            else:
                print(f"  → No condition_id, marking as stale_no_cond")
                exit_price = entry_price
                exit_reason = "reconciled_stale_no_cond"
                realized_pnl = 0.0
                realized_return = 0.0
            
            # Update DB
            if exit_price is not None and not DRY_RUN:
                duration = now - ts
                conn.execute("""
                    UPDATE trades SET
                        exit_price = ?,
                        realized_pnl = ?,
                        realized_return = ?,
                        exit_reason = ?,
                        duration_seconds = ?
                    WHERE trade_id = ?
                """, (exit_price, realized_pnl, realized_return, exit_reason, duration, trade_id))
                conn.commit()
                updated += 1
            elif DRY_RUN:
                updated += 1  # Count for reporting
        else:
            skipped += 1
            print(f"  → Still valid (age={age_hours:.1f}h < {MAX_HOLD_HOURS}h), skipping")
        
        # Rate limit
        if i < total - 1:
            time.sleep(0.5)
    
    conn.close()
    
    print()
    print("=" * 60)
    print(f"Reconciliation complete:")
    print(f"  Total positions scanned: {total}")
    print(f"  Updated (exited):        {updated}")
    print(f"  Skipped (still valid):   {skipped}")
    print(f"  API errors:              {api_errors}")
    if DRY_RUN:
        print(f"\n  *** DRY RUN - No changes made to database ***")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile stuck positions in trades.db")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying database")
    args = parser.parse_args()
    DRY_RUN = args.dry_run
    reconcile()
