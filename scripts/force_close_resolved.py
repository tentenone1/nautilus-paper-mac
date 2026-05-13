#!/usr/bin/env python3
"""Force-close all positions that are marked resolved but have no exit_price.

The resolution poller sets exit_reason=resolved/market_resolved but sometimes
the exit_price is never written. This script queries the Polymarket CLOB API
for each condition_id and force-closes with the correct exit price.
"""
import sys
sys.path.insert(0, '/Users/tentenone/workspace/nautilus-trading')

import sqlite3
import urllib.request
import json
import time
from datetime import datetime, timezone

DB_PATH = '/Users/tentenone/workspace/nautilus-trading/research/trades.db'

def get_market_info(condition_id: str) -> dict:
    """Fetch market info from Polymarket CLOB API."""
    try:
        url = f"https://clob.polymarket.com/markets/{condition_id}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

def get_resolution_for_condition(condition_id: str) -> tuple[bool, float, str]:
    """Get resolution outcome for a market.
    
    Returns (resolved, exit_price, winning_outcome)
    - resolved: bool
    - exit_price: 1.0 for YES winner, 0.0 for YES loser
    - winning_outcome: description of what won
    """
    market = get_market_info(condition_id)
    
    if "error" in market:
        return False, 0.0, market["error"]
    
    # Check if market is resolved
    try:
        # Polymarket stores resolution as array of winning outcome indices
        winner = market.get("winner", None)
        prices = market.get("outcomePrices", {})
        
        if winner is None:
            return False, 0.0, "no_winner"
        
        # winner is typically "0" (first outcome) or "1" (second) or the outcome name
        # outcomePrices maps outcome -> price
        # Resolved means the price is 1.0 (100%) for winner
        
        # Find the price for the winning outcome
        outcomes = market.get("outcomes", ["YES", "NO"])
        
        # Handle different winner formats
        if isinstance(winner, int) and winner < len(outcomes):
            winning_outcome = outcomes[winner]
        elif str(winner) in outcomes:
            winning_outcome = str(winner)
        else:
            winning_outcome = str(winner)
        
        # Get price of winning outcome
        winning_price = 1.0  # Resolved market, winner pays 1.0
        
        # Check outcomePrices for the YES outcome
        # YES outcome wins if the YES side resolved to 1.0
        yes_price = None
        for outcome, price in prices.items():
            if 'YES' in outcome.upper() or outcome in ['1', 'Yes']:
                yes_price = float(price)
                break
        
        if yes_price is not None:
            resolved = yes_price in (1.0, 0.0)
            if yes_price == 1.0:
                # YES won
                return True, 1.0, f"YES won ({winning_outcome})"
            else:
                # YES lost
                return True, 0.0, f"YES lost ({winning_outcome})"
        
        return True, winning_price, winning_outcome
        
    except Exception as e:
        return False, 0.0, str(e)

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Get all open positions with exit_reason set but no exit_price
    cur.execute("""
        SELECT trade_id, condition_id, side, entry_price, exit_reason,
               position_size_usd, market_title, timestamp
        FROM trades
        WHERE (exit_price IS NULL OR exit_price = 0.0)
          AND exit_reason IN ('resolved', 'market_resolved')
        ORDER BY timestamp ASC
    """)
    
    rows = cur.fetchall()
    print(f"Found {len(rows)} resolved-but-open positions")
    print()
    
    if not rows:
        print("No stuck positions to fix.")
        conn.close()
        return
    
    closed = 0
    skipped = 0
    errors = 0
    total_pnl = 0.0
    
    for row in rows:
        trade_id, condition_id, side, entry_price, exit_reason, size_usd, title, ts = row
        
        if condition_id:
            resolved, exit_price, outcome_desc = get_resolution_for_condition(condition_id)
        else:
            resolved, exit_price = False, 0.0
            outcome_desc = "no_condition_id"
        
        print(f"[{ts}] {title[:60]}")
        print(f"  trade_id={trade_id[:8]}...")
        print(f"  side={side}, entry={entry_price}, reason={exit_reason}")
        
        if not resolved:
            # Market not actually resolved - clear the stale flag
            print(f"  [STALE FLAG] API says not resolved ({outcome_desc}) — clearing flag")
            cur.execute("""
                UPDATE trades SET
                    exit_reason = NULL,
                    notes = COALESCE(notes, '') || ' | stale_exit_reason_cleared'
                WHERE trade_id = ?
            """, (trade_id,))
            skipped += 1
        else:
            # Market IS resolved - force close with correct exit_price
            print(f"  [RESOLVING] exit_price={exit_price} ({outcome_desc})")
            
            size = size_usd or 1.0
            
            # Calculate P&L
            if side == 'BUY':
                pnl = (exit_price - entry_price) * size / entry_price
                ret = (exit_price - entry_price) / entry_price
            else:
                pnl = (entry_price - exit_price) * size / entry_price
                ret = (entry_price - exit_price) / entry_price
            
            # Cap return to ±200%
            if ret > 2.0:
                ret = 2.0
                pnl = entry_price * 2.0 * size / entry_price
            elif ret < -2.0:
                ret = -2.0
                pnl = entry_price * (-2.0) * size / entry_price
            
            result = 'WIN' if pnl >= 0 else 'LOSS'
            print(f"  → exit={exit_price}, pnl={pnl:+.4f}, return={ret:+.4f}, result={result}")
            
            try:
                cur.execute("""
                    UPDATE trades SET
                        exit_price = ?,
                        realized_pnl = ?,
                        realized_return = ?,
                        resolution_outcome = ? || ' | force_closed'
                    WHERE trade_id = ?
                """, (exit_price, pnl, ret, result, trade_id))
                closed += 1
                total_pnl += pnl
            except Exception as e:
                print(f"  [ERROR] {e}")
                errors += 1
        
        print()
        time.sleep(0.3)  # Rate limit
    
    conn.commit()
    
    # Final state
    cur.execute("SELECT COUNT(*) FROM trades WHERE exit_price IS NULL OR exit_price = 0.0")
    remaining = cur.fetchone()[0]
    
    print(f"=== Summary ===")
    print(f"Force-closed: {closed}")
    print(f"Stale flags cleared: {skipped}")
    print(f"Errors: {errors}")
    print(f"Total P&L from closures: {total_pnl:+.4f}")
    print(f"Remaining open positions: {remaining}")
    
    conn.close()

if __name__ == '__main__':
    main()
