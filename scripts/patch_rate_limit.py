#!/usr/bin/env python3
"""Patch run_paper.py to add rate limiting for Polymarket API calls."""

import sys

# Read the file
with open('run_paper.py', 'r') as f:
    lines = f.readlines()

# Find the function start (line ~85)
start_idx = None
for i, line in enumerate(lines):
    if 'def load_whale_markets_from_api(limit: int = 20)' in line:
        start_idx = i
        break

if start_idx is None:
    print('ERROR: Could not find function')
    sys.exit(1)

# Find the function end (next function or print statement)
end_idx = None
for i in range(start_idx + 1, len(lines)):
    if lines[i].startswith('print("Scanning current whale') or lines[i].startswith('def '):
        end_idx = i
        break

print(f'Found function at lines {start_idx+1} to {end_idx+1}')

# New function with rate limiting
new_func = '''def load_whale_markets_from_api(limit: int = 20) -> list[dict]:
    """Fetch markets whales are actively holding positions in — live data, not stale DB.
    
    Rate limit: Polymarket allows ~100 requests/min for unauthenticated.
    We use 0.7s between calls = ~85 requests/min (safe margin).
    Retry logic: 3 retries with exponential backoff on empty responses.
    """
    import time
    
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "pipeline", "data", "whale_discovery.db"
    )
    addresses = []
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT address FROM whales WHERE alpha_score >= 70 ORDER BY alpha_score DESC LIMIT 15"
        ).fetchall()
        conn.close()
        addresses = [r[0] for r in rows]

    if not addresses:
        print("No whale addresses in DB, using fallback")
        return []

    import subprocess, json as _json
    market_conds = {}
    failed_count = 0
    
    for i, addr in enumerate(addresses):
        # Rate limit: 0.7s between requests
        if i > 0:
            time.sleep(0.7)
        
        success = False
        for retry in range(3):
            try:
                result = subprocess.run(
                    ["curl", "-s", "-m", "15",
                     f"https://data-api.polymarket.com/positions?user={addr}&limit=50"],
                    capture_output=True, text=True, timeout=20
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    positions = _json.loads(result.stdout)
                    if positions:
                        for pos in positions:
                            cond = pos.get("conditionId", "")
                            if not cond:
                                continue
                            if cond not in market_conds:
                                market_conds[cond] = {
                                    "condition_id": cond,
                                    "title": pos.get("title", ""),
                                    "whale_count": 0,
                                }
                            market_conds[cond]["whale_count"] += 1
                        print(f"  OK [{i+1}/{len(addresses)}]: {addr[:12]}... ({len(positions)} pos)")
                        success = True
                        break
                    else:
                        print(f"  EMPTY [{i+1}/{len(addresses)}]: {addr[:12]}...")
                        success = True
                        break
                else:
                    if retry < 2:
                        wait = 2 ** retry
                        print(f"  RETRY {retry+1}: {addr[:12]}... (wait {wait}s)")
                        time.sleep(wait)
                    else:
                        print(f"  SKIP [{i+1}/{len(addresses)}]: {addr[:12]}... (rate limited)")
                        failed_count += 1
                        
            except subprocess.TimeoutExpired:
                if retry < 2:
                    wait = 2 ** retry
                    print(f"  TIMEOUT retry {retry+1}: {addr[:12]}...")
                    time.sleep(wait)
                else:
                    print(f"  TIMEOUT [{i+1}/{len(addresses)}]: {addr[:12]}...")
                    failed_count += 1
            except Exception as e:
                print(f"  ERROR [{i+1}/{len(addresses)}]: {addr[:12]}...: {e}")
                failed_count += 1
                break
    
    if failed_count > 0:
        print(f"  Failed: {failed_count}/{len(addresses)} addresses")

'''

# Replace the function
new_lines = lines[:start_idx] + [new_func] + lines[end_idx:]

# Write back
with open('run_paper.py', 'w') as f:
    f.writelines(new_lines)

print('PATCH SUCCESS: Rate limiting added')
print(f'  - 0.7s delay between API calls')
print(f'  - 3 retries with exponential backoff')
print(f'  - Progress logging per address')