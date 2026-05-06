#!/usr/bin/env python3
"""Generate actionable trading strategies using uncensored reasoning model."""
import json
import sqlite3
import requests
import re
from datetime import datetime

API_URL = "http://localhost:8080/v1/chat/completions"
MODEL = "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf"

# Get whale data with actual PnL
conn = sqlite3.connect("research/trades.db")
whales = conn.execute("""
    SELECT whale_name, COUNT(*) as trades, 
           AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100 as win_rate_pct,
           SUM(position_size_usd) as volume,
           AVG(edge_score) as avg_edge,
           SUM(realized_pnl) as total_pnl
    FROM trades WHERE whale_name IS NOT NULL AND whale_name != "unknown_unnamed"
    GROUP BY whale_name HAVING trades >= 10 ORDER BY total_pnl DESC LIMIT 20
""").fetchall()

whale_data = [
    {
        "name": w[0], 
        "trades": w[1], 
        "win_rate": round(w[2], 1),
        "volume_usd": int(w[3]),
        "edge": round(w[4], 2) if w[4] else 0,
        "pnl": int(w[5]) if w[5] else 0
    } 
    for w in whales
]

prompt = """Analyze Polymarket whale trading patterns. Find exploitable edges.

TOP WHALES BY PNL:
""" + json.dumps(whale_data[:10], indent=2) + """

BOTTOM WHALES (LOSERS):
""" + json.dumps(whale_data[-5:], indent=2) + """

TASK: Generate 5 actionable trading strategies. Focus on:
1. Which whales to FOLLOW (high win rate, consistent timing)
2. Which whales to FADE (bet against their direction)
3. Timing patterns (when do winners enter?)
4. Market category edges (sports vs crypto vs politics)
5. Size-based signals (large bets vs small bets reliability)

OUTPUT ONLY a JSON object with this structure:
{
  "strategies": [
    {"name": "...", "action": "...", "target": "...", "confidence": 0.X}
  ],
  "follow": ["whale1", "whale2"],
  "fade": ["whale3", "whale4"],
  "edge_signals": ["signal1", "signal2"]
}

No reasoning in output. Just the JSON."""

try:
    r = requests.post(API_URL, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 5000  # Reasoning model needs lots of tokens
    }, timeout=300)
    
    result = r.json()
    msg = result["choices"][0]["message"]
    
    # Try content first, then reasoning_content
    raw = msg.get("content") or msg.get("reasoning_content") or ""
    
    # Extract JSON from response
    json_match = re.search(r"\{[\s\S]*\"strategies\"[\s\S]*\}", raw)
    if json_match:
        try:
            strategies = json.loads(json_match.group())
        except:
            strategies = {"parse_error": "Could not parse JSON", "raw": raw[:500]}
    else:
        strategies = {"no_json": True, "raw": raw[:1000]}
    
    output = {
        "generated": datetime.now().isoformat(),
        "whales_analyzed": len(whales),
        "strategies": strategies,
        "model": MODEL,
        "tokens_used": result.get("usage", {}).get("total_tokens", 0)
    }
    
    with open("research/jailbreak_strategies.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print("=== STRATEGIES GENERATED ===")
    print(json.dumps(strategies, indent=2)[:1500])
    
except Exception as e:
    print(f"ERROR: {e}")
