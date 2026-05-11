#!/usr/bin/env python3
"""Sync whale_intelligence DB from actual trading results.

Updates: win_rate, total_trades, pnl, should_fade, trust_score, classification.
Source: trades.db (actual paper trading results).
Target: whale_discovery.db (whale_intelligence table).

Run: python scripts/sync_whale_intelligence.py
Schedule: After each grading run or daily.
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRADES_DB = PROJECT_ROOT / "research" / "trades.db"
INTEL_DB = PROJECT_ROOT / "pipeline" / "data" / "whale_discovery.db"

# Thresholds for re-classification
FADE_WR_THRESHOLD = 0.35  # Win rate below this → fade
FADE_MIN_TRADES = 5       # Minimum trades before fade decision
TRUST_WR_MAP = {
    # win_rate_range -> trust_score
    (0.80, 1.00): 9,
    (0.60, 0.80): 7,
    (0.45, 0.60): 5,
    (0.35, 0.45): 3,
    (0.00, 0.35): 1,
}
CLASSIFICATION_MAP = {
    # (win_rate, volume, pattern) -> classification
    "winning_whale": "skilled_human",
    "losing_whale": "degenerate_human",
    "neutral": "mixed_entity",
}


def get_actual_stats(trades_db: Path) -> dict:
    """Calculate actual trading stats for each whale from trades.db."""
    if not trades_db.exists():
        print(f"ERROR: trades.db not found at {trades_db}")
        return {}
    
    conn = sqlite3.connect(trades_db)
    query = """
        SELECT 
            whale_name,
            COUNT(*) as total_trades,
            SUM(realized_pnl) as total_pnl,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(realized_pnl) / COUNT(*) as avg_pnl,
            MIN(entry_price) as min_entry,
            MAX(entry_price) as max_entry
        FROM trades
        WHERE whale_name IS NOT NULL
        GROUP BY whale_name
    """
    rows = conn.execute(query).fetchall()
    conn.close()
    
    stats = {}
    for row in rows:
        name, trades, pnl, wins, avg_pnl, min_entry, max_entry = row
        win_rate = wins / trades if trades > 0 else 0.0
        stats[name] = {
            "total_trades": trades,
            "total_pnl": pnl or 0.0,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl or 0.0,
            "wins": wins,
            "losses": trades - wins,
        }
    
    return stats


def calculate_trust_score(win_rate: float, trades: int) -> int:
    """Assign trust score based on win rate and sample size."""
    if trades < 3:
        return 3  # Insufficient data → neutral
    
    for (min_wr, max_wr), score in TRUST_WR_MAP.items():
        if min_wr <= win_rate < max_wr:
            # Boost score if high sample size
            if trades >= 20:
                return min(score + 1, 10)
            return score
    
    return 3  # Default


def determine_classification(win_rate: float, pnl: float, trades: int) -> str:
    """Re-classify whale based on actual performance."""
    if trades < 3:
        return "mixed_entity"  # Unknown
    
    if win_rate >= 0.55 and pnl > 0:
        return "skilled_human"
    elif win_rate <= 0.30 and pnl < 0:
        return "degenerate_human"
    elif win_rate >= 0.40 and pnl >= 0:
        return "trading_bot"  # Consistent but not exceptional
    else:
        return "mixed_entity"


def should_fade_decision(win_rate: float, trades: int, pnl: float) -> int:
    """0 = follow, 1 = fade. Based on actual results."""
    if trades < FADE_MIN_TRADES:
        return 0  # Not enough data
    
    if win_rate < FADE_WR_THRESHOLD and pnl < 0:
        return 1  # Fade losing whales
    
    return 0


def update_intelligence_db(intel_db: Path, stats: dict) -> int:
    """Update whale_intelligence table with actual stats."""
    if not intel_db.exists():
        print(f"ERROR: whale_discovery.db not found at {intel_db}")
        return 0
    
    conn = sqlite3.connect(intel_db)
    cursor = conn.cursor()
    
    updated = 0
    for name, data in stats.items():
        win_rate = data["win_rate"]
        trades = data["total_trades"]
        pnl = data["total_pnl"]
        
        trust = calculate_trust_score(win_rate, trades)
        classification = determine_classification(win_rate, pnl, trades)
        fade = should_fade_decision(win_rate, trades, pnl)
        
        # Check if whale exists in intel DB
        cursor.execute("SELECT name FROM whale_intelligence WHERE name = ?", (name,))
        exists = cursor.fetchone()
        
        if exists:
            # Update existing entry
            cursor.execute("""
                UPDATE whale_intelligence SET
                    win_rate = ?,
                    total_trades = ?,
                    pnl = ?,
                    trust_score = ?,
                    classification = ?,
                    should_fade = ?,
                    reasoning = ?
                WHERE name = ?
            """, (
                win_rate,
                trades,
                pnl,
                trust,
                classification,
                fade,
                f"Synced from trades.db: WR={win_rate:.1%}, {trades} trades, PnL=${pnl:.0f}",
                name,
            ))
            updated += 1
        else:
            # Insert new entry (whale traded but not in intel DB)
            cursor.execute("""
                INSERT INTO whale_intelligence (
                    name, address, win_rate, total_trades, pnl,
                    trust_score, classification, should_fade, should_copy, reasoning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name,
                name,  # Use name as address if unknown
                win_rate,
                trades,
                pnl,
                trust,
                classification,
                fade,
                0,  # Don't copy by default
                f"Created from trades.db sync: WR={win_rate:.1%}, {trades} trades",
            ))
            updated += 1
    
    conn.commit()
    conn.close()
    
    return updated


def main():
    print("=== Whale Intelligence Sync ===")
    print(f"Source: {TRADES_DB}")
    print(f"Target: {INTEL_DB}")
    
    # Step 1: Get actual stats
    stats = get_actual_stats(TRADES_DB)
    if not stats:
        print("No stats found. Exiting.")
        sys.exit(1)
    
    print(f"\nWhales with trading data: {len(stats)}")
    
    # Show top losers (candidates for fade)
    losers = [(n, d) for n, d in stats.items() 
              if d["total_trades"] >= FADE_MIN_TRADES and d["win_rate"] < FADE_WR_THRESHOLD]
    losers.sort(key=lambda x: x[1]["total_pnl"])
    
    print(f"\nWhales to FADE (WR < {FADE_WR_THRESHOLD:.0%}, {FADE_MIN_TRADES}+ trades): {len(losers)}")
    for name, data in losers[:10]:
        print(f"  {name}: WR={data['win_rate']:.1%}, PnL=${data['total_pnl']:.0f}, {data['total_trades']} trades")
    
    # Step 2: Update intel DB
    updated = update_intelligence_db(INTEL_DB, stats)
    print(f"\nUpdated {updated} entries in whale_intelligence")
    
    # Verify
    conn = sqlite3.connect(INTEL_DB)
    fade_count = conn.execute("SELECT COUNT(*) FROM whale_intelligence WHERE should_fade = 1").fetchone()[0]
    conn.close()
    
    print(f"\nTotal whales marked for FADE: {fade_count}")
    print("=== Sync Complete ===")


if __name__ == "__main__":
    main()