#!/usr/bin/env python3
"""Migrate trades.db to add actual_pnl/actual_return columns.
Restores simulated P&L from backup, stores actual resolution P&L separately.
"""
import sqlite3
from pathlib import Path

TRADES_DB = Path(__file__).parents[1] / "research" / "trades.db"
BACKUP_DB = Path(__file__).parents[1] / "research" / "trade_db" / "backup-20260504-1323.db"

def migrate():
    print(f"Source: {TRADES_DB}")
    print(f"Backup: {BACKUP_DB}")
    
    if not TRADES_DB.exists():
        print("[ERROR] trades.db not found")
        return False
    if not BACKUP_DB.exists():
        print("[ERROR] backup database not found")
        return False
    
    conn = sqlite3.connect(str(TRADES_DB))
    cur = conn.cursor()
    
    # Step 1: Check if migration already done
    cur.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cur.fetchall()}
    
    if "actual_pnl" in columns:
        print("[OK] Migration already applied (actual_pnl column exists)")
    else:
        print("Adding actual_pnl and actual_return columns...")
        cur.execute("ALTER TABLE trades ADD COLUMN actual_pnl REAL")
        cur.execute("ALTER TABLE trades ADD COLUMN actual_return REAL")
        conn.commit()
        print("  Columns added.")
    
    # Step 2: Count resolved trades
    cur.execute("SELECT COUNT(*) FROM trades WHERE resolution_outcome IS NOT NULL")
    resolved_count = cur.fetchone()[0]
    print(f"  Resolved trades: {resolved_count}")
    
    # Step 3: For each resolved trade, copy current realized_pnl → actual_pnl
    # and restore simulated P&L from backup
    cur.execute("""
        SELECT trade_id, realized_pnl, realized_return 
        FROM trades 
        WHERE resolution_outcome IS NOT NULL AND actual_pnl IS NULL
    """)
    to_migrate = cur.fetchall()
    
    if not to_migrate:
        cur.execute("SELECT COUNT(*) FROM trades WHERE actual_pnl IS NOT NULL")
        already = cur.fetchone()[0]
        print(f"[OK] All resolved trades already have actual_pnl set ({already} trades)")
    else:
        # Copy current values to actual_pnl/actual_return
        backup_conn = sqlite3.connect(str(BACKUP_DB))
        backup_cur = backup_conn.cursor()
        
        migrated = 0
        not_found = 0
        for trade_id, cur_pnl, cur_return in to_migrate:
            # Set actual_pnl from current realized_pnl
            cur.execute("UPDATE trades SET actual_pnl = ?, actual_return = ? WHERE trade_id = ?",
                       (cur_pnl, cur_return, trade_id))
            
            # Restore simulated P&L from backup
            backup_cur.execute(
                "SELECT realized_pnl, realized_return FROM trades WHERE trade_id = ?",
                (trade_id,))
            row = backup_cur.fetchone()
            if row:
                sim_pnl, sim_return = row
                cur.execute("UPDATE trades SET realized_pnl = ?, realized_return = ? WHERE trade_id = ?",
                           (sim_pnl, sim_return, trade_id))
                migrated += 1
            else:
                not_found += 1
        
        backup_conn.close()
        conn.commit()
        print(f"  Migrated: {migrated} trades restored from backup")
        if not_found:
            print(f"  WARNING: {not_found} trades not found in backup")
    
    # Step 4: Clean up resolution_outcome to just WON/LOST
    cur.execute("""
        UPDATE trades 
        SET resolution_outcome = 'WON'
        WHERE resolution_outcome IS NOT NULL 
          AND resolution_outcome LIKE 'WIN%'
          AND resolution_outcome NOT IN ('WON', 'LOST')
    """)
    cur.execute("""
        UPDATE trades 
        SET resolution_outcome = 'LOST'
        WHERE resolution_outcome IS NOT NULL 
          AND resolution_outcome LIKE 'LOSS%'
          AND resolution_outcome NOT IN ('WON', 'LOST')
    """)
    conn.commit()
    
    # Step 5: Summary
    cur.execute("SELECT COUNT(*) FROM trades WHERE resolution_outcome IS NOT NULL")
    resolved = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM trades WHERE actual_pnl IS NOT NULL")
    with_actual = cur.fetchone()[0]
    cur.execute("""
        SELECT 
            ROUND(SUM(actual_pnl), 2),
            ROUND(AVG(actual_pnl), 2),
            ROUND(SUM(realized_pnl), 2),
            ROUND(AVG(realized_pnl), 2)
        FROM trades WHERE actual_pnl IS NOT NULL
    """)
    actual_sum, actual_avg, sim_sum, sim_avg = cur.fetchone() or (0, 0, 0, 0)
    
    print(f"""
=== Migration Summary ===
Resolved trades:    {resolved}
Actual P&L set:     {with_actual}
Simulated P&L total: ${sim_sum:+,.2f}
Actual P&L total:    ${actual_sum:+,.2f}
Divergence:          ${actual_sum - sim_sum:+,.2f}
Avg simulated/trade: ${sim_avg:+,.2f}
Avg actual/trade:    ${actual_avg:+,.2f}
""")
    
    cur.execute("SELECT DISTINCT resolution_outcome FROM trades WHERE resolution_outcome IS NOT NULL")
    outcomes = [r[0] for r in cur.fetchall()]
    print(f"Resolution outcomes: {sorted(outcomes)}")
    
    conn.close()
    return True

if __name__ == "__main__":
    migrate()
